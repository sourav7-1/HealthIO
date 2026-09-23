from datetime import UTC, datetime

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="system.heartbeat")
def heartbeat() -> str:
    now = datetime.now(UTC).isoformat()
    log.info("worker_heartbeat", at=now)
    return now
