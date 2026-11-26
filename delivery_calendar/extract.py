"""Tier-0 extraction: one ShipmentCandidate per tracking number, no model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from delivery_calendar.addresses import extract_locations
from delivery_calendar.dates import DateFacts, extract_dates
from delivery_calendar.links import classify_action, extract_urls
from delivery_calendar.mailparse import ParsedMail
from delivery_calendar.models import (
    ActionKind,
    LocationKind,
    Shipment,
    Status,
    calendar_uid_for,
    shipment_key_for,
)
from delivery_calendar.normalize import TrackingMatch, extract_tracking
from delivery_calendar.rules import Rules

_MARKETPLACES = {"amazon", "aliexpress", "temu", "shein", "ebay"}

_ORDER = re.compile(
    r"(?:order|order\s*#|order\s*number|הזמנה|מספר הזמנה)\s*[:#]?\s*([0-9][0-9\-]{4,})",
    re.IGNORECASE,
)

_ITEM_LABELS = ("item:", "items:", "product:", "פריט:", "תכולה:")


@dataclass
class ShipmentCandidate:
    shipment: Shipment
    family: str | None = None
    warnings: list[str] = field(default_factory=list)


def detect_status(text: str) -> Status:
    haystack = text.lower()
    if re.search(r"\b(cancelled|canceled|בוטל)\b", haystack):
        return Status.cancelled
    if re.search(r"(was delivered|has been delivered|נמסר ללקוח|נמסר\b)", haystack):
        return Status.delivered
    if re.search(
        r"(ready for (?:pickup|collection)|ready to collect|מוכן לאיסוף|הגיע לנקודת|מחכה לך)",
        haystack,
    ):
        return Status.ready_for_pickup
    if re.search(r"(out for delivery|בדרך אליך|יצא לחלוקה)", haystack):
        return Status.out_for_delivery
    if re.search(r"\b(delayed|postponed|עיכוב|נדחה)\b", haystack):
        return Status.delayed
    if re.search(
        r"(has shipped|shipped|on (?:its|the) way|in transit|נשלח|בדרך)", haystack
    ):
        return Status.in_transit
    if re.search(r"(order confirmed|we received your order|ההזמנה נקלטה)", haystack):
        return Status.announced
    return Status.unknown


def _order_number(text: str) -> str | None:
    match = _ORDER.search(text)
    return match.group(1).strip() if match else None


def _items(text: str) -> list[str]:
    items: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        lowered = line.strip().lower()
        if not any(lowered.startswith(label) for label in _ITEM_LABELS):
            continue
        first = line.split(":", 1)[1].strip()
        if first:
            items.append(first)
        for following in lines[index + 1 : index + 6]:
            following = following.strip()
            if not following:
                break
            if len(following) > 80:
                break
            items.append(following)
        break
    return items[:5]


def _tracking_url(urls: list[str], tracking: str | None) -> str | None:
    if not tracking:
        return None
    for url in urls:
        if tracking.lower() in url.lower():
            return url
    return None


def _confidence(
    tracking: TrackingMatch | None, status: Status, location: bool, merchant: bool
) -> float:
    score = 0.45
    if tracking is not None:
        score += 0.35 if tracking.valid else 0.12
    if status is not Status.unknown:
        score += 0.1
    if location:
        score += 0.05
    if merchant:
        score += 0.05
    return min(1.0, round(score, 2))


def _build(
    mail: ParsedMail,
    facts: DateFacts,
    rules: Rules,
    status: Status,
    tracking: TrackingMatch | None,
    min_confidence: float,
) -> ShipmentCandidate:
    sender_rule = rules.carrier_for_sender(mail.from_addr)
    carrier = tracking.carrier if tracking else (sender_rule or {}).get("carrier")
    carrier_display = rules.display_for_carrier(carrier) or (sender_rule or {}).get("display")
    merchant = carrier_display if carrier in _MARKETPLACES else None

    urls = extract_urls(mail.text)
    action = classify_action(mail.text, urls)
    locations = extract_locations(mail.text, rules)
    primary = next((loc for loc in locations if not loc.is_fallback), None)
    fallback = next((loc for loc in locations if loc.is_fallback), None)

    order = _order_number(mail.text) or _order_number(mail.subject)
    key = shipment_key_for(
        tracking=tracking.normalized if tracking else None,
        carrier=carrier,
        merchant=merchant,
        order=order,
        sender=mail.from_addr,
        subject=mail.subject,
        mail_date=mail.date.date().isoformat(),
    )

    review: list[str] = []
    if tracking is None:
        review.append("no_tracking_number")
    elif not tracking.valid:
        review.append("tracking_checksum_failed")
    if status is Status.unknown:
        review.append("ambiguous_status")
    if any(reason == "conflicts_with_eta" for _, reason in facts.rejected):
        review.append("conflicting_eta")

    confidence = _confidence(
        tracking, status, location=primary is not None, merchant=merchant is not None
    )
    if confidence < min_confidence and not review:
        review.append("ambiguous_status")

    shipment = Shipment(
        shipment_key=key,
        calendar_uid=calendar_uid_for(key),
        status=status,
        confidence=confidence,
        source="regex",
        needs_review=bool(review),
        review_reasons=review,
        tracking_number=tracking.normalized if tracking else None,
        tracking_url=_tracking_url(urls, tracking.normalized if tracking else None),
        carrier=carrier,
        carrier_display=carrier_display,
        merchant=merchant,
        order_number=order,
        package_title=mail.subject or None,
        items=[],
        eta_date=facts.eta_date,
        eta_window_start=facts.window_start,
        eta_window_end=facts.window_end,
        arrival_date=facts.arrival_date,
        pickup_deadline=facts.pickup_deadline,
        location_text=primary.text if primary else None,
        location_kind=LocationKind(primary.kind) if primary else LocationKind.unknown,
        alt_location_text=fallback.text if fallback else None,
        alt_location_kind=LocationKind(fallback.kind) if fallback else None,
        action_url=action.url if action else None,
        action_kind=ActionKind(action.kind) if action else ActionKind.none,
        action_label=action.label if action else None,
    )
    return ShipmentCandidate(
        shipment=shipment,
        family=tracking.family if tracking else None,
        warnings=[reason for _, reason in facts.rejected],
    )


def extract(
    mail: ParsedMail,
    rules: Rules,
    min_confidence: float = 0.7,
) -> list[ShipmentCandidate]:
    haystack = f"{mail.subject}\n{mail.text}"
    facts = extract_dates(mail.text, mail.date.date(), rules_languages())
    status = detect_status(haystack)
    matches = extract_tracking(haystack, rules)
    if not matches:
        return [_build(mail, facts, rules, status, None, min_confidence)]
    return [
        _build(mail, facts, rules, status, match, min_confidence) for match in matches
    ]


def rules_languages() -> tuple[str, ...]:
    return ("he", "en")
