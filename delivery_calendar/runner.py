"""One run loop, shared by the scheduler, the CLI and the UI's Run button."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from delivery_calendar.calendar_client import GoogleCalendar
from delivery_calendar.config import Settings
from delivery_calendar.imap_source import ImapSource
from delivery_calendar.rules import RuleCache
from delivery_calendar.service import run_once
from delivery_calendar.store import Store

log = logging.getLogger(__name__)


class Runner:
    def __init__(self, settings: Settings, store: Store, rules: RuleCache):
        self.settings = settings
        self.store = store
        self.rules = rules
        self.calendar = GoogleCalendar(settings)
        self.heartbeat = Path(settings.state_dir) / "heartbeat"

    def __call__(self) -> dict:
        started = time.monotonic()
        if not self.settings.mail_user or not self.settings.mail_app_password:
            payload = {"idle": "no mailbox configured; waiting for .env"}
            self.store.record_run("idle", json.dumps(payload))
            log.warning("no mailbox configured: set MAIL_USER and MAIL_APP_PASSWORD")
            self._touch()
            return payload
        source = ImapSource(self.settings, self.store)
        try:
            report = run_once(
                self.settings, self.store, self.rules.rules, source, self.calendar
            )
            status = "error" if report.errors else "ok"
            payload = report.as_dict()
            payload["seconds"] = round(time.monotonic() - started, 1)
            self.store.record_run(status, json.dumps(payload))
            log.info("run finished: %s", payload)
            return payload
        except Exception as exc:  # noqa: BLE001 - a run must never kill the scheduler
            self.store.record_run("error", str(exc))
            log.exception("run failed")
            return {"error": str(exc)}
        finally:
            self._touch()

    def _touch(self) -> None:
        try:
            self.heartbeat.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8"
            )
        except OSError:
            pass
