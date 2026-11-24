"""Arrival-only calendar synchronisation.

Nothing here runs for a shipment that has not reached its delivery point. That
single rule is what keeps the owner's calendar free of status chatter.
"""

from __future__ import annotations

from dataclasses import dataclass

from delivery_sync.calendar_client import (
    CalendarConflict,
    CalendarNotFound,
    CalendarPort,
)
from delivery_sync.events import build_event, event_fingerprint
from delivery_sync.models import ARRIVAL_STATUSES, Shipment, Status
from delivery_sync.store import Store


@dataclass
class SyncResult:
    action: str
    event_id: str | None = None


def sync_shipment(
    store: Store,
    calendar: CalendarPort,
    shipment: Shipment,
    *,
    dry_run: bool = False,
) -> SyncResult:
    shipment_id = store.shipment_id_for_uid(shipment.calendar_uid)
    meta = store.shipment_event_meta(shipment_id)
    event_id = meta.get("calendar_event_id")

    if shipment.suppressed:
        if event_id and not dry_run:
            try:
                calendar.delete(event_id)
            except CalendarNotFound:
                pass
            store.clear_event(shipment_id)
        return SyncResult("none")

    if shipment.status not in ARRIVAL_STATUSES:
        if event_id and shipment.status is Status.cancelled:
            if not dry_run:
                try:
                    calendar.delete(event_id)
                except CalendarNotFound:
                    pass
                store.clear_event(shipment_id)
            return SyncResult("delete", event_id)
        return SyncResult("none")

    if shipment.arrival_date is None:
        return SyncResult("none")

    event = build_event(shipment)
    fingerprint = event_fingerprint(event)

    if event_id is None:
        if dry_run:
            return SyncResult("create")
        return _create(store, calendar, shipment, shipment_id, event, fingerprint)

    if meta.get("event_fingerprint") == fingerprint:
        return SyncResult("none", event_id)

    if dry_run:
        return SyncResult("patch", event_id)
    try:
        new_id, etag = calendar.patch(event_id, event, meta.get("event_etag"))
    except CalendarNotFound:
        store.clear_event(shipment_id)
        return _create(store, calendar, shipment, shipment_id, event, fingerprint)
    except CalendarConflict:
        found = calendar.find_by_ical_uid(event["iCalUID"]) or {}
        adopted_id = found.get("id")
        if not adopted_id:
            raise
        new_id, etag = calendar.patch(adopted_id, event, found.get("etag"))
    store.set_event_meta(shipment_id, new_id, etag, fingerprint, calendar.calendar_id)
    return SyncResult("patch", new_id)


def _create(
    store: Store,
    calendar: CalendarPort,
    shipment: Shipment,
    shipment_id: int,
    event: dict,
    fingerprint: str,
) -> SyncResult:
    try:
        event_id, etag = calendar.insert(event)
        store.set_event_meta(shipment_id, event_id, etag, fingerprint, calendar.calendar_id)
        return SyncResult("create", event_id)
    except CalendarConflict:
        found = calendar.find_by_ical_uid(event["iCalUID"])
        if not found:
            raise
        existing_id = found["id"]
        if event_fingerprint(found) == fingerprint:
            store.set_event_meta(
                shipment_id, existing_id, found.get("etag"), fingerprint, calendar.calendar_id
            )
            return SyncResult("adopt", existing_id)
        new_id, etag = calendar.patch(existing_id, event, found.get("etag"))
        store.set_event_meta(shipment_id, new_id, etag, fingerprint, calendar.calendar_id)
        return SyncResult("adopt", new_id)
