from datetime import datetime, timezone
from pathlib import Path

import pytest

from delivery_sync.calendar_client import FakeCalendar
from delivery_sync.config import Settings
from delivery_sync.imap_source import RawMail, plan_fetch
from delivery_sync.rules import load_rules
from delivery_sync.service import run_once
from delivery_sync.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSource:
    def __init__(self, raw_mails: list[bytes]):
        self._pending = list(raw_mails)
        self.calls = 0

    def fetch_new(self, limit: int = 50):
        self.calls += 1
        batch, self._pending = self._pending[:limit], self._pending[limit:]
        return [RawMail(uid=index + 1, raw=raw) for index, raw in enumerate(batch)]


def _settings(tmp_path, **overrides) -> Settings:
    env = {
        "DB_PATH": str(tmp_path / "db.sqlite"),
        "RAW_DIR": str(tmp_path / "raw"),
        "STATE_DIR": str(tmp_path),
        "RULES_DIR": str(tmp_path / "rules"),
        "DRY_RUN": "1",
        "CALENDAR_ID": "cal@example.com",
    }
    env.update(overrides)
    return Settings.from_env(env)


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_in_transit_mail_creates_shipment_but_no_event(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()
    report = run_once(
        _settings(tmp_path),
        store,
        load_rules(None, None),
        FakeSource([_raw("amazon_shipped.eml")]),
        calendar,
        now=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
    )
    assert report.fetched == 1
    assert report.stored == 1
    assert store.count_shipments() == 1
    assert calendar.calls == []
    assert report.created_events == 0


def test_pickup_mail_creates_one_event(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()
    settings = _settings(tmp_path, DRY_RUN="0")
    report = run_once(
        settings,
        store,
        load_rules(None, None),
        FakeSource([_raw("hfd_pickup.eml")]),
        calendar,
        now=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
    )
    assert report.created_events == 1
    assert len(calendar.events) == 1


def test_same_mail_twice_is_stored_once(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()
    settings = _settings(tmp_path)
    rules = load_rules(None, None)
    now = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)

    run_once(settings, store, rules, FakeSource([_raw("amazon_shipped.eml")]), calendar, now=now)
    report = run_once(
        settings, store, rules, FakeSource([_raw("amazon_shipped.eml")]), calendar, now=now
    )
    assert report.stored == 0
    assert store.count_shipments() == 1
    assert store.message_count() == 1


def test_one_bad_message_does_not_abort_run(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()

    def flaky_parse(raw: bytes):
        if b"POISON" in raw:
            raise ValueError("cannot parse this one")
        from delivery_sync.mailparse import parse_message

        return parse_message(raw)

    report = run_once(
        _settings(tmp_path),
        store,
        load_rules(None, None),
        FakeSource([b"POISON", _raw("amazon_shipped.eml")]),
        calendar,
        now=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
        parser=flaky_parse,
    )
    assert report.fetched == 2
    assert len(report.errors) == 1
    assert store.count_shipments() == 1


def test_promo_mail_is_stored_not_parsed(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()
    report = run_once(
        _settings(tmp_path),
        store,
        load_rules(None, None),
        FakeSource([_raw("promo.eml")]),
        calendar,
        now=datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc),
    )
    assert report.promos == 1
    assert store.count_shipments() == 0


def test_plan_fetch_resumes_from_last_uid():
    uids = plan_fetch(last_uid=10, uidvalidity_changed=False, available=[5, 11, 12])
    assert uids == [11, 12]


def test_plan_fetch_after_uidvalidity_change_uses_all_available():
    uids = plan_fetch(last_uid=10, uidvalidity_changed=True, available=[5, 11, 12])
    assert uids == [5, 11, 12]
