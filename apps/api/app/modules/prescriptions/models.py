"""Prescriptions and their items.

A prescription is editable only while DRAFT. Once issued (by a doctor) or recorded
(an external/uploaded one after human verification), its content and items are frozen by
database triggers (migration 0002). A correction is a new prescription that supersedes
the old one, so the medico-legal history is never lost.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import MealRelation, VerificationStatus
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


class PrescriptionSource(StrEnum):
    DOCTOR_ISSUED = "doctor_issued"  # e-prescription written on the platform
    UPLOADED = "uploaded"  # photo/PDF of a paper prescription (OCR + human verification)
    MANUAL_ENTRY = "manual_entry"  # typed in by the patient or caregiver
    INTEGRATION = "integration"  # e.g. received through ABDM


class PrescriptionStatus(StrEnum):
    DRAFT = "draft"  # editable
    ISSUED = "issued"  # doctor-issued, frozen
    RECORDED = "recorded"  # external prescription verified by a person, frozen
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    ENTERED_IN_ERROR = "entered_in_error"


class Prescription(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "prescriptions"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        patient_scoped_fk(["document_id"], "health_documents"),
        patient_scoped_fk(["supersedes_prescription_id"], "prescriptions"),
        CheckConstraint(
            "source <> 'doctor_issued' OR prescriber_doctor_id IS NOT NULL",
            name="doctor_issued_has_prescriber",
        ),
        CheckConstraint(
            "source <> 'uploaded' OR document_id IS NOT NULL", name="uploaded_has_document"
        ),
        CheckConstraint(
            "status <> 'issued' OR (source = 'doctor_issued' AND issued_at IS NOT NULL)",
            name="issued_is_doctor_issued",
        ),
        CheckConstraint(
            "status <> 'recorded' OR (source <> 'doctor_issued' AND verification_status IN "
            "('patient_verified', 'doctor_verified') AND verified_at IS NOT NULL "
            "AND verified_by IS NOT NULL)",
            name="recorded_is_verified",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_consistent"
        ),
        CheckConstraint(
            "valid_until IS NULL OR prescribed_on IS NULL OR valid_until >= prescribed_on",
            name="valid_until_after_prescribed",
        ),
        CheckConstraint(
            "supersedes_prescription_id IS NULL OR supersedes_prescription_id <> id",
            name="not_self_superseding",
        ),
        Index(
            "uq_prescriptions_supersedes",
            "supersedes_prescription_id",
            unique=True,
            postgresql_where=text("supersedes_prescription_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(revision = 1) = (supersedes_prescription_id IS NULL)", name="revision_chain"
        ),
        CheckConstraint(
            "revision = 1 OR revision_reason IS NOT NULL", name="correction_has_reason"
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256_hex"
        ),
        Index("ix_prescriptions_patient_prescribed", "patient_id", "prescribed_on"),
        Index("ix_prescriptions_prescriber_created", "prescriber_doctor_id", "created_at"),
    )

    source: Mapped[PrescriptionSource] = mapped_column(str_enum(PrescriptionSource), nullable=False)
    status: Mapped[PrescriptionStatus] = mapped_column(
        str_enum(PrescriptionStatus), nullable=False, default=PrescriptionStatus.DRAFT
    )
    prescriber_doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT")
    )
    # For prescriptions from outside the platform, as written on the paper.
    external_prescriber_name: Mapped[str | None] = mapped_column(String(200))
    external_prescriber_registration: Mapped[str | None] = mapped_column(String(64))
    external_facility_name: Mapped[str | None] = mapped_column(String(200))

    visit_id: Mapped[uuid.UUID | None]
    document_id: Mapped[uuid.UUID | None]
    supersedes_prescription_id: Mapped[uuid.UUID | None]

    prescribed_on: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime | None]
    # Diagnosis/advice exactly as written by the prescriber; never inferred.
    diagnosis_as_written: Mapped[str | None] = mapped_column(
        EncryptedString("prescriptions.diagnosis_as_written")
    )
    advice: Mapped[str | None] = mapped_column(EncryptedString("prescriptions.advice"))

    verification_status: Mapped[VerificationStatus] = mapped_column(
        str_enum(VerificationStatus), nullable=False, default=VerificationStatus.UNVERIFIED
    )
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None] = user_fk()

    cancelled_at: Mapped[datetime | None]
    cancelled_by: Mapped[uuid.UUID | None] = user_fk()
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    # Versions of one prescription: 1 is the original; a correction is a new row with
    # revision + 1 that supersedes the previous one (which stays frozen, unchanged).
    revision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    revision_reason: Mapped[str | None] = mapped_column(String(300))
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    follow_up_instructions: Mapped[str | None] = mapped_column(
        EncryptedString("prescriptions.follow_up_instructions")
    )
    # SHA-256 of the canonical issued content; printed on exports for verification.
    content_sha256: Mapped[str | None] = mapped_column(String(64))


class PrescriptionItem(Base, Entity, PatientOwned):
    """One medicine line on a prescription, kept as written plus structured fields."""

    __tablename__ = "prescription_items"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["prescription_id"], "prescriptions", ondelete="CASCADE"),
        UniqueConstraint("prescription_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("dose_amount IS NULL OR dose_amount > 0", name="dose_positive"),
        CheckConstraint("duration_days IS NULL OR duration_days > 0", name="duration_positive"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "times_per_day IS NULL OR times_per_day BETWEEN 1 AND 24",
            name="times_per_day_range",
        ),
        CheckConstraint("NOT is_prn OR prn_reason IS NOT NULL", name="prn_has_reason"),
    )

    prescription_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    drug_name: Mapped[str] = mapped_column(String(200), nullable=False)  # as written
    generic_name: Mapped[str | None] = mapped_column(String(200))
    drug_code: Mapped[str | None] = mapped_column(String(64))  # drug catalogue (Phase 14)
    strength: Mapped[str | None] = mapped_column(String(64))  # e.g. "500 mg"
    dosage_form: Mapped[str | None] = mapped_column(String(64))  # tablet, syrup, …
    route: Mapped[str | None] = mapped_column(String(64))  # oral, topical, …

    dose_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    dose_unit: Mapped[str | None] = mapped_column(String(32))
    frequency_text: Mapped[str | None] = mapped_column(String(64))  # as written: "1-0-1", "BD"
    times_per_day: Mapped[int | None] = mapped_column(SmallInteger)
    meal_relation: Mapped[MealRelation | None] = mapped_column(str_enum(MealRelation))
    duration_days: Mapped[int | None] = mapped_column(SmallInteger)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    is_prn: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    prn_reason: Mapped[str | None] = mapped_column(String(200))
    instructions: Mapped[str | None] = mapped_column(Text)
