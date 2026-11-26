"""Triage: is this mail about a delivery, a promo, or neither?

Server-side IMAP search already skips obvious categories; this is the local
safety net and it records *why* it decided, so rules can be tuned from real mail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from delivery_calendar.mailparse import ParsedMail
from delivery_calendar.normalize import extract_tracking
from delivery_calendar.rules import Rules

# A delivery word alone is not enough: repo names ("delivery-calendar"), product
# names and marketing copy all contain them. Without a tracking number, the mail
# must also say when — an arrival phrase or a date.
_ARRIVAL_OR_TIME = re.compile(
    r"(ready for (?:pickup|collection)|ready to collect|out for delivery|"
    r"(?:was|has been|will be|been) delivered|delivered to|arriv\w+|"
    r"\beta\b|estimated delivery|delivery (?:date|window|on|by)|"
    r"tomorrow|today|"
    r"מוכן לאיסוף|נמסר|מחר|היום|הגיע)",
    re.IGNORECASE,
)

_DELIVERY_WORDS = (
    "delivery",
    "deliver",
    "shipped",
    "shipment",
    "parcel",
    "package",
    "courier",
    "tracking",
    "pickup",
    "arriving",
    "משלוח",
    "מעקב",
    "איסוף",
    "מסירה",
    "חבילה",
    "נמסר",
)


@dataclass(frozen=True)
class Verdict:
    kind: str
    rule: str


def classify(mail: ParsedMail, rules: Rules) -> Verdict:
    tracking = extract_tracking(mail.text + "\n" + mail.subject, rules)
    haystack = f"{mail.subject}\n{mail.text}".lower()

    domain = mail.from_addr.rsplit("@", 1)[-1]
    for promo_domain in rules.promos.domains:
        if domain == promo_domain or domain.endswith("." + promo_domain):
            if not tracking:
                return Verdict("promo", f"sender_domain:{promo_domain}")

    if not tracking and "list-unsubscribe" in mail.headers:
        return Verdict("promo", "list_unsubscribe")

    has_delivery_word = any(word in haystack for word in _DELIVERY_WORDS)
    if tracking:
        return Verdict("delivery", f"tracking:{tracking[0].family}")
    if not has_delivery_word:
        promo_hits = [word for word in rules.promos.keywords if word.lower() in haystack]
        if promo_hits:
            return Verdict("promo", f"promo_keyword:{promo_hits[0]}")
        return Verdict("unrelated", "no_delivery_signal")
    promo_hits = [word for word in rules.promos.keywords if word.lower() in haystack]
    if promo_hits and not mail.headers.get("reply-to"):
        return Verdict("promo", f"promo_keyword:{promo_hits[0]}")
    if not _ARRIVAL_OR_TIME.search(haystack):
        return Verdict("unrelated", "delivery_word_without_date")
    return Verdict("delivery", "delivery_keyword")
