"""URL extraction, redirect unwrapping, and action-link classification.

The only URL the calendar shows is the one that does something: confirm receipt,
approve, reschedule, collect. Tracking pixels, unsubscribe links and social links
are noise and are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_NOISE_HOSTS = (
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "youtube.com",
    "tiktok.com",
)

_NOISE_PATH = re.compile(r"(unsubscribe|optout|opt-out|preferences|pixel|open\.gif|1x1)", re.I)

_REDIRECT_HOSTS = (
    "track.",
    "click.",
    "links.",
    "email.",
    "mailchi.mp",
    "sendgrid.net",
    "awstrack.me",
    "list-manage.com",
)

_ACTIONS = (
    ("confirm_delivery", re.compile(r"(confirm[-_/]?(delivery|receipt|arrival)|\bconfirm\b|approve[-_/]?(delivery|receipt))", re.I)),
    ("approve_received", re.compile(r"(approve|received|accept[-_/]?delivery)", re.I)),
    ("reschedule", re.compile(r"(reschedule|change[-_/]?(date|delivery)|another[-_/]?day)", re.I)),
    ("pickup", re.compile(r"(pickup|collect|locker|pick[-_/]?up)", re.I)),
    ("pay", re.compile(r"(pay|customs|duties|payment)", re.I)),
)


@dataclass(frozen=True)
class ActionLink:
    url: str
    kind: str
    label: str
    unwrapped: bool


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?")
        if url in seen:
            continue
        seen.add(url)
        host = (urlparse(url).hostname or "").lower()
        if any(host == noise or host.endswith("." + noise) for noise in _NOISE_HOSTS):
            continue
        if _NOISE_PATH.search(url):
            continue
        urls.append(url)
    return urls


def unwrap(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if not any(marker in host for marker in _REDIRECT_HOSTS):
        return url
    query = parse_qs(urlparse(url).query)
    for key in ("url", "u", "target", "redirect", "dest", "link"):
        values = query.get(key)
        if values:
            candidate = unquote(values[0])
            if candidate.startswith("http"):
                return candidate
    return url


_LABEL_BEFORE = re.compile(r"([A-Za-zא-ת][^:\n]{2,40})\s*[:\-]\s*$")


def classify_action(text: str, urls: list[str]) -> ActionLink | None:
    for raw in urls:
        target = unwrap(raw)
        label = _label_for(text, raw)
        haystack = f"{target} {label or ''}"
        for kind, pattern in _ACTIONS:
            if not pattern.search(haystack):
                continue
            if kind == "approve_received" and "confirm" in haystack.lower():
                kind = "confirm_delivery"
            return ActionLink(
                url=target,
                kind=kind,
                label=label or kind.replace("_", " "),
                unwrapped=target != raw,
            )
    return None


def _label_for(text: str, url: str) -> str | None:
    index = text.find(url)
    if index <= 0:
        return None
    prefix = text[max(0, index - 120) : index]
    match = _LABEL_BEFORE.search(prefix)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()
