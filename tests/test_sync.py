from datetime import date, datetime, timezone

from delivery_sync.calendar_client import FakeCalendar
from delivery_sync.models import Shipment, Status, calendar_uid_for
from delivery_sync.store import Store
from delivery_sync.sync import sync_shipment


def _shipment(**overrides) -> Shipment:
    data = {
        "shipment_key": "amazon:TBA123456789012",
        "calendar_uid": calendar_uid_for("amazon:TBA123456789012"),
        "status": Status.in_transit,
        "confidence": 0.9,
        "needs_review": False,
        "merchant": "Amazon",
        "tracking_number": "TBA123456789012",
    }
    data.update(overrides)
    return Shipment(**data)


def _store(tmp_path):
    store = Store(tmp_path / "s.db")
    store.migrate()
    return store


def test_in_transit_mail_calls_no_calendar_method(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(_shipment(eta_date=date(2026, 11, 28)))
    sync_shipment(store, calendar, result.shipment)
    assert calendar.calls == []
    assert store.shipment_event_meta(result.shipment_id)["calendar_event_id"] is None


def test_ready_for_pickup_creates_then_delivered_patches_same_event(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    first = store.upsert_shipment(
        _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    )
    sync_shipment(store, calendar, first.shipment)
    event_id = store.shipment_event_meta(first.shipment_id)["calendar_event_id"]
    assert event_id is not None
    assert len(calendar.events) == 1

    delivered = store.upsert_shipment(
        _shipment(
            status=Status.delivered,
            arrival_date=date(2026, 11, 25),
            delivered_at=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
        )
    )
    sync_shipment(store, calendar, delivered.shipment)
    assert store.shipment_event_meta(first.shipment_id)["calendar_event_id"] == event_id
    assert len(calendar.events) == 1
    assert calendar.events[event_id]["start"]["date"] == "2026-11-25"
    assert calendar.events[event_id]["summary"].startswith("✅")


def test_noop_mail_does_not_patch(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    created = store.upsert_shipment(
        _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    )
    sync_shipment(store, calendar, created.shipment)
    calls_after_create = len(calendar.calls)

    sync_shipment(store, calendar, created.shipment)
    assert len(calendar.calls) == calls_after_create


def test_duplicate_insert_adopts_existing(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    shipment = _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    # Simulate a rebuilt database: the event already exists in Google.
    existing_id, _ = calendar.insert(
        {
            "iCalUID": f"{shipment.calendar_uid}@delivery-sync.local",
            "summary": "old",
            "start": {"date": "2026-11-24"},
            "end": {"date": "2026-11-25"},
        }
    )
    result = store.upsert_shipment(shipment)
    sync_shipment(store, calendar, result.shipment)
    assert store.shipment_event_meta(result.shipment_id)["calendar_event_id"] == existing_id
    assert calendar.events[existing_id]["summary"] != "old"


def test_suppressed_is_skipped(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(
        _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    )
    store.suppress(result.shipment_id)
    sync_shipment(store, calendar, store.get_shipment(result.shipment_id))
    assert calendar.calls == []


def test_dry_run_never_calls_calendar(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(
        _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    )
    outcome = sync_shipment(store, calendar, result.shipment, dry_run=True)
    assert outcome.action == "create"
    assert calendar.calls == []


def test_cancelled_deletes_existing_event(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(
        _shipment(status=Status.ready_for_pickup, arrival_date=date(2026, 11, 24))
    )
    sync_shipment(store, calendar, result.shipment)
    event_id = store.shipment_event_meta(result.shipment_id)["calendar_event_id"]

    cancelled = store.upsert_shipment(
        _shipment(status=Status.cancelled, arrival_date=date(2026, 11, 24))
    )
    sync_shipment(store, calendar, cancelled.shipment)
    assert event_id not in calendar.events


def test_needs_review_shipment_does_not_create_event(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(
        _shipment(
            status=Status.delivered,
            arrival_date=date(2026, 11, 24),
            needs_review=True,
            tracking_number=None,
            review_reasons=["no_tracking_number"],
        )
    )
    sync_shipment(store, calendar, result.shipment)
    assert calendar.calls == []


def test_accepting_a_review_shipment_creates_its_event(tmp_path):
    store = _store(tmp_path)
    calendar = FakeCalendar()
    result = store.upsert_shipment(
        _shipment(
            status=Status.delivered,
            arrival_date=date(2026, 11, 24),
            needs_review=True,
            tracking_number=None,
            review_reasons=["no_tracking_number"],
        )
    )
    sync_shipment(store, calendar, result.shipment)
    assert calendar.calls == []
    store.accept(result.shipment_id)
    sync_shipment(store, calendar, store.get_shipment(result.shipment_id))
    assert len(calendar.events) == 1
