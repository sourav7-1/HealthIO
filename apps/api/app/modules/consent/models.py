"""Consent records (DPDP Act 2023): who allowed whom to use which data, for what purpose.

A consent record is immutable after creation except for its lifecycle fields (status,
withdrawal). Changing the scope of a consent means withdrawing or superseding it and
creating a new record, so the full history is always reconstructable (trigger in
migration 0002).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    Base,
    Entity,
    PatientOwned,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
    user_fk,
)


class GrantorCapacity(StrEnum):
    SELF = "self"
    GUARDIAN = "guardian"  # parent of a minor / legal representative (DPDP s.9)
    NOMINEE = "nominee"  # acting under a DPDP nomination (s.14)
    # The patient consented in person (e.g. at the clinic) and the treating clinician
    # recorded it, with the notice version shown. `granted_by` is the recording clinician.
    # The patient can review and withdraw it once they have an account.
    CLINICIAN_RECORDED = "clinician_recorded"


class GranteeType(StrEnum):
    DOCTOR = "doctor"
    CAREGIVER = "caregiver"
    PLATFORM = "platform"  # a platform purpose such as AI processing or reminders
    ORGANIZATION = "organization"  # e.g. a hospital or lab (ABDM HIU)


class ConsentPurpose(StrEnum):
    CARE_DELIVERY = "care_delivery"
    CAREGIVER_SUPPORT = "caregiver_support"
    MEDICATION_REMINDERS = "medication_reminders"
    AI_PROCESSING = "ai_processing"
    SMS_NOTIFICATIONS = "sms_notifications"
    WHATSAPP_NOTIFICATIONS = "whatsapp_notifications"
    HEALTH_RECORD_EXCHANGE = "health_record_exchange"  # ABDM
    RESEARCH = "research"  # never on by default


class DataCategory(StrEnum):
    DEMOGRAPHICS = "demographics"
    CONDITIONS = "conditions"
    ALLERGIES = "allergies"
    MEDICATIONS = "medications"
    PRESCRIPTIONS = "prescriptions"
    VISITS_AND_NOTES = "visits_and_notes"
    TESTS_AND_REPORTS = "tests_and_reports"
    DOCUMENTS = "documents"
    ADHERENCE = "adherence"
    APPOINTMENTS = "appointments"
    EMERGENCY = "emergency"


class AccessLevel(StrEnum):
    READ = "read"
    READ_WRITE = "read_write"


class LegalBasis(StrEnum):
    CONSENT = "consent"
    LEGITIMATE_USE = "legitimate_use"  # e.g. medical emergency (DPDP s.7)


class ConsentStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


_DATA_CATEGORY_ARRAY = "ARRAY[" + ", ".join(f"'{c.value}'" for c in DataCategory) + "]::varchar[]"


class ConsentRecord(Base, Entity, PatientOwned):
    __tablename__ = "consent_records"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["supersedes_consent_id"], "consent_records"),
        CheckConstraint(f"data_categories <@ {_DATA_CATEGORY_ARRAY}", name="data_categories_known"),
        CheckConstraint(
            "cardinality(data_categories) >= 1 OR grantee_type = 'platform'",
            name="sharing_has_categories",
        ),
        CheckConstraint(
            "(grantee_type IN ('doctor', 'caregiver')) = (grantee_user_id IS NOT NULL)",
            name="person_grantee_has_user",
        ),
        CheckConstraint(
            "grantee_type <> 'organization' OR grantee_organization IS NOT NULL",
            name="organization_grantee_named",
        ),
        CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name="withdrawn_consistent",
        ),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="until_after_from"),
        CheckConstraint(
            "legal_basis = 'consent' OR purpose = 'care_delivery'",
            name="legitimate_use_only_for_care",
        ),
        Index(
            "ix_consent_records_active_grantee",
            "patient_id",
            "grantee_user_id",
            "purpose",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_consent_records_active_purpose",
            "patient_id",
            "purpose",
            postgresql_where=text("status = 'active'"),
        ),
    )

    granted_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    grantor_capacity: Mapped[GrantorCapacity] = mapped_column(
        str_enum(GrantorCapacity), nullable=False
    )
    grantee_type: Mapped[GranteeType] = mapped_column(str_enum(GranteeType), nullable=False)
    grantee_user_id: Mapped[uuid.UUID | None] = user_fk()
    grantee_organization: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[ConsentPurpose] = mapped_column(str_enum(ConsentPurpose), nullable=False)
    data_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list, server_default="{}"
    )
    access_level: Mapped[AccessLevel] = mapped_column(
        str_enum(AccessLevel), nullable=False, default=AccessLevel.READ
    )
    legal_basis: Mapped[LegalBasis] = mapped_column(
        str_enum(LegalBasis), nullable=False, default=LegalBasis.CONSENT
    )
    # Version of the privacy notice shown when consent was given (e.g. "2026-09-en").
    notice_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ConsentStatus] = mapped_column(
        str_enum(ConsentStatus), nullable=False, default=ConsentStatus.ACTIVE
    )
    valid_from: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    valid_until: Mapped[datetime | None]
    withdrawn_at: Mapped[datetime | None]
    withdrawn_by: Mapped[uuid.UUID | None] = user_fk()
    withdrawal_reason: Mapped[str | None] = mapped_column(String(300))
    supersedes_consent_id: Mapped[uuid.UUID | None]
