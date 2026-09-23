"""Care team: doctor profiles and doctor ↔ patient relationships (many-to-many)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, Entity, OptimisticLock, PatientOwned, str_enum, user_fk


class DoctorVerificationStatus(StrEnum):
    UNVERIFIED = "unverified"  # signed up, documents not yet submitted
    PENDING = "pending"  # submitted, waiting for admin review
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class DoctorProfile(Base, Entity, OptimisticLock):
    """Professional profile for any specialty. Only VERIFIED doctors may be linked to
    patients or prescribe (enforced by the access policy engine)."""

    __tablename__ = "doctor_profiles"
    __table_args__ = (
        Index("uq_doctor_profiles_user", "user_id", unique=True),
        # A registration number can belong to only one live (pending/verified) profile.
        Index(
            "uq_doctor_profiles_registration_live",
            "registration_council",
            "registration_number",
            unique=True,
            postgresql_where=text("verification_status IN ('pending', 'verified')"),
        ),
        CheckConstraint(
            "verification_status <> 'verified' OR (verified_at IS NOT NULL "
            "AND verified_by IS NOT NULL)",
            name="verified_has_verifier",
        ),
        CheckConstraint(
            "registration_year IS NULL OR registration_year BETWEEN 1900 AND 2200",
            name="registration_year_range",
        ),
        Index("ix_doctor_profiles_verification_status", "verification_status"),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Registration with the National Medical Commission or a State Medical Council.
    registration_council: Mapped[str | None] = mapped_column(String(120))
    registration_number: Mapped[str | None] = mapped_column(String(64))
    registration_year: Mapped[int | None] = mapped_column(SmallInteger)
    primary_specialty: Mapped[str | None] = mapped_column(String(120))
    additional_specialties: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=list, server_default="{}"
    )
    qualifications: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=list, server_default="{}"
    )
    practice_name: Mapped[str | None] = mapped_column(String(200))
    practice_address: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[DoctorVerificationStatus] = mapped_column(
        str_enum(DoctorVerificationStatus),
        nullable=False,
        default=DoctorVerificationStatus.UNVERIFIED,
    )
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None] = user_fk()
    verification_notes: Mapped[str | None] = mapped_column(Text)


class RelationshipStatus(StrEnum):
    PENDING_PATIENT = "pending_patient"  # doctor invited; patient must accept (consent)
    PENDING_DOCTOR = "pending_doctor"  # patient requested; doctor must accept
    ACTIVE = "active"
    DECLINED = "declined"
    ENDED = "ended"


class DoctorPatientRelationship(Base, Entity, PatientOwned):
    """Links a doctor to a patient. Access to the patient's data also needs consent.

    Rows are never deleted: ending a relationship keeps the history of who treated whom.
    """

    __tablename__ = "doctor_patient_relationships"
    __table_args__ = (
        Index(
            "uq_doctor_patient_relationships_open",
            "doctor_id",
            "patient_id",
            unique=True,
            postgresql_where=text("status IN ('pending_patient', 'pending_doctor', 'active')"),
        ),
        CheckConstraint(
            "(status = 'active') = (started_at IS NOT NULL AND ended_at IS NULL)",
            name="active_has_start_no_end",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="end_after_start"),
        Index("ix_doctor_patient_relationships_doctor_status", "doctor_id", "status"),
    )

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RelationshipStatus] = mapped_column(
        str_enum(RelationshipStatus, name="relationship_status"), nullable=False
    )
    is_primary_doctor: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    initiated_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    ended_by: Mapped[uuid.UUID | None] = user_fk()
    end_reason: Mapped[str | None] = mapped_column(String(200))
