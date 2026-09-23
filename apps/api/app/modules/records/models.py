"""Records: metadata for files in object storage (the bytes never live in PostgreSQL)."""

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, Date, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import RecordSource
from app.core.models import (
    Base,
    Entity,
    PatientOwned,
    SoftDelete,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
)


class DocumentType(StrEnum):
    PRESCRIPTION = "prescription"
    LAB_REPORT = "lab_report"
    IMAGING_REPORT = "imaging_report"
    DISCHARGE_SUMMARY = "discharge_summary"
    CONSULTATION_NOTE = "consultation_note"
    VACCINATION_RECORD = "vaccination_record"
    MEDICAL_CERTIFICATE = "medical_certificate"
    INSURANCE = "insurance"
    OTHER = "other"


class ScanStatus(StrEnum):
    """Files are unusable until the antivirus scan marks them CLEAN."""

    PENDING_UPLOAD = "pending_upload"  # presigned URL issued, upload not confirmed
    PENDING_SCAN = "pending_scan"
    CLEAN = "clean"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class HealthDocument(Base, Entity, PatientOwned, SoftDelete):
    __tablename__ = "health_documents"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        Index("uq_health_documents_storage_key", "storage_key", unique=True),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="size_positive"),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint(
            "content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/heic', "
            "'image/webp')",
            name="allowed_content_type",
        ),
        CheckConstraint(
            "scan_status NOT IN ('clean', 'quarantined') OR (sha256 IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            name="scanned_has_digest",
        ),
        Index(
            "ix_health_documents_patient_type_date",
            "patient_id",
            "document_type",
            "document_date",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    document_type: Mapped[DocumentType] = mapped_column(str_enum(DocumentType), nullable=False)
    # Titles can reveal diagnoses ("HIV test"), so they are encrypted like descriptions.
    title: Mapped[str | None] = mapped_column(EncryptedString("health_documents.title"))
    description: Mapped[str | None] = mapped_column(EncryptedString("health_documents.description"))
    document_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[RecordSource] = mapped_column(str_enum(RecordSource), nullable=False)
    visit_id: Mapped[uuid.UUID | None]

    # Object storage. The key never contains names or other PHI (see ARCHITECTURE.md §9).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(
        EncryptedString("health_documents.original_filename")
    )
    scan_status: Mapped[ScanStatus] = mapped_column(
        str_enum(ScanStatus), nullable=False, default=ScanStatus.PENDING_UPLOAD
    )
