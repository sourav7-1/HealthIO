"""Clinical: problem list, allergies, history, doctor visits and clinical notes.

History is preserved: conditions and allergies change status (resolved, refuted,
entered_in_error) rather than being deleted, and signed notes are immutable. A
correction is a new note that supersedes the old one (DB trigger, see migration 0002).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import DatePrecision, RecordSource
from app.core.models import (
    Base,
    Entity,
    OptimisticLock,
    PatientOwned,
    SoftDelete,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
    user_fk,
)

# --- conditions ----------------------------------------------------------------------


class ConditionClinicalStatus(StrEnum):
    ACTIVE = "active"
    RECURRENCE = "recurrence"
    RELAPSE = "relapse"
    INACTIVE = "inactive"
    REMISSION = "remission"
    RESOLVED = "resolved"


class ConditionVerificationStatus(StrEnum):
    """As documented by the recording person; the platform never infers a diagnosis."""

    UNCONFIRMED = "unconfirmed"  # e.g. patient-reported, not confirmed by a doctor
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    ENTERED_IN_ERROR = "entered_in_error"


class Severity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class MedicalCondition(Base, Entity, PatientOwned, SoftDelete, OptimisticLock):
    __tablename__ = "medical_conditions"
    __table_args__ = (
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        CheckConstraint(
            "abatement_date IS NULL OR onset_date IS NULL OR abatement_date >= onset_date",
            name="abatement_after_onset",
        ),
        CheckConstraint(
            "icd10_code IS NULL OR icd10_code ~ '^[A-Z][0-9][0-9A-Z](\\.[0-9A-Z]{1,4})?$'",
            name="icd10_format",
        ),
        Index(
            "ix_medical_conditions_patient_status",
            "patient_id",
            "clinical_status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    icd10_code: Mapped[str | None] = mapped_column(String(10))
    clinical_status: Mapped[ConditionClinicalStatus] = mapped_column(
        str_enum(ConditionClinicalStatus), nullable=False
    )
    verification_status: Mapped[ConditionVerificationStatus] = mapped_column(
        str_enum(ConditionVerificationStatus), nullable=False
    )
    severity: Mapped[Severity | None] = mapped_column(str_enum(Severity))
    onset_date: Mapped[date | None] = mapped_column(Date)
    onset_precision: Mapped[DatePrecision | None] = mapped_column(
        str_enum(DatePrecision, name="onset_precision")
    )
    abatement_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[RecordSource] = mapped_column(str_enum(RecordSource), nullable=False)
    visit_id: Mapped[uuid.UUID | None]
    notes: Mapped[str | None] = mapped_column(EncryptedString("medical_conditions.notes"))


# --- allergies -----------------------------------------------------------------------


class AllergenCategory(StrEnum):
    MEDICATION = "medication"
    FOOD = "food"
    ENVIRONMENT = "environment"
    BIOLOGIC = "biologic"
    OTHER = "other"


class AllergyType(StrEnum):
    ALLERGY = "allergy"
    INTOLERANCE = "intolerance"
    UNKNOWN = "unknown"


class ReactionSeverity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    LIFE_THREATENING = "life_threatening"


class AllergyClinicalStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    RESOLVED = "resolved"


class Allergy(Base, Entity, PatientOwned, SoftDelete, OptimisticLock):
    __tablename__ = "allergies"
    __table_args__ = (
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        Index(
            "ix_allergies_patient_active",
            "patient_id",
            postgresql_where=text("deleted_at IS NULL AND clinical_status = 'active'"),
        ),
    )

    substance: Mapped[str] = mapped_column(String(200), nullable=False)
    # Drug catalogue link for the medication-safety engine (catalogue arrives in Phase 14).
    substance_code: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[AllergenCategory] = mapped_column(str_enum(AllergenCategory), nullable=False)
    allergy_type: Mapped[AllergyType] = mapped_column(
        str_enum(AllergyType), nullable=False, default=AllergyType.UNKNOWN
    )
    reaction: Mapped[str | None] = mapped_column(String(300))
    severity: Mapped[ReactionSeverity | None] = mapped_column(str_enum(ReactionSeverity))
    clinical_status: Mapped[AllergyClinicalStatus] = mapped_column(
        str_enum(AllergyClinicalStatus), nullable=False, default=AllergyClinicalStatus.ACTIVE
    )
    verification_status: Mapped[ConditionVerificationStatus] = mapped_column(
        str_enum(ConditionVerificationStatus), nullable=False
    )
    onset_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[RecordSource] = mapped_column(str_enum(RecordSource), nullable=False)
    visit_id: Mapped[uuid.UUID | None]


# --- medical history -----------------------------------------------------------------


class HistoryCategory(StrEnum):
    SURGICAL = "surgical"
    HOSPITALIZATION = "hospitalization"
    FAMILY = "family"
    SOCIAL = "social"  # e.g. tobacco, alcohol, occupation
    IMMUNIZATION = "immunization"
    OBSTETRIC = "obstetric"
    INJURY = "injury"
    OTHER = "other"


class MedicalHistoryEntry(Base, Entity, PatientOwned, SoftDelete, OptimisticLock):
    """Past events that are not active problems: surgeries, admissions, family history..."""

    __tablename__ = "medical_history_entries"
    __table_args__ = (
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        CheckConstraint(
            "category <> 'family' OR family_relation IS NOT NULL", name="family_has_relation"
        ),
        Index(
            "ix_medical_history_entries_patient_category",
            "patient_id",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    category: Mapped[HistoryCategory] = mapped_column(str_enum(HistoryCategory), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    details: Mapped[str | None] = mapped_column(EncryptedString("medical_history_entries.details"))
    occurred_on: Mapped[date | None] = mapped_column(Date)
    occurred_precision: Mapped[DatePrecision | None] = mapped_column(
        str_enum(DatePrecision, name="occurred_precision")
    )
    family_relation: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[RecordSource] = mapped_column(str_enum(RecordSource), nullable=False)
    visit_id: Mapped[uuid.UUID | None]


# --- visits and notes ----------------------------------------------------------------


class VisitType(StrEnum):
    IN_PERSON = "in_person"
    TELECONSULT = "teleconsult"
    HOME_VISIT = "home_visit"
    EMERGENCY = "emergency"


class VisitStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ENTERED_IN_ERROR = "entered_in_error"


class DoctorVisit(Base, Entity, PatientOwned, OptimisticLock):
    """A consultation (encounter). Anchors notes, orders and prescriptions from it."""

    __tablename__ = "doctor_visits"
    __table_args__ = (
        patient_scope_key(),
        # use_alter: visits -> appointments -> follow_ups -> visits is a cycle.
        patient_scoped_fk(["appointment_id"], "appointments", use_alter=True),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="end_after_start"),
        CheckConstraint(
            "status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL",
            name="started_has_start_time",
        ),
        Index("ix_doctor_visits_patient_started", "patient_id", "started_at"),
        Index("ix_doctor_visits_doctor_started", "doctor_id", "started_at"),
    )

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[uuid.UUID | None]
    visit_type: Mapped[VisitType] = mapped_column(str_enum(VisitType), nullable=False)
    status: Mapped[VisitStatus] = mapped_column(
        str_enum(VisitStatus), nullable=False, default=VisitStatus.PLANNED
    )
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    chief_complaint: Mapped[str | None] = mapped_column(
        EncryptedString("doctor_visits.chief_complaint")
    )
    location: Mapped[str | None] = mapped_column(String(200))


class NoteType(StrEnum):
    PROGRESS = "progress"
    CONSULTATION = "consultation"
    SOAP = "soap"
    PROCEDURE = "procedure"
    DISCHARGE = "discharge"
    REFERRAL = "referral"
    OTHER = "other"


class NoteStatus(StrEnum):
    DRAFT = "draft"
    SIGNED = "signed"  # immutable from here on (trigger)
    SUPERSEDED = "superseded"  # replaced by a signed amendment
    ENTERED_IN_ERROR = "entered_in_error"


class ClinicalNote(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "clinical_notes"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        patient_scoped_fk(["supersedes_note_id"], "clinical_notes"),
        CheckConstraint(
            "status = 'draft' OR (signed_at IS NOT NULL AND signed_by IS NOT NULL)",
            name="signed_has_signer",
        ),
        CheckConstraint(
            "supersedes_note_id IS NULL OR amendment_reason IS NOT NULL",
            name="amendment_has_reason",
        ),
        CheckConstraint(
            "supersedes_note_id IS NULL OR supersedes_note_id <> id", name="not_self_superseding"
        ),
        # A note can be superseded by at most one amendment (a linear history).
        Index(
            "uq_clinical_notes_supersedes",
            "supersedes_note_id",
            unique=True,
            postgresql_where=text("supersedes_note_id IS NOT NULL"),
        ),
        Index("ix_clinical_notes_patient_created", "patient_id", "created_at"),
    )

    visit_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    author_doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    note_type: Mapped[NoteType] = mapped_column(str_enum(NoteType), nullable=False)
    status: Mapped[NoteStatus] = mapped_column(
        str_enum(NoteStatus), nullable=False, default=NoteStatus.DRAFT
    )
    body: Mapped[str] = mapped_column(EncryptedString("clinical_notes.body"), nullable=False)
    signed_at: Mapped[datetime | None]
    signed_by: Mapped[uuid.UUID | None] = user_fk()
    supersedes_note_id: Mapped[uuid.UUID | None]
    amendment_reason: Mapped[str | None] = mapped_column(String(300))
    # True when the text was drafted by the AI assistant and then edited/accepted.
    ai_assisted: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
