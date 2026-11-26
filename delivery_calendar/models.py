"""The strict shipment schema.

Pydantic is the source of truth: the exported JSON Schema validates parser and
model output, and backs the review form.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_UID_NAMESPACE = uuid.UUID("6f1d3f0e-2b7a-5c4d-9a1e-0d7b2c9f8e41")


class Status(StrEnum):
    announced = "announced"
    in_transit = "in_transit"
    out_for_delivery = "out_for_delivery"
    ready_for_pickup = "ready_for_pickup"
    delivered = "delivered"
    delayed = "delayed"
    exception = "exception"
    cancelled = "cancelled"
    unknown = "unknown"


class LocationKind(StrEnum):
    destination = "destination"
    pickup_point = "pickup_point"
    depot = "depot"
    unknown = "unknown"


class ActionKind(StrEnum):
    approve_received = "approve_received"
    confirm_delivery = "confirm_delivery"
    reschedule = "reschedule"
    pickup = "pickup"
    pay = "pay"
    none = "none"


STATUS_RANK: dict[Status, int] = {
    Status.announced: 1,
    Status.in_transit: 2,
    Status.out_for_delivery: 3,
    Status.ready_for_pickup: 4,
    Status.delivered: 5,
    Status.delayed: 0,
    Status.exception: 0,
    Status.cancelled: 0,
    Status.unknown: 0,
}

ARRIVAL_STATUSES = frozenset({Status.ready_for_pickup, Status.delivered})
TERMINAL_STATUSES = frozenset({Status.delivered, Status.cancelled})

REVIEW_REASONS = frozenset(
    {
        "no_tracking_number",
        "no_eta",
        "address_low_confidence",
        "model_unverified_field",
        "ambiguous_status",
        "multiple_tracking_numbers",
        "conflicting_eta",
        "possible_duplicate",
    }
)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    qty: int = 1


class Shipment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_key: str
    calendar_uid: str
    status: Status
    confidence: float = 0.0
    source: str = "regex"
    needs_review: bool = True
    suppressed: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    tracking_number: str | None = None
    tracking_url: str | None = None
    carrier: str | None = None
    carrier_display: str | None = None
    merchant: str | None = None
    order_number: str | None = None
    package_title: str | None = None
    items: list[Item] = Field(default_factory=list)

    eta_date: date | None = None
    eta_window_start: str | None = None
    eta_window_end: str | None = None
    arrival_date: date | None = None
    delivered_at: datetime | None = None
    pickup_deadline: date | None = None

    location_text: str | None = None
    location_kind: LocationKind = LocationKind.unknown
    alt_location_text: str | None = None
    alt_location_kind: LocationKind | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    location_confidence: float | None = None
    maps_url: str | None = None

    action_url: str | None = None
    action_kind: ActionKind = ActionKind.none
    action_label: str | None = None


def calendar_uid_for(first_key: str) -> str:
    """Immutable event identity. Minted once from the first key a shipment had."""
    return str(uuid.uuid5(_UID_NAMESPACE, first_key))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def shipment_key_for(
    *,
    tracking: str | None,
    carrier: str | None,
    merchant: str | None,
    order: str | None,
    sender: str,
    subject: str,
    mail_date: str,
) -> str:
    """An index hint, not identity: it may change when better data arrives."""
    if tracking:
        return f"{(carrier or 'unknown').lower()}:{tracking.upper()}"
    if merchant and order:
        return f"{_slug(merchant)}:{order.strip()}"
    digest = hashlib.sha1(
        f"{sender}|{subject}|{mail_date}".encode("utf-8")
    ).hexdigest()[:16]
    return f"hash:{digest}"
