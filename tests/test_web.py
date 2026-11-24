from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from delivery_sync.calendar_client import FakeCalendar
from delivery_sync.config import Settings
from delivery_sync.models import Shipment, Status, calendar_uid_for
from delivery_sync.store import Store
from delivery_sync.sync import sync_shipment
from delivery_sync.web import create_app

AUTH = ("admin", "secret")


def _settings(tmp_path) -> Settings:
    return Settings.from_env(
        {
            "DB_PATH": str(tmp_path / "db.sqlite"),
            "RAW_DIR": str(tmp_path / "raw"),
            "RULES_DIR": str(tmp_path / "rules"),
            "UI_PASSWORD": "secret",
            "UI_USER": "admin",
            "DRY_RUN": "0",
        }
    )


def _shipment(**overrides) -> Shipment:
    key = overrides.get("shipment_key", "amazon:TBA123456789012")
    data = {
        "shipment_key": key,
        "calendar_uid": calendar_uid_for(key),
        "status": Status.ready_for_pickup,
        "confidence": 0.9,
        "needs_review": True,
        "merchant": "Amazon",
        "tracking_number": "TBA123456789012",
        "arrival_date": date(2026, 11, 24),
    }
    data.update(overrides)
    return Shipment(**data)


@pytest.fixture()
def ctx(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.migrate()
    calendar = FakeCalendar()
    app = create_app(_settings(tmp_path), store, calendar)
    return TestClient(app), store, calendar


def test_healthz_reports_last_run(ctx):
    client, store, _ = ctx
    store.record_run("ok", '{"fetched": 3}')
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["last_run_status"] == "ok"
    assert body["pending_review"] == 0
    assert body["ok"] is True


def test_requires_auth(ctx):
    client, _, _ = ctx
    assert client.get("/").status_code == 401
    assert client.get("/", auth=AUTH).status_code == 200


def test_accept_clears_review(ctx):
    client, store, _ = ctx
    result = store.upsert_shipment(_shipment())
    response = client.post(f"/shipments/{result.shipment_id}/accept", auth=AUTH, follow_redirects=False)
    assert response.status_code in {302, 303}
    assert store.get_shipment(result.shipment_id).needs_review is False


def test_suppress_deletes_event(ctx):
    client, store, calendar = ctx
    result = store.upsert_shipment(_shipment(needs_review=False))
    sync_shipment(store, calendar, result.shipment)
    assert len(calendar.events) == 1
    event_id = next(iter(calendar.events))

    response = client.post(
        f"/shipments/{result.shipment_id}/suppress", auth=AUTH, follow_redirects=False
    )
    assert response.status_code in {302, 303}
    assert store.get_shipment(result.shipment_id).suppressed is True
    assert event_id not in calendar.events


def test_index_lists_review_items(ctx):
    client, store, _ = ctx
    store.upsert_shipment(_shipment())
    response = client.get("/", auth=AUTH)
    assert response.status_code == 200
    assert "TBA123456789012" in response.text


def test_mail_page_neutralises_links(ctx):
    client, store, _ = ctx
    now = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    store.insert_message(
        "m1",
        "<m1@x>",
        "a@b.c",
        "Subject",
        now,
        verdict="delivery",
        body_text="Click https://evil.example.com/x",
    )
    response = client.get("/mail/m1", auth=AUTH)
    assert response.status_code == 200
    assert 'href="https://evil.example.com/x"' not in response.text


def test_accept_creates_the_event_immediately(ctx):
    client, store, calendar = ctx
    result = store.upsert_shipment(_shipment(needs_review=True))
    assert calendar.events == {}
    client.post(f"/shipments/{result.shipment_id}/accept", auth=AUTH, follow_redirects=False)
    assert len(calendar.events) == 1
