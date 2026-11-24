"""Tracking-number families, checksums, and carrier resolution.

A checksum is advisory: a number that fails one is kept with lower confidence and
forced into review, never silently dropped. Wrongly discarding a real tracking
number loses a delivery; wrongly keeping a fake one costs a glance in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from delivery_sync.rules import Rules, TrackingFamily

_UPU_COUNTRY_CARRIERS = {
    "IL": "israel_post",
    "US": "usps",
    "GB": "royal_mail",
    "DE": "deutsche_post",
    "CN": "china_post",
    "NL": "postnl",
}


@dataclass(frozen=True)
class TrackingMatch:
    raw: str
    normalized: str
    family: str
    carrier: str | None
    valid: bool
    span: tuple[int, int]
    confidence: float


def _alpha_value(char: str) -> int:
    return int(char) if char.isdigit() else (ord(char) - 63) % 10


def ups_check_digit(prefix: str) -> int:
    """Check digit for the 17-character ``1Z`` prefix (``1Z`` + 15 characters).

    Letters are 1-based from the alphabet (A=2 … Z=27) taken mod 10; positions
    alternate weight 1 and 2 from the left.
    """
    body = prefix[2:17]
    if len(body) != 15:
        raise ValueError("UPS prefix must be 17 characters")
    total = 0
    for index, char in enumerate(body):
        value = _alpha_value(char)
        total += value if index % 2 == 0 else value * 2
    return (10 - (total % 10)) % 10


def ups_checksum_ok(number: str) -> bool:
    number = number.strip().upper()
    if len(number) != 18 or not number.startswith("1Z"):
        return False
    if not re.fullmatch(r"1Z[0-9A-Z]{16}", number):
        return False
    try:
        return ups_check_digit(number[:17]) == int(number[17])
    except ValueError:
        return False


def upu_check_digit(body: str) -> int:
    """S10 check digit for the eight-digit serial."""
    if len(body) != 8 or not body.isdigit():
        raise ValueError("UPU body must be 8 digits")
    weights = (8, 6, 4, 2, 3, 5, 9, 7)
    total = sum(int(digit) * weight for digit, weight in zip(body, weights))
    remainder = 11 - (total % 11)
    if remainder == 10:
        return 0
    if remainder == 11:
        return 5
    return remainder


def upu_checksum_ok(number: str) -> bool:
    number = number.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", number):
        return False
    return upu_check_digit(number[2:10]) == int(number[10])


def _context_ok(text: str, start: int, end: int, words: tuple[str, ...]) -> bool:
    window = text[max(0, start - 60) : min(len(text), end + 60)].lower()
    return any(word.lower() in window for word in words)


def extract_tracking(text: str, rules: Rules) -> list[TrackingMatch]:
    accepted: list[TrackingMatch] = []
    for family in rules.tracking.families:
        for match in re.finditer(family.pattern, text, re.IGNORECASE):
            span = (match.start(), match.end())
            if any(
                span[0] < other.span[1] and other.span[0] < span[1]
                for other in accepted
            ):
                continue
            if family.needs_context and not _context_ok(
                text, span[0], span[1], rules.tracking.context_words
            ):
                continue
            candidate = _build_match(match.group(0), family, span)
            accepted.append(candidate)
    unique: dict[str, TrackingMatch] = {}
    for item in accepted:
        current = unique.get(item.normalized)
        if current is None or item.confidence > current.confidence:
            unique[item.normalized] = item
    return sorted(unique.values(), key=lambda item: item.confidence, reverse=True)


def _build_match(
    raw: str, family: TrackingFamily, span: tuple[int, int]
) -> TrackingMatch:
    normalized = re.sub(r"\s+", "", raw).upper()
    carrier = family.carrier
    if family.checksum == "upu" and len(normalized) == 13:
        carrier = _UPU_COUNTRY_CARRIERS.get(normalized[-2:])
    valid = True
    confidence = family.confidence
    if family.checksum == "ups":
        valid = ups_checksum_ok(normalized)
    elif family.checksum == "upu":
        valid = upu_checksum_ok(normalized)
    if not valid:
        confidence = max(0.0, confidence - 0.35)
    return TrackingMatch(
        raw=raw,
        normalized=normalized,
        family=family.id,
        carrier=carrier,
        valid=valid,
        span=span,
        confidence=confidence,
    )
