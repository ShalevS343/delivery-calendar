"""Review UI: the queue that keeps a strict schema honest."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from delivery_sync.calendar_client import CalendarNotFound, CalendarPort
from delivery_sync.config import Settings
from delivery_sync.store import Store
from delivery_sync.sync import sync_shipment

TEMPLATES = Path(__file__).parent / "templates"


def create_app(
    settings: Settings,
    store: Store,
    calendar: CalendarPort,
    runner: Callable[[], dict] | None = None,
) -> FastAPI:
    app = FastAPI(title="delivery-sync", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    security = HTTPBasic(auto_error=False)

    def require_auth(
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> None:
        if not settings.ui_password:
            return
        if credentials is None:
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="delivery-sync"'},
            )
        user_ok = secrets.compare_digest(credentials.username, settings.ui_user)
        password_ok = secrets.compare_digest(credentials.password, settings.ui_password)
        if not (user_ok and password_ok):
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="delivery-sync"'},
            )

    @app.get("/healthz")
    def healthz() -> dict:
        info = store.run_info()
        age_minutes = None
        if info["last_run_at"]:
            try:
                last = datetime.fromisoformat(info["last_run_at"])
                age_minutes = round(
                    (datetime.now(timezone.utc) - last).total_seconds() / 60, 1
                )
            except ValueError:
                age_minutes = None
        limit = max(150, settings.poll_interval_minutes * 2 + 60)
        fresh = age_minutes is None or age_minutes <= limit
        return {
            "ok": fresh and info["last_run_status"] != "error",
            "last_run_at": info["last_run_at"],
            "age_minutes": age_minutes,
            "last_run_status": info["last_run_status"],
            "last_run_summary": info["last_run_summary"],
            "pending_review": store.count_pending_review(),
            "dry_run": settings.dry_run,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, _: None = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "review": store.pending_review(limit=30),
                "shipments": store.list_shipments(limit=15),
                "run": store.run_info(),
                "dry_run": settings.dry_run,
            },
        )

    @app.get("/shipments", response_class=HTMLResponse)
    def shipments(request: Request, _: None = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "shipments.html",
            {
                "request": request,
                "shipments": store.list_shipments(limit=200, include_suppressed=True),
            },
        )

    @app.get("/shipments/{shipment_id}", response_class=HTMLResponse)
    def shipment_detail(request: Request, shipment_id: int, _: None = Depends(require_auth)):
        shipment = store.get_shipment(shipment_id)
        return templates.TemplateResponse(
            request,
            "shipment.html",
            {
                "request": request,
                "shipment": shipment,
                "shipment_id": shipment_id,
                "meta": store.shipment_event_meta(shipment_id),
                "updates": store.shipment_updates(shipment_id),
            },
        )

    @app.post("/shipments/{shipment_id}/accept")
    def accept(shipment_id: int, _: None = Depends(require_auth)):
        store.accept(shipment_id)
        shipment = store.get_shipment(shipment_id)
        sync_shipment(store, calendar, shipment, dry_run=settings.dry_run)
        return RedirectResponse("/", status_code=303)

    @app.post("/shipments/{shipment_id}/suppress")
    def suppress(shipment_id: int, _: None = Depends(require_auth)):
        meta = store.shipment_event_meta(shipment_id)
        event_id = meta.get("calendar_event_id")
        if event_id and not settings.dry_run:
            try:
                calendar.delete(event_id)
            except CalendarNotFound:
                pass
            store.clear_event(shipment_id)
        store.suppress(shipment_id)
        return RedirectResponse("/shipments", status_code=303)

    @app.post("/shipments/{shipment_id}/resync")
    def resync(shipment_id: int, _: None = Depends(require_auth)):
        shipment = store.get_shipment(shipment_id)
        sync_shipment(store, calendar, shipment, dry_run=settings.dry_run)
        return RedirectResponse(f"/shipments/{shipment_id}", status_code=303)

    @app.get("/mail/{gm_msgid}", response_class=HTMLResponse)
    def mail(request: Request, gm_msgid: str, _: None = Depends(require_auth)):
        message = store.get_message(gm_msgid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return templates.TemplateResponse(
            request, "mail.html", {"request": request, "message": message}
        )

    @app.get("/skipped", response_class=HTMLResponse)
    def skipped(request: Request, _: None = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "skipped.html",
            {
                "request": request,
                "promos": store.messages_by_verdict("promo", limit=50),
                "unrelated": store.messages_by_verdict("unrelated", limit=50),
            },
        )

    @app.post("/run")
    def run(_: None = Depends(require_auth)):
        if runner is None:
            raise HTTPException(status_code=503, detail="runner not configured")
        return runner()

    return app
