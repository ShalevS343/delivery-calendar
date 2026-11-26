"""Command line entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from delivery_calendar.calendar_client import FakeCalendar, GoogleCalendar
from delivery_calendar.config import ConfigError, Settings
from delivery_calendar.events import build_event
from delivery_calendar.imap_source import ImapSource
from delivery_calendar.mailparse import parse_message
from delivery_calendar.models import Shipment
from delivery_calendar.rules import RuleCache
from delivery_calendar.runner import Runner
from delivery_calendar.service import run_once
from delivery_calendar.store import Store
from delivery_calendar.sync import sync_shipment
from delivery_calendar.web import create_app

log = logging.getLogger("delivery_calendar")


def _bootstrap(settings: Settings) -> tuple[Store, RuleCache]:
    store = Store(settings.db_path)
    store.migrate()
    rules = RuleCache(settings.rules_dir)
    errors = rules.reload()
    for error in errors:
        log.error("rules: %s", error)
    return store, rules


def _calendar(settings: Settings, *, force_dry: bool):
    if settings.dry_run or force_dry:
        return GoogleCalendar(settings) if not settings.dry_run else _DryCalendar(settings)
    return GoogleCalendar(settings)


class _DryCalendar(FakeCalendar):
    """Dry run: build and fingerprint events, never touch the network."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.calendar_id = settings.calendar_id


def cmd_check(settings: Settings, _args) -> int:
    problems = settings.validate()
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print("ok")
    print(json.dumps({k: v for k, v in settings.__dict__.items() if "password" not in k}, default=str, indent=2))
    return 0


def cmd_run(settings: Settings, _args) -> int:
    store, rules = _bootstrap(settings)
    report = run_once(
        settings,
        store,
        rules.rules,
        ImapSource(settings, store),
        _calendar(settings, force_dry=False),
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 1 if report.errors else 0


def cmd_fetch(settings: Settings, args) -> int:
    store, _ = _bootstrap(settings)
    source = ImapSource(settings, store)
    mails = source.fetch_new(limit=args.limit)
    for raw in mails:
        mail = parse_message(raw.raw)
        print(f"{raw.uid}\t{mail.date.date()}\t{mail.from_addr}\t{mail.subject}")
    print(f"-- {len(mails)} message(s)")
    return 0


def cmd_events(settings: Settings, args) -> int:
    store, _ = _bootstrap(settings)
    shipments = store.list_shipments(limit=200, include_suppressed=False)
    printed = 0
    for shipment_id, shipment in shipments:
        if args.shipment and shipment_id != args.shipment:
            continue
        if shipment.arrival_date is None:
            continue
        print(json.dumps(build_event(shipment), indent=2, ensure_ascii=False))
        printed += 1
    print(f"-- {printed} arrival event(s)")
    return 0


def cmd_reparse(settings: Settings, args) -> int:
    store, rules = _bootstrap(settings)
    calendar = _calendar(settings, force_dry=settings.dry_run)
    limit = args.limit
    rows = store.conn.execute(
        "SELECT gm_msgid, raw_path FROM messages WHERE verdict = 'delivery' "
        "ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    processed = 0
    for row in rows:
        path = row["raw_path"]
        if not path or not Path(path).is_file():
            continue
        raw = Path(path).read_bytes()
        mail = parse_message(raw)
        from delivery_calendar.extract import extract

        store.conn.execute("DELETE FROM messages WHERE gm_msgid = ?", (mail.gm_msgid,))
        report = type("R", (), {"errors": []})()
        for candidate in extract(mail, rules.rules, settings.review_min_confidence):
            result = store.upsert_shipment(candidate.shipment)
            store.link_message(mail.gm_msgid, result.shipment_id)
            sync_shipment(store, calendar, result.shipment, dry_run=settings.dry_run)
        processed += 1
    print(json.dumps({"reparsed": processed}, indent=2))
    return 0


def cmd_reload(settings: Settings, _args) -> int:
    rules = RuleCache(settings.rules_dir)
    errors = rules.reload()
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print(
        json.dumps(
            {
                "families": [f.id for f in rules.rules.tracking.families],
                "senders": len(rules.rules.carriers.senders),
                "promo_domains": len(rules.rules.promos.domains),
            },
            indent=2,
        )
    )
    return 0


def cmd_export_fixtures(settings: Settings, args) -> int:
    store, _ = _bootstrap(settings)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = store.conn.execute(
        "SELECT gm_msgid, raw_path FROM messages WHERE raw_path IS NOT NULL "
        "ORDER BY received_at DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    written = 0
    for row in rows:
        path = Path(row["raw_path"])
        if not path.is_file():
            continue
        target = out / f"{row['gm_msgid']}.eml"
        target.write_bytes(path.read_bytes())
        written += 1
    print(f"wrote {written} fixture(s) to {out}")
    if written and not args.redact:
        print("NOTE: real fixtures contain addresses; keep them out of git.")
    return 0


def cmd_serve(settings: Settings, _args) -> int:
    import uvicorn

    from delivery_calendar.scheduler import start_scheduler

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format='{"t":"%(asctime)s","lvl":"%(levelname)s","mod":"%(name)s","msg":%(message)r}',
    )
    problems = settings.validate()
    for problem in problems:
        log.error("config: %s", problem)
    if problems:
        return 1

    store, rules = _bootstrap(settings)
    runner = Runner(settings, store, rules)
    start_scheduler(settings, runner)
    app = create_app(settings, store, runner.calendar, runner=runner)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.ui_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delivery-calendar")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the scheduler and the review UI")

    sub.add_parser("check", help="validate configuration")
    sub.add_parser("run", help="run one poll now")
    sub.add_parser("reload", help="reload rules from the data volume")

    fetch = sub.add_parser("fetch", help="fetch and print recent mail headers")
    fetch.add_argument("--limit", type=int, default=5)

    events = sub.add_parser("events", help="print arrival event payloads")
    events.add_argument("--shipment", type=int, default=None)

    reparse = sub.add_parser("reparse", help="re-run extraction over stored mail")
    reparse.add_argument("--limit", type=int, default=50)

    export = sub.add_parser("export-fixtures", help="copy raw mail out for tests")
    export.add_argument("--limit", type=int, default=30)
    export.add_argument("--out", required=True)
    export.add_argument("--redact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.command in (None, "serve"):
        return cmd_serve(settings, args)
    handlers = {
        "check": cmd_check,
        "run": cmd_run,
        "fetch": cmd_fetch,
        "events": cmd_events,
        "reparse": cmd_reparse,
        "reload": cmd_reload,
        "export-fixtures": cmd_export_fixtures,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
