"""SQLite persistence, shipment identity, merging rules and status precedence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from delivery_calendar.models import (
    ARRIVAL_STATUSES,
    STATUS_RANK,
    TERMINAL_STATUSES,
    Shipment,
    Status,
)

_EXTRA_COLUMNS = {
    "id",
    "calendar_event_id",
    "calendar_id",
    "event_etag",
    "event_fingerprint",
    "first_seen_at",
    "updated_at",
    "delivered_seen_at",
    "suppressed_at",
}

_JSON_FIELDS = {"items", "review_reasons"}
_DATE_FIELDS = {"eta_date", "arrival_date", "pickup_deadline"}
_DATETIME_FIELDS = {"delivered_at"}
_BOOL_FIELDS = {"needs_review", "suppressed"}

_TERMINAL_MERGE_WHITELIST = {
    "location_text",
    "location_kind",
    "alt_location_text",
    "alt_location_kind",
    "location_lat",
    "location_lon",
    "location_confidence",
    "maps_url",
    "action_url",
    "action_kind",
    "action_label",
    "order_number",
    "package_title",
}


def _column_type(name: str) -> str:
    if name in _JSON_FIELDS:
        return "TEXT"
    if name in _BOOL_FIELDS:
        return "INTEGER"
    if name in _DATE_FIELDS or name in _DATETIME_FIELDS:
        return "TEXT"
    return "TEXT"


def _serialise(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return json.dumps(
            [item.model_dump() if hasattr(item, "model_dump") else item for item in value],
            ensure_ascii=False,
        )
    if hasattr(value, "value") and not isinstance(value, str):
        return value.value
    return value


def _deserialise(name: str, value):
    if value is None:
        return None
    if name in _JSON_FIELDS:
        loaded = json.loads(value)
        return loaded
    if name in _DATE_FIELDS:
        return date.fromisoformat(value)
    if name in _DATETIME_FIELDS:
        return datetime.fromisoformat(value)
    if name in _BOOL_FIELDS:
        return bool(value)
    return value


@dataclass
class UpsertResult:
    shipment_id: int
    created: bool
    changed_fields: list[str]
    shipment: Shipment


def apply_update(
    existing: Shipment, incoming: Shipment, mail_date: date
) -> tuple[Shipment, list[str]]:
    """Merge one mail's view of a shipment into the stored one.

    Ordering is by the mail's own date, never by fetch order, and a delivered or
    cancelled shipment is frozen: nothing may drag it back to "in transit".
    """
    if existing.status is Status.cancelled:
        return existing, []

    merged = existing.model_copy(deep=True)

    if existing.status in TERMINAL_STATUSES:
        for name in _TERMINAL_MERGE_WHITELIST:
            if getattr(merged, name) in (None, "") and getattr(incoming, name) not in (
                None,
                "",
            ):
                setattr(merged, name, getattr(incoming, name))
        changed = _changed_fields(existing, merged)
        return merged, changed

    existing_rank = STATUS_RANK[existing.status]
    incoming_rank = STATUS_RANK[incoming.status]
    new_status: Status | None = None

    if incoming.status is Status.cancelled:
        new_status = Status.cancelled
    elif incoming.status in {Status.delayed, Status.exception}:
        if existing_rank <= 3:
            new_status = incoming.status
    elif incoming_rank > 0 and incoming_rank >= existing_rank:
        new_status = incoming.status

    if new_status is not None:
        merged.status = new_status

    for name, field_info in Shipment.model_fields.items():
        if name in {"shipment_key", "calendar_uid", "status"}:
            continue
        value = getattr(incoming, name)
        if value in (None, "", []) and name not in {"items"}:
            continue
        if name in {"eta_date", "arrival_date", "delivered_at", "pickup_deadline"}:
            if incoming_rank < existing_rank:
                continue
        setattr(merged, name, value)

    if merged.arrival_date is None and incoming.arrival_date is not None:
        merged.arrival_date = incoming.arrival_date
    if merged.status in {Status.delivered} and merged.arrival_date is None:
        merged.arrival_date = mail_date

    return merged, _changed_fields(existing, merged)


def _changed_fields(before: Shipment, after: Shipment) -> list[str]:
    return [
        name
        for name in Shipment.model_fields
        if getattr(before, name) != getattr(after, name)
    ]


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # ---------------------------------------------------------------- schema
    def migrate(self) -> None:
        columns = ", ".join(
            f"{name} {_column_type(name)}" for name in Shipment.model_fields
        )
        self.conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {columns},
                calendar_event_id TEXT,
                calendar_id TEXT,
                event_etag TEXT,
                event_fingerprint TEXT,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivered_seen_at TEXT,
                suppressed_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shipments_key
                ON shipments(shipment_key);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shipments_uid
                ON shipments(calendar_uid);
            CREATE INDEX IF NOT EXISTS idx_shipments_tracking
                ON shipments(tracking_number);
            CREATE INDEX IF NOT EXISTS idx_shipments_order
                ON shipments(merchant, order_number);
            CREATE INDEX IF NOT EXISTS idx_shipments_status
                ON shipments(status);
            CREATE TABLE IF NOT EXISTS messages (
                gm_msgid TEXT PRIMARY KEY,
                message_id TEXT,
                thread_id TEXT,
                uid INTEGER,
                uidvalidity TEXT,
                received_at TEXT NOT NULL,
                from_addr TEXT,
                from_name TEXT,
                subject TEXT,
                snippet TEXT,
                raw_path TEXT,
                body_text TEXT,
                verdict TEXT,
                verdict_rule TEXT,
                verdict_override_at TEXT,
                parser TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_shipments (
                gm_msgid TEXT NOT NULL REFERENCES messages(gm_msgid),
                shipment_id INTEGER NOT NULL REFERENCES shipments(id),
                role TEXT NOT NULL DEFAULT 'update',
                PRIMARY KEY (gm_msgid, shipment_id)
            );
            CREATE TABLE IF NOT EXISTS shipment_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL REFERENCES shipments(id),
                gm_msgid TEXT,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        row = self.conn.execute("SELECT count(*) AS n FROM schema_version").fetchone()
        if row["n"] == 0:
            self.conn.execute("INSERT INTO schema_version(version) VALUES (1)")

    def shipment_columns(self) -> set[str]:
        rows = self.conn.execute("PRAGMA table_info(shipments)").fetchall()
        return {row["name"] for row in rows} - _EXTRA_COLUMNS

    # -------------------------------------------------------------- messages
    def insert_message(
        self,
        gm_msgid: str,
        message_id: str,
        from_addr: str,
        subject: str,
        received_at: datetime,
        *,
        verdict: str = "delivery",
        verdict_rule: str = "",
        snippet: str = "",
        raw_path: str | None = None,
        body_text: str = "",
        uid: int | None = None,
        uidvalidity: str | None = None,
        thread_id: str = "",
        parser: str = "regex",
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO messages(
                gm_msgid, message_id, thread_id, uid, uidvalidity, received_at,
                from_addr, from_name, subject, snippet, raw_path, body_text,
                verdict, verdict_rule, parser, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                gm_msgid,
                message_id,
                thread_id,
                uid,
                uidvalidity,
                received_at.isoformat(),
                from_addr,
                "",
                subject,
                snippet,
                raw_path,
                body_text,
                verdict,
                verdict_rule,
                parser,
                now,
            ),
        )
        return cursor.rowcount > 0

    def message_seen(self, gm_msgid: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE gm_msgid = ?", (gm_msgid,)
        ).fetchone()
        return row is not None

    def message_count(self) -> int:
        return self.conn.execute("SELECT count(*) AS n FROM messages").fetchone()["n"]

    def link_message(self, gm_msgid: str, shipment_id: int, role: str = "update") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO message_shipments(gm_msgid, shipment_id, role) VALUES (?,?,?)",
            (gm_msgid, shipment_id, role),
        )

    def mark_verdict(
        self, gm_msgid: str, verdict: str, rule: str, *, override: bool = False
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE messages SET verdict = ?, verdict_rule = ?,
               verdict_override_at = CASE WHEN ? THEN ? ELSE verdict_override_at END
               WHERE gm_msgid = ?""",
            (verdict, rule, int(override), now, gm_msgid),
        )

    # -------------------------------------------------------------- shipments
    def upsert_shipment(
        self, shipment: Shipment, *, now: datetime | None = None
    ) -> UpsertResult:
        now = now or datetime.now(timezone.utc)
        existing_id = self._find_existing(shipment)
        if existing_id is None:
            shipment_id = self._insert(shipment, now)
            return UpsertResult(shipment_id, True, [], shipment)

        existing = self.get_shipment(existing_id)
        merged, changed = apply_update(
            existing, shipment, mail_date=now.date()
        )
        if existing.tracking_number is None and shipment.tracking_number:
            merged.shipment_key = shipment.shipment_key
        self._write(merged, shipment_id=existing_id, now=now)
        if changed:
            self._record_updates(existing_id, None, existing, merged, now)
        return UpsertResult(existing_id, False, changed, merged)

    def _find_existing(self, shipment: Shipment) -> int | None:
        if shipment.tracking_number:
            row = self.conn.execute(
                "SELECT id FROM shipments WHERE tracking_number = ? LIMIT 1",
                (shipment.tracking_number,),
            ).fetchone()
            if row:
                return int(row["id"])
            if shipment.merchant and shipment.order_number:
                row = self.conn.execute(
                    """SELECT id FROM shipments
                       WHERE merchant = ? AND order_number = ?
                         AND tracking_number IS NULL LIMIT 1""",
                    (shipment.merchant, shipment.order_number),
                ).fetchone()
                if row:
                    return int(row["id"])
            return None
        if shipment.merchant and shipment.order_number:
            row = self.conn.execute(
                """SELECT id FROM shipments
                   WHERE merchant = ? AND order_number = ? AND tracking_number IS NULL
                   LIMIT 1""",
                (shipment.merchant, shipment.order_number),
            ).fetchone()
            if row:
                return int(row["id"])
        if shipment.shipment_key.startswith("hash:"):
            row = self.conn.execute(
                "SELECT id FROM shipments WHERE shipment_key = ? LIMIT 1",
                (shipment.shipment_key,),
            ).fetchone()
            if row:
                return int(row["id"])
        return None

    def _insert(self, shipment: Shipment, now: datetime) -> int:
        names = list(Shipment.model_fields)
        placeholders = ",".join("?" for _ in names)
        columns = ",".join(names)
        values = [_serialise(getattr(shipment, name)) for name in names]
        cursor = self.conn.execute(
            f"""INSERT INTO shipments({columns}, first_seen_at, updated_at)
                VALUES ({placeholders}, ?, ?)""",
            (*values, now.isoformat(), now.isoformat()),
        )
        return int(cursor.lastrowid)

    def _write(self, shipment: Shipment, *, shipment_id: int, now: datetime) -> None:
        names = list(Shipment.model_fields)
        values = [_serialise(getattr(shipment, name)) for name in names]
        try:
            assignments = ",".join(f"{name} = ?" for name in names)
            self.conn.execute(
                f"UPDATE shipments SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, now.isoformat(), shipment_id),
            )
        except sqlite3.IntegrityError:
            # The new key collides with another row (two packages of one order).
            # Keep the row and its event identity; identity is calendar_uid, not key.
            kept = [(n, v) for n, v in zip(names, values) if n != "shipment_key"]
            assignments = ",".join(f"{name} = ?" for name, _ in kept)
            self.conn.execute(
                f"UPDATE shipments SET {assignments}, updated_at = ? WHERE id = ?",
                (*[v for _, v in kept], now.isoformat(), shipment_id),
            )

    def _record_updates(
        self,
        shipment_id: int,
        gm_msgid: str | None,
        before: Shipment,
        after: Shipment,
        now: datetime,
    ) -> None:
        for name in Shipment.model_fields:
            old, new = getattr(before, name), getattr(after, name)
            if old == new:
                continue
            self.conn.execute(
                """INSERT INTO shipment_updates(shipment_id, gm_msgid, field, old_value,
                   new_value, at) VALUES (?,?,?,?,?,?)""",
                (
                    shipment_id,
                    gm_msgid,
                    name,
                    _as_text(old),
                    _as_text(new),
                    now.isoformat(),
                ),
            )

    def shipment_id_for_uid(self, calendar_uid: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM shipments WHERE calendar_uid = ?", (calendar_uid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"shipment {calendar_uid} not found")
        return int(row["id"])

    def get_shipment(self, shipment_id: int) -> Shipment:
        row = self.conn.execute(
            "SELECT * FROM shipments WHERE id = ?", (shipment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"shipment {shipment_id} not found")
        return _row_to_shipment(row)

    def shipment_event_meta(self, shipment_id: int) -> dict:
        row = self.conn.execute(
            """SELECT calendar_event_id, calendar_id, event_etag, event_fingerprint
               FROM shipments WHERE id = ?""",
            (shipment_id,),
        ).fetchone()
        return dict(row) if row else {}

    def set_event_meta(
        self,
        shipment_id: int,
        event_id: str | None,
        etag: str | None,
        fingerprint: str | None,
        calendar_id: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE shipments SET calendar_event_id = ?, event_etag = ?,
               event_fingerprint = ?, calendar_id = ? WHERE id = ?""",
            (event_id, etag, fingerprint, calendar_id, shipment_id),
        )

    def count_shipments(self) -> int:
        return self.conn.execute("SELECT count(*) AS n FROM shipments").fetchone()["n"]

    def shipments_needing_sync(self) -> list[Shipment]:
        rows = self.conn.execute(
            """SELECT * FROM shipments
               WHERE suppressed = 0 AND status IN (?, ?)""",
            tuple(status.value for status in ARRIVAL_STATUSES),
        ).fetchall()
        return [_row_to_shipment(row) for row in rows]

    def pending_review(self, limit: int = 50) -> list[tuple[int, Shipment]]:
        rows = self.conn.execute(
            """SELECT * FROM shipments WHERE needs_review = 1 AND suppressed = 0
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(int(row["id"]), _row_to_shipment(row)) for row in rows]

    def suppress(self, shipment_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE shipments SET suppressed = 1, suppressed_at = ? WHERE id = ?",
            (now, shipment_id),
        )

    def accept(self, shipment_id: int) -> None:
        """A human confirmed the record: clear review and mark the source."""
        self.conn.execute(
            "UPDATE shipments SET needs_review = 0, review_reasons = '[]',"
            " source = 'manual' WHERE id = ?",
            (shipment_id,),
        )

    def unsuppress(self, shipment_id: int) -> None:
        self.conn.execute(
            "UPDATE shipments SET suppressed = 0, suppressed_at = NULL WHERE id = ?",
            (shipment_id,),
        )

    def clear_event(self, shipment_id: int) -> None:
        self.set_event_meta(shipment_id, None, None, None)

    def due_for_cleanup(self, today: date, retention_days: int) -> list[Shipment]:
        if retention_days <= 0:
            return []
        rows = self.conn.execute(
            """SELECT * FROM shipments
               WHERE calendar_event_id IS NOT NULL AND suppressed = 0
                 AND status IN (?, ?)""",
            tuple(status.value for status in ARRIVAL_STATUSES),
        ).fetchall()
        due = []
        for row in rows:
            shipment = _row_to_shipment(row)
            anchor = shipment.pickup_deadline or shipment.arrival_date
            if anchor is None:
                continue
            window = retention_days if shipment.status is Status.delivered else retention_days + 7
            if (today - anchor).days > window:
                due.append(shipment)
        return due

    def shipment_updates(self, shipment_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM shipment_updates WHERE shipment_id = ? ORDER BY id",
            (shipment_id,),
        ).fetchall()

    def list_shipments(
        self, limit: int = 100, include_suppressed: bool = False
    ) -> list[tuple[int, Shipment]]:
        clause = "" if include_suppressed else "WHERE suppressed = 0"
        rows = self.conn.execute(
            f"SELECT * FROM shipments {clause} ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(row["id"]), _row_to_shipment(row)) for row in rows]

    def get_message(self, gm_msgid: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE gm_msgid = ?", (gm_msgid,)
        ).fetchone()
        return dict(row) if row else None

    def messages_by_verdict(self, verdict: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT gm_msgid, received_at, from_addr, subject, verdict, verdict_rule "
            "FROM messages WHERE verdict = ? ORDER BY received_at DESC LIMIT ?",
            (verdict, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_pending_review(self) -> int:
        return self.conn.execute(
            "SELECT count(*) AS n FROM shipments WHERE needs_review = 1 AND suppressed = 0"
        ).fetchone()["n"]

    def record_run(self, status: str, summary: str) -> None:
        self.set_setting("last_run_at", datetime.now(timezone.utc).isoformat())
        self.set_setting("last_run_status", status)
        self.set_setting("last_run_summary", summary[:2000])

    def run_info(self) -> dict:
        return {
            "last_run_at": self.get_setting("last_run_at"),
            "last_run_status": self.get_setting("last_run_status"),
            "last_run_summary": self.get_setting("last_run_summary"),
        }

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps(
            [v.model_dump() if hasattr(v, "model_dump") else v for v in value],
            ensure_ascii=False,
        )
    return str(value)


def _row_to_shipment(row: sqlite3.Row) -> Shipment:
    data = {
        name: _deserialise(name, row[name]) for name in Shipment.model_fields
    }
    return Shipment(**data)
