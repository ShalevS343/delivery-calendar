"""One run: fetch, triage, extract, store, sync. No model, no carrier APIs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from delivery_calendar.calendar_client import CalendarPort
from delivery_calendar.config import Settings
from delivery_calendar.extract import extract
from delivery_calendar.mailparse import ParsedMail, parse_message
from delivery_calendar.models import ARRIVAL_STATUSES
from delivery_calendar.prefilter import classify
from delivery_calendar.rules import Rules
from delivery_calendar.store import Store
from delivery_calendar.sync import sync_shipment

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    fetched: int = 0
    stored: int = 0
    promos: int = 0
    shipments_created: int = 0
    shipments_updated: int = 0
    review: int = 0
    created_events: int = 0
    patched_events: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "stored": self.stored,
            "promos": self.promos,
            "shipments_created": self.shipments_created,
            "shipments_updated": self.shipments_updated,
            "review": self.review,
            "created_events": self.created_events,
            "patched_events": self.patched_events,
            "errors": list(self.errors),
        }


def run_once(
    settings: Settings,
    store: Store,
    rules: Rules,
    source,
    calendar: CalendarPort,
    *,
    now: datetime | None = None,
    parser: Callable[[bytes], ParsedMail] = parse_message,
) -> RunReport:
    now = now or datetime.now(timezone.utc)
    report = RunReport()
    try:
        raw_mails = source.fetch_new(limit=settings.max_mails_per_run)
    except Exception as exc:  # noqa: BLE001 - a dead mailbox must be reported, not crash
        report.errors.append(f"fetch: {exc}")
        return report

    report.fetched = len(raw_mails)
    for raw_mail in raw_mails:
        try:
            _process(settings, store, rules, calendar, raw_mail.raw, now, parser, report)
        except Exception as exc:  # noqa: BLE001 - one bad mail cannot stop the run
            report.errors.append(f"uid {getattr(raw_mail, 'uid', '?')}: {exc}")
            log.exception("mail failed: uid=%s", getattr(raw_mail, "uid", "?"))
    return report


def _process(
    settings: Settings,
    store: Store,
    rules: Rules,
    calendar: CalendarPort,
    raw: bytes,
    now: datetime,
    parser: Callable[[bytes], ParsedMail],
    report: RunReport,
) -> None:
    mail = parser(raw)
    if store.message_seen(mail.gm_msgid):
        return

    verdict = classify(mail, rules)
    raw_path = _store_raw(settings, mail)
    inserted = store.insert_message(
        mail.gm_msgid,
        mail.message_id,
        mail.from_addr,
        mail.subject,
        mail.date,
        verdict=verdict.kind,
        verdict_rule=verdict.rule,
        snippet=mail.text[:280],
        raw_path=raw_path,
        body_text=mail.text,
    )
    if not inserted:
        return
    report.stored += 1

    if verdict.kind != "delivery":
        report.promos += 1
        return

    candidates = extract(mail, rules, min_confidence=settings.review_min_confidence)
    for candidate in candidates:
        shipment = candidate.shipment
        result = store.upsert_shipment(shipment, now=now)
        store.link_message(mail.gm_msgid, result.shipment_id, role="update")
        if result.created:
            report.shipments_created += 1
        else:
            report.shipments_updated += 1
        if result.shipment.needs_review:
            report.review += 1

        outcome = sync_shipment(
            store, calendar, result.shipment, dry_run=settings.dry_run
        )
        if outcome.action == "create":
            report.created_events += 1
        elif outcome.action == "patch":
            report.patched_events += 1


def _store_raw(settings: Settings, mail: ParsedMail) -> str | None:
    if not mail.raw:
        return None
    day = mail.date.date()
    directory = Path(settings.raw_dir) / f"{day:%Y}" / f"{day:%m}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{mail.gm_msgid}.eml"
        if not path.exists():
            path.write_bytes(mail.raw)
        return str(path)
    except OSError:
        return None
