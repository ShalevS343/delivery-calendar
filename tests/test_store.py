from datetime import date, datetime, timezone

import pytest

from delivery_sync.models import Shipment, Status, calendar_uid_for, shipment_key_for
from delivery_sync.store import Store, apply_update


def _shipment(**overrides) -> Shipment:
    tracking = overrides.get("tracking_number")
    carrier = overrides.get("carrier")
    key = overrides.get(
        "shipment_key",
        shipment_key_for(
            tracking=tracking,
            carrier=carrier,
            merchant=overrides.get("merchant"),
            order=overrides.get("order_number"),
            sender="a@b.c",
            subject="s",
            mail_date="2026-11-20",
        ),
    )
    data = {
        "shipment_key": key,
        "calendar_uid": calendar_uid_for(key),
        "status": Status.in_transit,
        "confidence": 0.9,
        "needs_review": False,
    }
    data.update(overrides)
    return Shipment(**data)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.migrate()
    return s


def test_same_tracking_twice_is_one_row(store):
    first = store.upsert_shipment(_shipment(tracking_number="1Z999AA10123456784", carrier="ups"))
    second = store.upsert_shipment(
        _shipment(tracking_number="1Z999AA10123456784", carrier="ups", eta_date=date(2026, 11, 22))
    )
    assert first.created is True
    assert second.created is False
    assert first.shipment_id == second.shipment_id
    assert store.count_shipments() == 1


def test_order_only_then_tracking_merges_into_one_row_with_same_uid(store):
    announced = _shipment(
        merchant="Amazon",
        order_number="123-4567890-1234567",
        status=Status.announced,
        tracking_number=None,
    )
    a = store.upsert_shipment(announced)
    uid = store.get_shipment(a.shipment_id).calendar_uid

    shipped = _shipment(
        merchant="Amazon",
        order_number="123-4567890-1234567",
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.in_transit,
    )
    b = store.upsert_shipment(shipped)

    assert b.created is False
    assert b.shipment_id == a.shipment_id
    merged = store.get_shipment(a.shipment_id)
    assert merged.calendar_uid == uid
    assert merged.tracking_number == "TBA123456789012"
    assert merged.shipment_key == "amazon:TBA123456789012"
    assert store.count_shipments() == 1


def test_three_packages_of_one_order_stay_three_rows(store):
    first = store.upsert_shipment(
        _shipment(
            merchant="Amazon",
            order_number="555-1111111-2222222",
            tracking_number="TBA123456789012",
            carrier="amazon",
        )
    )
    second = store.upsert_shipment(
        _shipment(
            merchant="Amazon",
            order_number="555-1111111-2222222",
            tracking_number="TBA123456789013",
            carrier="amazon",
        )
    )
    third = store.upsert_shipment(
        _shipment(
            merchant="Amazon",
            order_number="555-1111111-2222222",
            tracking_number="TBA123456789014",
            carrier="amazon",
        )
    )
    ids = {first.shipment_id, second.shipment_id, third.shipment_id}
    assert len(ids) == 3
    assert store.count_shipments() == 3


def test_delivered_cannot_regress():
    delivered = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.delivered,
        arrival_date=date(2026, 11, 24),
    )
    older = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.delayed,
        eta_date=date(2026, 11, 30),
    )
    merged, changed = apply_update(delivered, older, mail_date=date(2026, 11, 20))
    assert merged.status is Status.delivered
    assert merged.arrival_date == date(2026, 11, 24)
    assert changed == []


def test_in_transit_after_arrival_keeps_arrival_status():
    arrived = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.ready_for_pickup,
        arrival_date=date(2026, 11, 24),
    )
    stale = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.out_for_delivery,
    )
    merged, _ = apply_update(arrived, stale, mail_date=date(2026, 11, 23))
    assert merged.status is Status.ready_for_pickup


def test_delivered_patches_ready_for_pickup():
    arrived = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.ready_for_pickup,
        arrival_date=date(2026, 11, 24),
    )
    delivered = _shipment(
        tracking_number="TBA123456789012",
        carrier="amazon",
        status=Status.delivered,
        arrival_date=date(2026, 11, 25),
    )
    merged, changed = apply_update(arrived, delivered, mail_date=date(2026, 11, 25))
    assert merged.status is Status.delivered
    assert merged.arrival_date == date(2026, 11, 25)
    assert "status" in changed


def test_columns_match_model_fields(store):
    columns = store.shipment_columns()
    assert columns == set(Shipment.model_fields)


def test_message_insert_is_idempotent(store):
    now = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    assert store.insert_message("m1", "s1", "a@b.c", "Subject", now, verdict="delivery") is True
    assert store.insert_message("m1", "s1", "a@b.c", "Subject", now, verdict="delivery") is False
    assert store.message_count() == 1


def test_suppress_hides_from_sync(store):
    result = store.upsert_shipment(
        _shipment(
            tracking_number="TBA123456789012",
            carrier="amazon",
            status=Status.ready_for_pickup,
            arrival_date=date(2026, 11, 24),
        )
    )
    store.suppress(result.shipment_id)
    assert store.get_shipment(result.shipment_id).suppressed is True
    assert store.shipments_needing_sync() == []
