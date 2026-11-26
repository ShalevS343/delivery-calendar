"""Calendar port, a Google implementation, and an in-memory fake for tests."""

from __future__ import annotations

import copy
from typing import Protocol


class CalendarError(RuntimeError):
    pass


class CalendarConflict(CalendarError):
    """The iCalUID already exists (Google answers 409)."""


class CalendarNotFound(CalendarError):
    """The event id is gone (404/410)."""


class CalendarPort(Protocol):
    def insert(self, event: dict) -> tuple[str, str]:
        """Return (event_id, etag)."""

    def patch(self, event_id: str, event: dict, etag: str | None) -> tuple[str, str]:
        """Return the new (event_id, etag)."""

    def delete(self, event_id: str) -> None: ...

    def find_by_ical_uid(self, ical_uid: str) -> dict | None: ...


class FakeCalendar:
    """Enough of Google's semantics to test our side: unique iCalUID, etags."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.calls: list[tuple[str, object]] = []
        self.calendar_id = "fake-calendar"
        self._version = 0

    def _next_etag(self) -> str:
        self._version += 1
        return f"v{self._version}"

    def insert(self, event: dict) -> tuple[str, str]:
        self.calls.append(("insert", event.get("iCalUID")))
        uid = event.get("iCalUID")
        for event_id, existing in self.events.items():
            if existing.get("iCalUID") == uid:
                raise CalendarConflict(uid or "")
        self._version += 1
        event_id = f"evt{self._version}"
        stored = copy.deepcopy(event)
        stored["id"] = event_id
        stored["etag"] = self._next_etag()
        self.events[event_id] = stored
        return event_id, stored["etag"]

    def patch(self, event_id: str, event: dict, etag: str | None) -> tuple[str, str]:
        self.calls.append(("patch", event_id))
        if event_id not in self.events:
            raise CalendarNotFound(event_id)
        stored = self.events[event_id]
        if etag and stored.get("etag") != etag:
            raise CalendarConflict(f"etag mismatch on {event_id}")
        stored.update(copy.deepcopy(event))
        stored["etag"] = self._next_etag()
        return event_id, stored["etag"]

    def delete(self, event_id: str) -> None:
        self.calls.append(("delete", event_id))
        if event_id not in self.events:
            raise CalendarNotFound(event_id)
        del self.events[event_id]

    def find_by_ical_uid(self, ical_uid: str) -> dict | None:
        self.calls.append(("find", ical_uid))
        for event_id, event in self.events.items():
            if event.get("iCalUID") == ical_uid:
                return {"id": event_id, **event}
        return None


class GoogleCalendar:
    """Google Calendar API v3, service account (default) or owner OAuth."""

    SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def __init__(self, settings) -> None:
        self.settings = settings
        self.calendar_id = settings.calendar_id
        self._service = None

    def _build(self):
        if self._service is not None:
            return self._service
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if self.settings.calendar_auth == "service_account":
            credentials = service_account.Credentials.from_service_account_file(
                self.settings.service_account_file, scopes=[self.SCOPE]
            )
        else:
            from google.oauth2.credentials import Credentials

            credentials = Credentials.from_authorized_user_file(
                self.settings.token_file, scopes=[self.SCOPE]
            )
        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    @staticmethod
    def _translate(exc: Exception) -> CalendarError:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError):
            status = getattr(exc.resp, "status", 0)
            if status == 409:
                return CalendarConflict(str(exc))
            if status in {404, 410}:
                return CalendarNotFound(str(exc))
        return CalendarError(str(exc))

    def insert(self, event: dict) -> tuple[str, str]:
        try:
            created = (
                self._build()
                .events()
                .insert(calendarId=self.calendar_id, body=event)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - translated below
            raise self._translate(exc) from exc
        return created["id"], created.get("etag", "")

    def patch(self, event_id: str, event: dict, etag: str | None) -> tuple[str, str]:
        headers = {"If-Match": etag} if etag else {}
        try:
            updated = (
                self._build()
                .events()
                .patch(calendarId=self.calendar_id, eventId=event_id, body=event, headers=headers)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        return updated["id"], updated.get("etag", "")

    def delete(self, event_id: str) -> None:
        try:
            self._build().events().delete(
                calendarId=self.calendar_id, eventId=event_id
            ).execute()
        except CalendarNotFound:
            return
        except Exception as exc:  # noqa: BLE001
            error = self._translate(exc)
            if isinstance(error, CalendarNotFound):
                return
            raise error from exc

    def find_by_ical_uid(self, ical_uid: str) -> dict | None:
        try:
            response = (
                self._build()
                .events()
                .list(calendarId=self.calendar_id, iCalUID=ical_uid, maxResults=1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        items = response.get("items") or []
        return items[0] if items else None
