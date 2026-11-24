"""MIME parsing: headers plus readable text, HTML fallback, quoted reply removal."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses, parsedate_to_datetime

from bs4 import BeautifulSoup

MAX_BODY_BYTES = 256 * 1024

_QUOTE_START = re.compile(
    r"(^On .+ wrote:$|^-{2,}\s*Original Message\s*-{2,}$|^From: .+$)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedMail:
    gm_msgid: str
    message_id: str
    thread_id: str
    date: datetime
    from_addr: str
    from_name: str
    subject: str
    text: str
    raw: bytes = field(repr=False, default=b"")
    headers: dict[str, str] = field(default_factory=dict, repr=False)


def _body_text(message) -> str:
    plain: str | None = None
    html: str | None = None
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        try:
            payload = part.get_content()
        except (LookupError, UnicodeDecodeError, ValueError):
            charset = part.get_content_charset() or "utf-8"
            raw_payload = part.get_payload(decode=True) or b""
            payload = raw_payload.decode(charset, errors="replace")
        if not isinstance(payload, str):
            continue
        if content_type == "text/plain" and plain is None:
            plain = payload
        elif content_type == "text/html" and html is None:
            html = payload
    if plain:
        return plain
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n")
    return ""


def _clean(text: str) -> str:
    marker = _QUOTE_START.search(text)
    if marker:
        text = text[: marker.start()]
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        lines.append(stripped)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_message(raw: bytes) -> ParsedMail:
    message = message_from_bytes(raw, policy=policy.default)
    headers = {key.lower(): str(value) for key, value in message.items()}
    sender = getaddresses([headers.get("from", "")])
    from_name, from_addr = ("", "")
    if sender:
        from_name, from_addr = sender[0][0], sender[0][1]

    try:
        date = parsedate_to_datetime(headers.get("date", "")) if headers.get("date") else None
    except (TypeError, ValueError):
        date = None
    if date is None:
        date = datetime.now(timezone.utc)
    elif date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    message_id = headers.get("message-id", "").strip()
    gm_msgid = headers.get("x-gm-msgid", "").strip()
    if not gm_msgid:
        digest = hashlib.sha1((message_id or raw[:2048]).encode("utf-8", "replace"))
        gm_msgid = f"local-{digest.hexdigest()[:20]}"

    text = _clean(_body_text(message))
    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        text = text.encode("utf-8")[:MAX_BODY_BYTES].decode("utf-8", "ignore")
    return ParsedMail(
        gm_msgid=gm_msgid,
        message_id=message_id,
        thread_id=headers.get("thread-id", ""),
        date=date,
        from_addr=from_addr.lower(),
        from_name=from_name,
        subject=headers.get("subject", ""),
        text=text,
        raw=raw,
        headers=headers,
    )
