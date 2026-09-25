"""Records service: health documents in object storage (upload, verify, download).

Upload flow (ARCHITECTURE.md §9):
1. `start_upload` records the document as PENDING_UPLOAD and returns a presigned POST that
   pins the key, content type, size limit and server-side encryption.
2. The browser uploads straight to object storage.
3. `complete_upload` reads the object back, checks size and magic bytes against the
   declared type, rejects PDFs with active content, stores the SHA-256 and runs the
   malware scanner (app/core/malware.py). Infected files are QUARANTINED; files waiting
   for a verdict are PENDING_SCAN. Only CLEAN documents can be downloaded or used.

Who may see a document depends on its type (`DOCUMENT_PERMISSION`), so a doctor whose
consent does not cover visits cannot open a discharge summary through the documents API.
Metadata corrections need a reason and are versioned by the database (migration 0011).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import RecordSource
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.ids import uuid7
from app.core.logging import get_logger
from app.core.malware import MalwareScanner, ScanResult, Verdict
from app.core.storage import Storage
from app.modules.access.permissions import Permission
from app.modules.records.models import DocumentType, HealthDocument, ScanStatus
from app.modules.timeline.versions import change_reason

log = get_logger(__name__)

ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}
# Test and imaging reports: PDF, JPEG or PNG only.
REPORT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
REPORT_DOCUMENTS = {DocumentType.LAB_REPORT, DocumentType.IMAGING_REPORT}

# Besides VIEW_REPORTS (the documents API), the permission for the kind of record a file is.
DOCUMENT_PERMISSION: dict[DocumentType, Permission] = {
    DocumentType.PRESCRIPTION: Permission.VIEW_PRESCRIPTIONS,
    DocumentType.LAB_REPORT: Permission.VIEW_REPORTS,
    DocumentType.IMAGING_REPORT: Permission.VIEW_REPORTS,
    DocumentType.DISCHARGE_SUMMARY: Permission.VIEW_VISITS,
    DocumentType.CONSULTATION_NOTE: Permission.VIEW_VISITS,
    DocumentType.VACCINATION_RECORD: Permission.VIEW_MEDICAL_HISTORY,
    DocumentType.MEDICAL_CERTIFICATE: Permission.VIEW_MEDICAL_HISTORY,
    DocumentType.INSURANCE: Permission.VIEW_REPORTS,
    DocumentType.OTHER: Permission.VIEW_REPORTS,
}


def can_view(permissions: frozenset[Permission], doc_type: DocumentType) -> bool:
    return Permission.VIEW_REPORTS in permissions and DOCUMENT_PERMISSION[doc_type] in permissions


def allowed_types(document_type: DocumentType) -> set[str]:
    return REPORT_TYPES if document_type in REPORT_DOCUMENTS else set(ALLOWED_TYPES)


# PDF features that run code or carry other files; never needed in a medical report.
_PDF_ACTIVE = (b"/JavaScript", b"/JS ", b"/JS(", b"/JS<", b"/Launch", b"/EmbeddedFile", b"/XFA")


def pdf_has_active_content(body: bytes) -> bool:
    return any(marker in body for marker in _PDF_ACTIVE)


def matches_declared_type(content_type: str, head: bytes) -> bool:
    if content_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if content_type == "image/heic":
        return head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1")
    return False


@dataclass(frozen=True)
class StartedUpload:
    document: HealthDocument
    url: str
    fields: dict[str, str]


async def start_upload(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    source: RecordSource,
    document_type: DocumentType,
    content_type: str,
    size_bytes: int,
    filename: str | None,
    title: str | None,
    document_date: date | None,
    visit_id: uuid.UUID | None,
    description: str | None = None,
) -> StartedUpload:
    if content_type not in allowed_types(document_type):
        raise ValidationFailedError(
            "Upload a PDF, JPEG or PNG file."
            if document_type in REPORT_DOCUMENTS
            else "Upload a PDF or an image (JPEG, PNG, WebP or HEIC)."
        )
    if size_bytes <= 0 or size_bytes > settings.upload_max_bytes:
        mb = settings.upload_max_bytes // (1024 * 1024)
        raise ValidationFailedError(f"Files must be smaller than {mb} MB.")
    doc_id = uuid7()
    # The key never contains names or other personal data.
    key = f"{settings.env.value}/patients/{patient_id}/{document_type.value}/{doc_id}"
    doc = HealthDocument(
        id=doc_id,
        patient_id=patient_id,
        document_type=document_type,
        title=title,
        description=description,
        document_date=document_date,
        source=source,
        visit_id=visit_id,
        storage_key=key,
        content_type=content_type,
        original_filename=filename,
        scan_status=ScanStatus.PENDING_UPLOAD,
        created_by=actor,
        updated_by=actor,
    )
    session.add(doc)
    await session.flush()
    post: dict[str, Any] = storage.presign_upload(key, content_type, settings.upload_max_bytes)
    return StartedUpload(doc, post["url"], {k: str(v) for k, v in post["fields"].items()})


async def _get(session: AsyncSession, patient_id: uuid.UUID, doc_id: uuid.UUID) -> HealthDocument:
    doc: HealthDocument | None = await session.scalar(
        select(HealthDocument).where(
            HealthDocument.id == doc_id,
            HealthDocument.patient_id == patient_id,
            HealthDocument.deleted_at.is_(None),
        )
    )
    if doc is None:
        raise NotFoundError()
    return doc


@dataclass(frozen=True)
class CompletedUpload:
    document: HealthDocument
    scan: ScanResult | None  # None: no scanner configured


async def complete_upload(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: uuid.UUID,
    scanner: MalwareScanner | None = None,
) -> CompletedUpload:
    """Validate the uploaded object and decide whether it may be used.

    Raises ValidationFailedError after QUARANTINING the row when the file is not what it
    claims to be, or is infected; the caller commits so the verdict is kept."""
    doc = await _get(session, patient_id, document_id)
    if doc.scan_status != ScanStatus.PENDING_UPLOAD:
        raise ConflictError("This upload was already completed.")
    body = await storage.read(doc.storage_key, settings.upload_max_bytes)
    if body is None:
        raise ValidationFailedError("The file has not been uploaded yet, or it is too large.")
    doc.size_bytes = len(body)
    doc.sha256 = hashlib.sha256(body).hexdigest()
    doc.updated_by = actor
    if not matches_declared_type(doc.content_type, body[:16]):
        doc.scan_status = ScanStatus.QUARANTINED
        await session.flush()
        raise ValidationFailedError("The file content does not match its declared type.")
    if doc.content_type == "application/pdf" and pdf_has_active_content(body):
        doc.scan_status = ScanStatus.QUARANTINED
        await session.flush()
        raise ValidationFailedError(
            "This PDF contains scripts or embedded files and can't be accepted. "
            "Save or print it as a plain PDF and try again."
        )
    result = await scanner.scan(body) if scanner else None
    verdict = apply_verdict(doc, result, required=settings.upload_virus_scan_required)
    await session.flush()
    if verdict == ScanStatus.QUARANTINED:
        raise ValidationFailedError("This file was blocked by the malware scanner.")
    return CompletedUpload(doc, result)


def apply_verdict(doc: HealthDocument, result: ScanResult | None, *, required: bool) -> ScanStatus:
    if result is None:
        doc.scan_status = ScanStatus.PENDING_SCAN if required else ScanStatus.CLEAN
    elif result.verdict == Verdict.CLEAN:
        doc.scan_status = ScanStatus.CLEAN
    elif result.verdict == Verdict.INFECTED:
        doc.scan_status = ScanStatus.QUARANTINED
    else:  # scanner error: never usable until a later scan succeeds
        doc.scan_status = ScanStatus.PENDING_SCAN
    return doc.scan_status


async def scan_pending(
    session: AsyncSession,
    storage: Storage,
    scanner: MalwareScanner,
    settings: Settings,
    *,
    limit: int = 50,
) -> list[tuple[HealthDocument, ScanResult | None]]:
    """Background job: give a verdict to files waiting in PENDING_SCAN."""
    docs = (
        await session.scalars(
            select(HealthDocument)
            .where(
                HealthDocument.scan_status == ScanStatus.PENDING_SCAN,
                HealthDocument.deleted_at.is_(None),
            )
            .order_by(HealthDocument.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    done: list[tuple[HealthDocument, ScanResult | None]] = []
    for doc in docs:
        body = await storage.read(doc.storage_key, settings.upload_max_bytes)
        if body is None or hashlib.sha256(body).hexdigest() != doc.sha256:
            doc.scan_status = ScanStatus.FAILED  # object missing or changed after upload
            log.warning("document_scan_object_mismatch", document_id=str(doc.id))
            done.append((doc, None))
            continue
        result = await scanner.scan(body)
        apply_verdict(doc, result, required=True)
        done.append((doc, result))
    await session.flush()
    return done


async def get_document(
    session: AsyncSession, patient_id: uuid.UUID, document_id: uuid.UUID
) -> HealthDocument:
    return await _get(session, patient_id, document_id)


def uploader_side(source: RecordSource) -> str:
    return "doctor" if source in (RecordSource.DOCTOR, RecordSource.INTEGRATION) else "patient"


async def correct_metadata(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: uuid.UUID,
    actor_side: str,
    reason: str,
    changes: dict[str, object],
) -> HealthDocument:
    """Fix a document's title, type or date. The file itself never changes: a wrong file
    is uploaded again. Only the side that added it (patient/caregiver or doctor) may
    correct it, and the earlier details are kept as a version."""
    doc: HealthDocument | None = await session.scalar(
        select(HealthDocument)
        .where(
            HealthDocument.id == document_id,
            HealthDocument.patient_id == patient_id,
            HealthDocument.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if doc is None:
        raise NotFoundError()
    if uploader_side(doc.source) != actor_side:
        raise ForbiddenError("Only the side that added this document can correct its details.")
    new_type = changes.get("document_type", doc.document_type)
    if new_type in REPORT_DOCUMENTS and doc.content_type not in REPORT_TYPES:
        raise ValidationFailedError("Test reports must be PDF, JPEG or PNG files.")
    async with change_reason(session, reason):
        for key, value in changes.items():
            setattr(doc, key, value)
        doc.updated_by = actor
    return doc


async def list_documents(session: AsyncSession, patient_id: uuid.UUID) -> list[HealthDocument]:
    rows = await session.scalars(
        select(HealthDocument)
        .where(
            HealthDocument.patient_id == patient_id,
            HealthDocument.deleted_at.is_(None),
            HealthDocument.scan_status.in_([ScanStatus.CLEAN, ScanStatus.PENDING_SCAN]),
        )
        .order_by(
            HealthDocument.document_date.desc().nulls_last(), HealthDocument.created_at.desc()
        )
    )
    return list(rows.all())


async def usable_document(
    session: AsyncSession, patient_id: uuid.UUID, document_id: uuid.UUID
) -> HealthDocument:
    doc = await _get(session, patient_id, document_id)
    if doc.scan_status != ScanStatus.CLEAN:
        raise ConflictError("This document is not available yet.")
    return doc


def download_url(storage: Storage, doc: HealthDocument) -> str:
    ext = ALLOWED_TYPES.get(doc.content_type, "bin")
    return storage.presign_download(doc.storage_key, filename=f"document-{doc.id}.{ext}")
