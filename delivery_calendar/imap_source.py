"""IMAP source with UID-based incremental reads.

Gmail's IMAP presents `X-GM-MSGID` (stable across folders) and `X-GM-RAW`
(Gmail search syntax), both of which make the fetch cheap without the Gmail API.
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass
from datetime import date, timedelta

from delivery_calendar.config import Settings
from delivery_calendar.store import Store


@dataclass(frozen=True)
class RawMail:
    uid: int
    raw: bytes


def plan_fetch(
    *, last_uid: int | None, uidvalidity_changed: bool, available: list[int]
) -> list[int]:
    """Which UIDs to fetch, given what the server has and where we stopped."""
    if uidvalidity_changed or last_uid is None:
        return sorted(available)
    return sorted(uid for uid in available if uid > last_uid)


def _gmail_search(settings: Settings) -> str:
    query = settings.imap_search.strip()
    return query


class ImapSource:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def _connect(self) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)
        client.login(self.settings.mail_user, self.settings.mail_app_password)
        return client

    def fetch_new(self, limit: int = 50) -> list[RawMail]:
        client = self._connect()
        try:
            status, _ = client.select(self.settings.mail_folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"cannot select {self.settings.mail_folder}")
            _, values = client.response("UIDVALIDITY")
            uidvalidity = (values[0] or b"").decode() if values else ""
            stored_validity = self.store.get_setting("uidvalidity")
            stored_uid = self.store.get_setting("last_uid")
            last_uid = int(stored_uid) if stored_uid else None
            changed = stored_validity is not None and stored_validity != uidvalidity

            uids = self._search(client)
            if last_uid is not None and not changed:
                uids = [uid for uid in uids if uid > last_uid]
            uids = uids[:limit]

            mails: list[RawMail] = []
            for uid in uids:
                raw = self._fetch_one(client, uid)
                if raw is not None:
                    mails.append(RawMail(uid=uid, raw=raw))

            if uids:
                self.store.set_setting("last_uid", str(max(uids)))
            self.store.set_setting("uidvalidity", uidvalidity)
            return mails
        finally:
            try:
                client.logout()
            except imaplib.IMAP4.error:
                pass

    def _search(self, client: imaplib.IMAP4_SSL) -> list[int]:
        since = (date.today() - timedelta(days=self.settings.lookback_days)).strftime(
            "%d-%b-%Y"
        )
        query = _gmail_search(self.settings)
        if query:
            # Gmail extension: full search syntax, including category filters.
            status, data = client.uid("SEARCH", "X-GM-RAW", f'"{query}"')
            if status != "OK":
                status, data = client.uid("SEARCH", "SINCE", since)
        else:
            status, data = client.uid("SEARCH", "SINCE", since)
        if status != "OK" or not data or data[0] is None:
            return []
        return [int(value) for value in data[0].split()]

    def _fetch_one(self, client: imaplib.IMAP4_SSL, uid: int) -> bytes | None:
        status, data = client.uid("FETCH", str(uid), "(X-GM-MSGID RFC822)")
        if status != "OK":
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2:
                return part[1]
        return None


def uid_parts(raw_fetch_header: bytes) -> tuple[str, str]:
    """Parse `(UID n X-GM-MSGID m)` response fragments; kept for diagnostics."""
    text = raw_fetch_header.decode("latin-1", "ignore")
    uid = re.search(r"UID (\d+)", text)
    gm = re.search(r"X-GM-MSGID (\d+)", text)
    return (uid.group(1) if uid else "", gm.group(1) if gm else "")
