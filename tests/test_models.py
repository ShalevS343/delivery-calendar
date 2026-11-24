import pytest
from pydantic import ValidationError

from delivery_sync.models import (
    ActionKind,
    LocationKind,
    Shipment,
    Status,
    calendar_uid_for,
    shipment_key_for,
)


def test_shipment_rejects_unknown_field():
    with pytest.raises(ValidationError):
        Shipment(
            shipment_key="ups:1Z999AA10123456784",
            calendar_uid=calendar_uid_for("ups:1Z999AA10123456784"),
            status=Status.in_transit,
            surprise=1,
        )


def test_shipment_requires_status_and_key():
    with pytest.raises(ValidationError):
        Shipment(calendar_uid="x")  # type: ignore[call-arg]


def test_defaults_are_conservative():
    s = Shipment(
        shipment_key="k",
        calendar_uid=calendar_uid_for("k"),
        status=Status.unknown,
    )
    assert s.needs_review is True
    assert s.suppressed is False
    assert s.review_reasons == []
    assert s.source == "regex"
    assert s.location_kind is LocationKind.unknown
    assert s.action_kind is ActionKind.none


def test_calendar_uid_is_stable_for_same_key():
    a = calendar_uid_for("ups:1Z999AA10123456784")
    b = calendar_uid_for("ups:1Z999AA10123456784")
    c = calendar_uid_for("ups:1Z999AA10123456780")
    assert a == b
    assert a != c


def test_key_prefers_tracking_over_merchant():
    assert (
        shipment_key_for(
            tracking="1z999aa10123456784",
            carrier="ups",
            merchant="Amazon",
            order="123-4567890-1234567",
            sender="ship@amazon.com",
            subject="shipped",
            mail_date="2026-11-20",
        )
        == "ups:1Z999AA10123456784"
    )
    assert (
        shipment_key_for(
            tracking="1z999aa10123456784",
            carrier=None,
            merchant=None,
            order=None,
            sender="a@b.c",
            subject="s",
            mail_date="2026-11-20",
        )
        == "unknown:1Z999AA10123456784"
    )


def test_key_falls_back_to_order_then_hash():
    order_key = shipment_key_for(
        tracking=None,
        carrier=None,
        merchant="Amazon",
        order="123-4567890-1234567",
        sender="ship@amazon.com",
        subject="order confirmed",
        mail_date="2026-11-20",
    )
    assert order_key.startswith("amazon:")

    hash_key = shipment_key_for(
        tracking=None,
        carrier=None,
        merchant=None,
        order=None,
        sender="courier@example.com",
        subject="your parcel",
        mail_date="2026-11-20",
    )
    assert hash_key.startswith("hash:")
    assert hash_key == shipment_key_for(
        tracking=None,
        carrier=None,
        merchant=None,
        order=None,
        sender="courier@example.com",
        subject="your parcel",
        mail_date="2026-11-20",
    )


def test_schema_is_closed_and_exportable(tmp_path):
    schema = Shipment.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "arrival_date" in schema["properties"]
    assert set(Status) <= set(Status.__members__.values())
