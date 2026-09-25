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
    from app.core.storage import S3Storage
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
                storage=S3Storage(settings),
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


# --- reminder engine --------------------------------------------------------------------------


async def _run_reminders(job: str) -> dict[str, int]:
    from datetime import timedelta

    from app.core.config import get_settings
    from app.core.db import Database
    from app.modules.notifications.push import build_push_sender
    from app.modules.reminders import engine

    settings = get_settings()
    db = Database(settings)
    now = datetime.now(UTC)
    app_url = settings.cors_origins[0] if settings.cors_origins else ""
    try:
        async with db.sessionmaker() as session:
            if job == "materialize":
                created = await engine.materialize_all(
                    session, now=now, horizon=timedelta(hours=settings.reminder_horizon_hours)
                )
                await session.commit()
                return {"created": created}
            sender = build_push_sender(settings)
            if job == "dispatch":
                report = await engine.dispatch_due(session, now=now, sender=sender, app_url=app_url)
            else:
                report = await engine.detect_missed(
                    session, now=now, sender=sender, app_url=app_url
                )
            await session.commit()
            return {"doses": report.doses, "in_app": report.in_app, "push": report.push}
    finally:
        await db.dispose()


@celery_app.task(name="reminders.dispatch_due", acks_late=True)
def reminders_dispatch_due() -> dict[str, int]:
    import asyncio

    result = asyncio.run(_run_reminders("dispatch"))
    log.info("reminders_dispatched", **result)
    return result


@celery_app.task(name="reminders.detect_missed", acks_late=True)
def reminders_detect_missed() -> dict[str, int]:
    import asyncio

    result = asyncio.run(_run_reminders("missed"))
    log.info("reminders_missed_checked", **result)
    return result


@celery_app.task(name="reminders.materialize", acks_late=True)
def reminders_materialize() -> dict[str, int]:
    import asyncio

    result = asyncio.run(_run_reminders("materialize"))
    log.info("reminders_materialized", **result)
    return result


# --- malware scanning ---------------------------------------------------------------------


async def _scan_pending() -> dict[str, int]:
    from app.core.config import get_settings
    from app.core.db import Database
    from app.core.malware import build_scanner
    from app.core.storage import S3Storage
    from app.modules.audit.models import AuditOutcome
    from app.modules.audit.service import AuditEvent, record_event
    from app.modules.records import service

    settings = get_settings()
    scanner = build_scanner(settings)
    if scanner is None:
        return {"scanned": 0}
    db = Database(settings)
    try:
        async with db.sessionmaker() as session:
            done = await service.scan_pending(session, S3Storage(settings), scanner, settings)
            for doc, result in done:
                await record_event(
                    session,
                    AuditEvent(
                        action="document.scanned",
                        outcome=AuditOutcome.ALLOWED,
                        actor_user_id=None,  # system
                        patient_id=doc.patient_id,
                        resource_type="health_document",
                        resource_id=doc.id,
                        context={
                            "scan_status": doc.scan_status.value,
                            "verdict": result.verdict.value if result else "object_mismatch",
                        },
                    ),
                )
            await session.commit()
            return {"scanned": len(done)}
    finally:
        await db.dispose()


@celery_app.task(name="records.scan_pending", acks_late=True)
def records_scan_pending() -> dict[str, int]:
    """Give a malware verdict to uploads waiting in PENDING_SCAN (scanner errors retry)."""
    import asyncio

    result = asyncio.run(_scan_pending())
    log.info("documents_scanned", **result)
    return result
