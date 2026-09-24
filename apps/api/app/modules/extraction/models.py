"""Prescription scans: an uploaded prescription photo read by AI (or typed in by hand),
reviewed field by field by a person, and only then turned into a prescription.

State machine (AI_SAFETY.md §3), enforced by triggers in migration 0008:

    queued ─► running ─► needs_review ─┬─► verified  (a prescription is created)
                  │                    └─► rejected  (reason recorded)
                  └─► failed

Once the AI result is stored it never changes: corrections live in `review`, next to
the original reading, so what the AI read and what the person confirmed are both kept.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import VerificationStatus
from app.core.models import (
    Base,
    Entity,
    OptimisticLock,
    PatientOwned,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
    user_fk,
)


class PrescriptionScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    FAILED = "failed"


class PrescriptionScanMode(StrEnum):
    AI = "ai"  # read by the vision model, then reviewed
    MANUAL = "manual"  # typed in by a person next to the photo (no AI consent or AI off)


class VerifierRole(StrEnum):
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    DOCTOR = "doctor"


OPEN_STATUSES = (
    PrescriptionScanStatus.QUEUED,
    PrescriptionScanStatus.RUNNING,
    PrescriptionScanStatus.NEEDS_REVIEW,
)


class PrescriptionScan(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "prescription_scans"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["document_id"], "health_documents"),
        patient_scoped_fk(["prescription_id"], "prescriptions"),
        Index(
            "uq_prescription_scans_open_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'needs_review')"),
        ),
        Index("ix_prescription_scans_patient_created", "patient_id", "created_at"),
        CheckConstraint(
            "status <> 'verified' OR (prescription_id IS NOT NULL AND verified_at IS NOT NULL "
            "AND verified_by IS NOT NULL AND verifier_role IS NOT NULL "
            "AND verification_level IN ('patient_verified', 'doctor_verified'))",
            name="verified_is_complete",
        ),
        CheckConstraint(
            "status <> 'rejected' OR rejected_reason IS NOT NULL", name="rejected_has_reason"
        ),
        CheckConstraint("status <> 'failed' OR error_code IS NOT NULL", name="failed_has_code"),
        CheckConstraint(
            "status NOT IN ('needs_review', 'verified', 'rejected') OR "
            "(result IS NOT NULL AND review IS NOT NULL)",
            name="reviewable_has_result",
        ),
        CheckConstraint(
            "mode = 'ai' OR (provider IS NULL AND model IS NULL)", name="manual_has_no_model"
        ),
        CheckConstraint(
            "image_sha256 IS NULL OR image_sha256 ~ '^[0-9a-f]{64}$'", name="image_sha256_hex"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    mode: Mapped[PrescriptionScanMode] = mapped_column(
        str_enum(PrescriptionScanMode, name="scan_mode"), nullable=False
    )
    status: Mapped[PrescriptionScanStatus] = mapped_column(
        str_enum(PrescriptionScanStatus, name="scan_status"),
        nullable=False,
        default=PrescriptionScanStatus.QUEUED,
    )
    requested_by: Mapped[uuid.UUID] = user_fk(nullable=False)

    # --- model metadata (never PHI) ---
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    preprocessing: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    provider_response_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))

    # --- content (PHI, encrypted JSON text) ---
    # The AI reading and its assessment, exactly as produced. Frozen once written.
    result: Mapped[str | None] = mapped_column(EncryptedString("prescription_scans.result"))
    ocr_text: Mapped[str | None] = mapped_column(EncryptedString("prescription_scans.ocr_text"))
    # The person's review: per-field status, final value and who changed it when.
    review: Mapped[str | None] = mapped_column(EncryptedString("prescription_scans.review"))

    # --- outcome ---
    verified_by: Mapped[uuid.UUID | None] = user_fk()
    verified_at: Mapped[datetime | None]
    verifier_role: Mapped[VerifierRole | None] = mapped_column(str_enum(VerifierRole))
    verification_level: Mapped[VerificationStatus | None] = mapped_column(
        str_enum(VerificationStatus, name="scan_verification_level")
    )
    rejected_reason: Mapped[str | None] = mapped_column(String(300))
    prescription_id: Mapped[uuid.UUID | None]

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": self.pipeline_version,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "ocr_engine": self.ocr_engine,
            "preprocessing": list(self.preprocessing or []),
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "attempts": self.attempts,
        }
