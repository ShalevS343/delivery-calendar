"""Hourly schedule inside the container."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from delivery_sync.config import Settings
from delivery_sync.runner import Runner

log = logging.getLogger(__name__)


def start_scheduler(settings: Settings, runner: Runner) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        runner,
        "interval",
        minutes=max(1, settings.poll_interval_minutes),
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
        id="poll",
    )
    scheduler.start()
    log.info("scheduler started: every %s minutes", settings.poll_interval_minutes)
    return scheduler
