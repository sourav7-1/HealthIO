"""Medication safety: trusted reference data and the warnings found for each patient.

Reference data (drug products and their active ingredients, interactions,
contraindications, allergy cross-sensitivity classes) comes only from structured
datasets published by allow-listed sources and loaded by
`scripts/import_drug_reference.py`. Every row points to its dataset, so each warning can
say where it came from. Nothing is generated or inferred: if a dataset does not list an
interaction, Health Io does not report one.

Warnings are stored per patient with their source, severity, when they were found, and
whether a clinician reviewed or the patient acknowledged them. They never say to stop or
change a medicine.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import Base, Entity, OptimisticLock, PatientOwned, str_enum, user_fk

# --- reference data -------------------------------------------------------------------


class DatasetStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class ReferenceDataset(Base, Entity):
    __tablename__ = "reference_datasets"
    __table_args__ = (
        Index("uq_reference_datasets_key_version", "key", "version", unique=True),
        Index(
            "uq_reference_datasets_active_key",
            "key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher: Mapped[str] = mapped_column(String(64), nullable=False)  # allow-list key
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    license: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewed_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[DatasetStatus] = mapped_column(
        str_enum(DatasetStatus), nullable=False, default=DatasetStatus.ACTIVE
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class DrugProduct(Base, Entity):
    """A product name (brand or generic) and its active ingredients, as the dataset lists."""

    __tablename__ = "drug_products"
    __table_args__ = (
        Index("ix_drug_products_name_key", "name_key"),
        Index("ix_drug_products_code", "code"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reference_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_key: Mapped[str] = mapped_column(String(300), nullable=False)
    ingredients: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False)
    dosage_form: Mapped[str | None] = mapped_column(String(64))


class SourceSeverity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CONTRAINDICATED = "contraindicated"


class DrugInteraction(Base, Entity):
    __tablename__ = "drug_interactions"
    __table_args__ = (
        Index("ix_drug_interactions_pair", "ingredient_a", "ingredient_b"),
        CheckConstraint("ingredient_a < ingredient_b", name="ordered_pair"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reference_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_a: Mapped[str] = mapped_column(String(200), nullable=False)
    ingredient_b: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[SourceSeverity] = mapped_column(str_enum(SourceSeverity), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)  # as the source words it
    source_ref: Mapped[str | None] = mapped_column(String(200))


class DrugContraindication(Base, Entity):
    """An ingredient listed against a condition, matched by ICD-10 code prefix."""

    __tablename__ = "drug_contraindications"
    __table_args__ = (Index("ix_drug_contraindications_ingredient", "ingredient"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reference_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient: Mapped[str] = mapped_column(String(200), nullable=False)
    icd10_prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    condition_label: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[SourceSeverity] = mapped_column(str_enum(SourceSeverity), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))


class AllergyClassMember(Base, Entity):
    """Ingredients a dataset groups into one allergy cross-sensitivity class."""

    __tablename__ = "allergy_class_members"
    __table_args__ = (
        Index("ix_allergy_class_members_class", "class_key"),
        Index("ix_allergy_class_members_ingredient", "ingredient"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reference_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_key: Mapped[str] = mapped_column(String(100), nullable=False)
    class_label: Mapped[str] = mapped_column(String(200), nullable=False)
    ingredient: Mapped[str] = mapped_column(String(200), nullable=False)


# --- warnings -------------------------------------------------------------------------


class WarningKind(StrEnum):
    DUPLICATE_MEDICATION = "duplicate_medication"
    DUPLICATE_INGREDIENT = "duplicate_ingredient"
    INTERACTION = "interaction"
    CONTRAINDICATION = "contraindication"
    ALLERGY = "allergy"
    INCONSISTENT_PRESCRIPTION = "inconsistent_prescription"


class WarningSeverity(StrEnum):
    INFO = "info"
    CAUTION = "caution"
    SERIOUS = "serious"


class WarningStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"  # the situation no longer exists (e.g. a medicine was stopped)


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    ACKNOWLEDGED = "acknowledged"  # the patient or a caregiver saw it
    REVIEWED = "reviewed"  # a doctor reviewed it (with a note)


class SafetyWarning(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "safety_warnings"
    __table_args__ = (
        # One open warning per situation; re-checks refresh it instead of duplicating.
        Index(
            "uq_safety_warnings_open_fingerprint",
            "patient_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_safety_warnings_patient_status", "patient_id", "status"),
        CheckConstraint(
            "review_status = 'unreviewed' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)",
            name="reviewed_has_reviewer",
        ),
        CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolved_consistent"
        ),
    )

    kind: Mapped[WarningKind] = mapped_column(str_enum(WarningKind), nullable=False)
    severity: Mapped[WarningSeverity] = mapped_column(str_enum(WarningSeverity), nullable=False)
    source_severity: Mapped[str | None] = mapped_column(String(32))  # as the dataset rates it
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # Names medicines, allergies and conditions, so these texts are encrypted.
    title: Mapped[str] = mapped_column(EncryptedString("safety_warnings.title"), nullable=False)
    # What the patient sees (plain words, never an instruction to change treatment).
    patient_detail: Mapped[str] = mapped_column(
        EncryptedString("safety_warnings.patient_detail"), nullable=False
    )
    # What clinicians see, including the source's own wording.
    clinician_detail: Mapped[str] = mapped_column(
        EncryptedString("safety_warnings.clinician_detail"), nullable=False
    )
    # Where it came from: a reference dataset, or a Health Io consistency rule.
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("reference_datasets.id", ondelete="SET NULL")
    )
    # Records involved, e.g. ["med:<id>", "rxi:<id>", "allergy:<id>", "cond:<id>"].
    subjects: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # Health-record categories a viewer needs to see the names involved (allergies…).
    needs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[WarningStatus] = mapped_column(
        str_enum(WarningStatus), nullable=False, default=WarningStatus.OPEN
    )
    resolved_at: Mapped[datetime | None]
    review_status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus), nullable=False, default=ReviewStatus.UNREVIEWED
    )
    reviewed_at: Mapped[datetime | None]
    reviewed_by: Mapped[uuid.UUID | None] = user_fk()
    reviewer_role: Mapped[str | None] = mapped_column(String(20))
    review_note: Mapped[str | None] = mapped_column(EncryptedString("safety_warnings.review_note"))
