"""Date, window, arrival and pickup-deadline extraction.

Only lines that talk about time are handed to dateparser; running it over a whole
email is slow and invites nonsense matches from order numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import dateparser

_ARRIVAL_PATTERNS = (
    r"ready for (?:pickup|collection)",
    r"ready to (?:be )?collect",
    r"waiting for you at",
    r"has arrived at",
    r"arrived at (?:the )?(?:pickup|collection|locker|depot)",
    r"was delivered",
    r"has been delivered",
    r"delivered to",
    r"מוכן לאיסוף",
    r"הגיע לנקודת",
    r"מחכה לך",
    r"נמסר",
    r"נמצא בנקודת",
)

_TIME_HINT = re.compile(
    r"\b(tomorrow|today|tonight|arriv\w*|deliver\w*|expect\w*|by |until |between|"
    r"eta|estimated|משלוח|אספקה|מחר|היום|יגיע|צפוי)\b",
    re.IGNORECASE,
)

_WINDOW_PATTERNS = (
    re.compile(r"between\s+(\d{1,2}[:.]\d{2})\s*(?:and|to|-|–)\s*(\d{1,2}[:.]\d{2})", re.I),
    re.compile(r"(\d{1,2}[:.]\d{2})\s*(?:-|–|to|until)\s*(\d{1,2}[:.]\d{2})", re.I),
    re.compile(r"(\d{1,2}):(\d{2})\s*(?:-|–)\s*(\d{1,2}):(\d{2})"),
)

_HOLD_PATTERN = re.compile(
    r"(?:within|for|up to|held for|collect(?:ed)? within|עד)\s+(\d{1,2})\s*(?:days|יום|ימים)",
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_DATE_TOKEN = re.compile(
    r"(\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:today|tomorrow|tonight)\b"
    r"|מחר|היום|מחרתיים)",
    re.IGNORECASE,
)

_RELATIVE_DAYS = {
    "today": 0,
    "tonight": 0,
    "היום": 0,
    "tomorrow": 1,
    "מחר": 1,
    "מחרתיים": 2,
}

_MIN_DAYS = -60
_MAX_DAYS = 120


@dataclass
class DateFacts:
    eta_date: date | None = None
    window_start: str | None = None
    window_end: str | None = None
    arrival_date: date | None = None
    pickup_deadline: date | None = None
    explicit_arrival: bool = False
    rejected: list[tuple[str, str]] = field(default_factory=list)


def _normalise_time(value: str) -> str:
    return value.replace(".", ":")


def _window(text: str) -> tuple[str | None, str | None]:
    for pattern in _WINDOW_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 4:
            start = f"{int(groups[0]):02d}:{groups[1]}"
            end = f"{int(groups[2]):02d}:{groups[3]}"
        else:
            start, end = (_normalise_time(g) for g in groups)
        return start, end
    return None, None


def _arrival(text: str) -> tuple[bool, bool]:
    """(arrival-looking, says delivered specifically)."""
    lowered = text.lower()
    found = False
    delivered = False
    for pattern in _ARRIVAL_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            found = True
            if re.search(r"deliver|נמסר", pattern):
                delivered = True
    return found, delivered


def _parse_line(line: str, mail_date: date, languages: tuple[str, ...]):
    lowered = line.lower()
    for word, offset in _RELATIVE_DAYS.items():
        if re.search(rf"(?<![\w\u0590-\u05FF]){re.escape(word)}(?![\w\u0590-\u05FF])", lowered):
            return mail_date + timedelta(days=offset)
    settings = {
        "RELATIVE_BASE": _as_datetime(mail_date),
        "DATE_ORDER": "DMY",
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }
    for match in _DATE_TOKEN.finditer(line):
        start = max(0, match.start() - 30)
        end = min(len(line), match.end() + 10)
        parsed = dateparser.parse(
            line[start:end], languages=list(languages), settings=settings
        )
        if parsed:
            return parsed.date() if hasattr(parsed, "date") else parsed
    parsed = dateparser.parse(line, languages=list(languages), settings=settings)
    if parsed:
        return parsed.date() if hasattr(parsed, "date") else parsed
    return None


def extract_dates(
    text: str,
    mail_date: date,
    languages: tuple[str, ...] = ("he", "en"),
) -> DateFacts:
    facts = DateFacts()
    if not text:
        return facts

    arrival, delivered = _arrival(text)
    if arrival:
        facts.explicit_arrival = True
        facts.arrival_date = mail_date

    window_start, window_end = _window(text)
    facts.window_start, facts.window_end = window_start, window_end

    for line in text.splitlines():
        line = line.strip()
        if not line or not _TIME_HINT.search(line):
            continue
        iso = _ISO_DATE.search(line)
        parsed = None
        if iso:
            try:
                parsed = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            except ValueError:
                continue
        if parsed is None:
            parsed = _parse_line(line, mail_date, languages)
        if not parsed:
            continue
        parsed_date = parsed.date() if hasattr(parsed, "date") else parsed
        delta = (parsed_date - mail_date).days
        if not (_MIN_DAYS <= delta <= _MAX_DAYS):
            facts.rejected.append((parsed_date.isoformat(), "out_of_range"))
            continue
        if facts.eta_date is None:
            facts.eta_date = parsed_date
        elif facts.eta_date != parsed_date:
            facts.rejected.append((parsed_date.isoformat(), "conflicts_with_eta"))

    hold = _HOLD_PATTERN.search(text)
    if hold and facts.arrival_date is not None:
        facts.pickup_deadline = facts.arrival_date + timedelta(days=int(hold.group(1)))

    if delivered and facts.arrival_date is None:
        facts.arrival_date = mail_date
    return facts


def _as_datetime(day: date):
    from datetime import datetime, time

    return datetime.combine(day, time(12, 0))
