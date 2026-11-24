"""Calendar event payload. Arrival only: no ETA, no in-transit churn."""

from __future__ import annotations

import hashlib
import json

from delivery_sync.models import Shipment, Status

_PREFIX = {
    Status.ready_for_pickup: "📦 Ready for pickup",
    Status.delivered: "✅ Delivered",
}


def _label(shipment: Shipment) -> str:
    if shipment.merchant:
        return shipment.merchant
    if shipment.carrier_display:
        return shipment.carrier_display
    if shipment.tracking_number:
        return f"…{shipment.tracking_number[-6:]}"
    return "Package"


def event_title(shipment: Shipment) -> str:
    prefix = _PREFIX.get(shipment.status, "📦 Delivery")
    label = _label(shipment)
    if shipment.tracking_number:
        return f"{prefix} — {label} · {shipment.tracking_number[-6:]}"
    return f"{prefix} — {label}"


def _description(shipment: Shipment) -> str:
    lines: list[str] = []
    status_text = (
        "Ready for pickup"
        if shipment.status is Status.ready_for_pickup
        else "Delivered"
    )
    arrived = shipment.arrival_date.isoformat() if shipment.arrival_date else "unknown"
    lines.append(f"Status: {status_text} — arrived {arrived}")
    if shipment.pickup_deadline:
        lines.append(f"Collect by: {shipment.pickup_deadline.isoformat()}")
    if shipment.location_text:
        kind = shipment.location_kind.value.replace("_", " ")
        lines.append(f"{kind.capitalize()}: {shipment.location_text}")
    if shipment.alt_location_text:
        lines.append(f"If not home: {shipment.alt_location_text}")
    if shipment.order_number:
        lines.append(f"Order: {shipment.order_number}")
    if shipment.package_title:
        lines.append(f"Subject: {shipment.package_title}")
    if shipment.items:
        items = ", ".join(f"{item.name} x{item.qty}" for item in shipment.items)
        lines.append(f"Items: {items}")
    if shipment.action_url:
        lines.append(f"{shipment.action_label or 'Action'}: {shipment.action_url}")
    if shipment.tracking_url:
        lines.append(f"Track: {shipment.tracking_url}")
    if shipment.maps_url:
        lines.append(f"Map: {shipment.maps_url}")
    if shipment.tracking_number:
        lines.append(f"Tracking: {shipment.tracking_number}")
    return "\n".join(lines)


def build_event(shipment: Shipment, calendar_id: str = "") -> dict:
    if shipment.arrival_date is None:
        raise ValueError("an arrival event needs arrival_date")
    from datetime import timedelta

    end = shipment.arrival_date + timedelta(days=1)
    event = {
        "iCalUID": f"{shipment.calendar_uid}@delivery-sync.local",
        "summary": event_title(shipment),
        "description": _description(shipment),
        "start": {"date": shipment.arrival_date.isoformat()},
        "end": {"date": end.isoformat()},
        "transparency": "transparent",
        "extendedProperties": {
            "private": {
                "delivery_sync": "1",
                "calendar_uid": shipment.calendar_uid,
            }
        },
    }
    if shipment.location_text:
        event["location"] = shipment.location_text
    if calendar_id:
        event["calendarId"] = calendar_id
    return event


def event_fingerprint(event: dict) -> str:
    canonical = json.dumps(event, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
