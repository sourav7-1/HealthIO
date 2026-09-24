import uuid
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="system.heartbeat")
def heartbeat() -> str:
    now = datetime.now(UTC).isoformat()
    log.info("worker_heartbeat", at=now)
    return now


@celery_app.task(name="ai.process_prescription_scan", acks_late=True, max_retries=0)
def process_prescription_scan(scan_id: str, patient_id: str) -> str:
    """Run the prescription reading pipeline for a queued scan (idempotent: a scan that is
    no longer queued is left alone)."""
    import asyncio

    return asyncio.run(_process_prescription_scan(uuid.UUID(scan_id), uuid.UUID(patient_id)))


async def _process_prescription_scan(scan_id: uuid.UUID, patient_id: uuid.UUID) -> str:
    from app.ai.ocr import build_ocr
    from app.ai.providers import build_extractor
    from app.core.config import get_settings
    from app.core.db import Database
    from app.core.storage import Storage
    from app.modules.audit.models import AuditOutcome
    from app.modules.audit.service import AuditEvent, record_event
    from app.modules.extraction import service
    from app.modules.extraction.models import PrescriptionScanStatus

    settings = get_settings()
    db = Database(settings)
    try:
        async with db.sessionmaker() as session:
            scan = await service.get(session, patient_id, scan_id)
            if not await service.mark_running(session, scan):
                return scan.status.value
            await session.commit()  # the UI shows "reading…" from here
            await service.process(
                session,
                scan,
                storage=Storage(settings),
                settings=settings,
                extractor=build_extractor(settings),
                ocr_engine=build_ocr(settings.ai_ocr_engine),
            )
            await record_event(
                session,
                AuditEvent(
                    action="prescription_scan.read_by_ai"
                    if scan.status == PrescriptionScanStatus.NEEDS_REVIEW
                    else "prescription_scan.failed",
                    outcome=AuditOutcome.ALLOWED,
                    actor_user_id=None,  # system
                    patient_id=patient_id,
                    resource_type="prescription_scan",
                    resource_id=scan.id,
                    context={
                        "provider": scan.provider or "",
                        "model": scan.model or "",
                        "error_code": scan.error_code or "",
                    },
                ),
            )
            await session.commit()
            log.info("prescription_scan_processed", scan_id=str(scan.id), status=scan.status.value)
            return scan.status.value
    finally:
        await db.dispose()
