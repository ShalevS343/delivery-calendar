"""Address candidates from an email body.

A location needs a labelled block or a street-plus-number pattern. Anything less
is left empty on purpose: placing the owner at a wrong address is worse than
placing them nowhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from delivery_calendar.rules import AddressRules, Rules

_KIND_BY_LABEL = ("destination", "pickup_point", "depot")


@dataclass(frozen=True)
class LocationCandidate:
    text: str
    kind: str
    is_fallback: bool
    label: str
    confidence: float


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t,;:-–")
    return value


def _has_street_number(value: str, rules: AddressRules) -> bool:
    for pattern in (rules.street_then_number, rules.number_then_street):
        if pattern and re.search(pattern, value):
            return True
    return False


def _label_match(line: str, rules: AddressRules) -> tuple[str, str, bool] | None:
    lowered = line.lower()
    for label in rules.fallback_labels:
        if label.lower() in lowered:
            return ("destination", label, True)
    for kind in _KIND_BY_LABEL:
        for label in rules.labels.get(kind, ()):
            if label.lower() in lowered:
                return (kind, label, False)
    return None


def _after_label(line: str, label: str) -> str:
    lowered = line.lower()
    index = lowered.find(label.lower())
    rest = line[index + len(label) :] if index >= 0 else line
    return _clean(rest.lstrip(" \t:：-–"))


def extract_locations(text: str, rules: Rules) -> list[LocationCandidate]:
    if not text:
        return []
    address_rules = rules.addresses
    lines = [line.strip() for line in text.splitlines()]
    found: list[LocationCandidate] = []

    for index, line in enumerate(lines):
        if not line:
            continue
        match = _label_match(line, address_rules)
        if not match:
            continue
        kind, label, is_fallback = match
        value = _after_label(line, label)
        if not _has_street_number(value, address_rules):
            for following in lines[index + 1 : index + 3]:
                following = following.strip()
                if not following:
                    break
                if _has_street_number(following, address_rules):
                    value = _clean(following)
                    break
        if not value or not _has_street_number(value, address_rules):
            continue
        if is_fallback:
            kind = "pickup_point" if kind == "pickup_point" else "destination"
        found.append(
            LocationCandidate(
                text=value,
                kind=kind,
                is_fallback=is_fallback,
                label=label,
                confidence=0.85 if not is_fallback else 0.7,
            )
        )

    if not any(not candidate.is_fallback for candidate in found):
        pattern = address_rules.street_then_number or r"\S+\s+\d{1,4}"
        for match in re.finditer(pattern, text):
            value = _clean(match.group(0))
            if _has_street_number(value, address_rules) and len(value) > 6:
                found.append(
                    LocationCandidate(
                        text=value,
                        kind="unknown",
                        is_fallback=False,
                        label="pattern",
                        confidence=0.45,
                    )
                )
                break

    found.sort(key=lambda candidate: (candidate.is_fallback, -candidate.confidence))
    return found
