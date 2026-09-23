"""Records service: health documents in object storage (upload, verify, download).

Upload flow (ARCHITECTURE.md §9):
1. `start_upload` records the document as PENDING_UPLOAD and returns a presigned POST that
   pins the key, content type, size limit and server-side encryption.
2. The browser uploads straight to object storage.
3. `complete_upload` reads the object back, checks size and magic bytes against the
   declared type, stores the SHA-256, and marks it CLEAN (or PENDING_SCAN when an
   antivirus verdict is required). Only CLEAN documents can be downloaded.
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
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.ids import uuid7
from app.core.storage import Storage
from app.modules.records.models import DocumentType, HealthDocument, ScanStatus

ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}


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
) -> StartedUpload:
    if content_type not in ALLOWED_TYPES:
        raise ValidationFailedError("Upload a PDF or an image (JPEG, PNG, WebP or HEIC).")
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


async def complete_upload(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: uuid.UUID,
) -> HealthDocument:
    doc = await _get(session, patient_id, document_id)
    if doc.scan_status != ScanStatus.PENDING_UPLOAD:
        raise ConflictError("This upload was already completed.")
    body = await storage.read(doc.storage_key, settings.upload_max_bytes)
    if body is None:
        raise ValidationFailedError("The file has not been uploaded yet, or it is too large.")
    doc.size_bytes = len(body)
    doc.sha256 = hashlib.sha256(body).hexdigest()
    if not matches_declared_type(doc.content_type, body[:16]):
        doc.scan_status = ScanStatus.QUARANTINED
        doc.updated_by = actor
        await session.flush()
        raise ValidationFailedError("The file content does not match its declared type.")
    doc.scan_status = (
        ScanStatus.PENDING_SCAN if settings.upload_virus_scan_required else ScanStatus.CLEAN
    )
    doc.updated_by = actor
    await session.flush()
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
