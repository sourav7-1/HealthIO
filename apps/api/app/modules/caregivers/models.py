"""Caregivers: family members and carers who help a patient, with scoped permissions.

A relationship says *who* helps *whom*; permissions (one row per scope) say *what* they
may do. Permissions are revoked by setting `revoked_at`, never by deleting the row, so
the history of who could see what, and when, is preserved.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, text
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


class CaregiverRelationshipType(StrEnum):
    PARENT = "parent"
    CHILD = "child"
    SPOUSE_PARTNER = "spouse_partner"
    SIBLING = "sibling"
    OTHER_RELATIVE = "other_relative"
    FRIEND = "friend"
    PROFESSIONAL_CARER = "professional_carer"
    LEGAL_GUARDIAN = "legal_guardian"
    OTHER = "other"


class CaregiverStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DECLINED = "declined"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CaregiverPermissionScope(StrEnum):
    """What a caregiver may do for a patient. Least privilege: grant only what is needed.

    Every scope is read-only or writes only information labelled as the caregiver's own
    (dose confirmations, reminder times, uploads, reported allergies). Deliberately
    absent: editing clinical records, changing a doctor's prescription and deleting
    medical records. Those can never be granted to a caregiver
    (app/modules/access/permissions.py enforces this again at decision time).
    """

    VIEW_PROFILE = "view_profile"
    VIEW_MEDICAL_HISTORY = "view_medical_history"  # conditions, allergies, history
    VIEW_PRESCRIPTIONS = "view_prescriptions"  # read-only
    VIEW_VISITS = "view_visits"  # visits and signed clinical notes, read-only
    VIEW_ADHERENCE = "view_adherence"
    REPORT_HEALTH_INFO = "report_health_info"  # add medicines/allergies, labelled as caregiver's
    VIEW_MEDICATIONS = "view_medications"
    LOG_DOSES = "log_doses"
    MANAGE_REMINDERS = "manage_reminders"  # reminder times and preferences, not doses
    VIEW_APPOINTMENTS = "view_appointments"
    MANAGE_APPOINTMENTS = "manage_appointments"
    VIEW_REPORTS = "view_reports"  # test reports and health documents
    UPLOAD_REPORTS = "upload_reports"
    RECEIVE_ALERTS = "receive_alerts"
    USE_AI_ASSISTANT = "use_ai_assistant"
    MANAGE_EMERGENCY_INFO = "manage_emergency_info"
    MANAGE_CAREGIVERS = "manage_caregivers"  # guardians only


class CaregiverRelationship(Base, Entity, PatientOwned):
    __tablename__ = "caregiver_relationships"
    __table_args__ = (
        patient_scope_key(),
        Index(
            "uq_caregiver_relationships_open",
            "caregiver_user_id",
            "patient_id",
            unique=True,
            postgresql_where=text("status IN ('invited', 'active')"),
        ),
        CheckConstraint(
            "status <> 'active' OR (accepted_at IS NOT NULL AND revoked_at IS NULL)",
            name="active_is_accepted",
        ),
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)", name="revoked_consistent"
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="expiry_after_creation"
        ),
        CheckConstraint("NOT is_guardian OR guardian_basis IS NOT NULL", name="guardian_has_basis"),
        Index("ix_caregiver_relationships_caregiver_status", "caregiver_user_id", "status"),
    )

    caregiver_user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    relationship_type: Mapped[CaregiverRelationshipType] = mapped_column(
        str_enum(CaregiverRelationshipType), nullable=False
    )
    # A guardian may consent on the patient's behalf (minor or dependent adult).
    is_guardian: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    guardian_basis: Mapped[str | None] = mapped_column(String(200))  # e.g. "parent of minor"
    status: Mapped[CaregiverStatus] = mapped_column(
        str_enum(CaregiverStatus), nullable=False, default=CaregiverStatus.INVITED
    )
    invited_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    accepted_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = user_fk()
    revoke_reason: Mapped[str | None] = mapped_column(String(200))


class CaregiverPermission(Base, Entity, PatientOwned):
    __tablename__ = "caregiver_permissions"
    __table_args__ = (
        patient_scoped_fk(["relationship_id"], "caregiver_relationships"),
        Index(
            "uq_caregiver_permissions_active",
            "relationship_id",
            "scope",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoke_after_grant"
        ),
    )

    relationship_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    scope: Mapped[CaregiverPermissionScope] = mapped_column(
        str_enum(CaregiverPermissionScope), nullable=False
    )
    granted_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    granted_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = user_fk()
