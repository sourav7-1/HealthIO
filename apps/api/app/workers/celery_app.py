"""Celery app for background work: reminders, OCR jobs, notifications, rollups.

Run a worker: `uv run celery -A app.workers.celery_app worker -l info`
Run beat:     `uv run celery -A app.workers.celery_app beat -l info`
"""

from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)

celery_app = Celery(
    "healthio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # redeliver if a worker dies mid-task; tasks must be idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    worker_hijack_root_logger=False,
    beat_schedule={
        "heartbeat": {"task": "system.heartbeat", "schedule": 60.0},
        # Reminder engine (app/modules/reminders/engine.py). Each job is idempotent and
        # safe on several workers (SKIP LOCKED + idempotency keys); a late or repeated run
        # never sends a reminder twice. Short expiry: a stale queued run is dropped.
        "reminders-dispatch-due": {
            "task": "reminders.dispatch_due",
            "schedule": 60.0,
            "options": {"expires": 55},
        },
        "reminders-detect-missed": {
            "task": "reminders.detect_missed",
            "schedule": 300.0,
            "options": {"expires": 280},
        },
        # Malware verdicts for uploads that could not be scanned inline (no-op without clamd).
        "records-scan-pending": {
            "task": "records.scan_pending",
            "schedule": 120.0,
            "options": {"expires": 110},
        },
        # Delete assistant chats past their owner's retention period.
        "assistant-purge-expired": {
            "task": "assistant.purge_expired",
            "schedule": 3600.0,
            "options": {"expires": 3500},
        },
        "reminders-materialize": {
            "task": "reminders.materialize",
            "schedule": 900.0,
            "options": {"expires": 850},
        },
    },
)
