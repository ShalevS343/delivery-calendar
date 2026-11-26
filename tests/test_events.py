from datetime import date

from delivery_calendar.events import build_event, event_fingerprint, event_title
from delivery_calendar.models import Shipment, Status, calendar_uid_for


def _shipment(**overrides) -> Shipment:
    data = {
        "shipment_key": "amazon:TBA123456789012",
        "calendar_uid": calendar_uid_for("amazon:TBA123456789012"),
        "status": Status.ready_for_pickup,
        "confidence": 0.9,
        "needs_review": False,
        "merchant": "Amazon",
        "tracking_number": "TBA123456789012",
        "arrival_date": date(2026, 11, 24),
    }
    data.update(overrides)
    return Shipment(**data)


def test_event_is_all_day_on_arrival_date():
    event = build_event(_shipment())
    assert event["start"] == {"date": "2026-11-24"}
    assert event["end"] == {"date": "2026-11-25"}
    assert "dateTime" not in event["start"]


def test_event_has_ical_uid_and_private_props():
    shipment = _shipment()
    event = build_event(shipment)
    assert event["iCalUID"] == f"{shipment.calendar_uid}@delivery-calendar.local"
    assert event["extendedProperties"]["private"]["calendar_uid"] == shipment.calendar_uid
    assert event["extendedProperties"]["private"]["delivery_calendar"] == "1"


def test_event_location_is_address_string():
    event = build_event(
        _shipment(
            location_text="Herzl 12, Tel Aviv",
            location_kind="pickup_point",
        )
    )
    assert event["location"] == "Herzl 12, Tel Aviv"


def test_description_carries_deadline_and_action_link():
    event = build_event(
        _shipment(
            pickup_deadline=date(2026, 12, 1),
            action_url="https://courier.example.com/confirm?id=9",
            action_label="Confirm receipt",
            alt_location_text="Dizengoff 99, Tel Aviv",
        )
    )
    description = event["description"]
    assert "2026-12-01" in description
    assert "https://courier.example.com/confirm?id=9" in description
    assert "Dizengoff 99" in description


def test_title_falls_back_when_merchant_unknown():
    assert event_title(_shipment(merchant=None, carrier_display="HFD")).startswith("📦")
    assert "HFD" in event_title(_shipment(merchant=None, carrier_display="HFD"))
    assert event_title(_shipment(merchant=None, carrier_display=None, tracking_number=None))


def test_fingerprint_ignores_nothing_but_is_stable():
    event = build_event(_shipment())
    assert event_fingerprint(event) == event_fingerprint(dict(event))
    changed = dict(event)
    changed["summary"] = "different"
    assert event_fingerprint(event) != event_fingerprint(changed)
