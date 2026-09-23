"""Baseline schema: all core tables, constraints and indexes (see docs/data-model.md).

Revision ID: 0001
Revises:
Create Date: 2026-09-23 19:05:45.644562+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Needed by the appointments exclusion constraint (doctor_id WITH =, range WITH &&).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "users",
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_bidx", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("phone_bidx", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_verification",
                "active",
                "locked",
                "suspended",
                "closed",
                name="user_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("preferred_language", sa.String(length=10), server_default="en", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="Asia/Kolkata", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "email_bidx IS NOT NULL OR phone_bidx IS NOT NULL",
            name=op.f("ck_users_has_login_identifier"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_users_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_users_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_users_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_bidx_live",
        "users",
        ["email_bidx"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND email_bidx IS NOT NULL"),
    )
    op.create_index(
        "uq_users_phone_bidx_live",
        "users",
        ["phone_bidx"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND phone_bidx IS NOT NULL"),
    )
    op.create_table(
        "doctor_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("registration_council", sa.String(length=120), nullable=True),
        sa.Column("registration_number", sa.String(length=64), nullable=True),
        sa.Column("registration_year", sa.SmallInteger(), nullable=True),
        sa.Column("primary_specialty", sa.String(length=120), nullable=True),
        sa.Column(
            "additional_specialties",
            sa.ARRAY(sa.String(length=120)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "qualifications", sa.ARRAY(sa.String(length=120)), server_default="{}", nullable=False
        ),
        sa.Column("practice_name", sa.String(length=200), nullable=True),
        sa.Column("practice_address", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "unverified",
                "pending",
                "verified",
                "rejected",
                "suspended",
                name="doctor_verification_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "verification_status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name=op.f("ck_doctor_profiles_verified_has_verifier"),
        ),
        sa.CheckConstraint(
            "registration_year IS NULL OR registration_year BETWEEN 1900 AND 2200",
            name=op.f("ck_doctor_profiles_registration_year_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_doctor_profiles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_doctor_profiles_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_doctor_profiles_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name=op.f("fk_doctor_profiles_verified_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doctor_profiles")),
    )
    op.create_index(
        "ix_doctor_profiles_verification_status",
        "doctor_profiles",
        ["verification_status"],
        unique=False,
    )
    op.create_index(
        "uq_doctor_profiles_registration_live",
        "doctor_profiles",
        ["registration_council", "registration_number"],
        unique=True,
        postgresql_where=sa.text("verification_status IN ('pending', 'verified')"),
    )
    op.create_index("uq_doctor_profiles_user", "doctor_profiles", ["user_id"], unique=True)
    op.create_table(
        "patient_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("given_name", sa.String(length=100), nullable=False),
        sa.Column("family_name", sa.String(length=100), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column(
            "sex_at_birth",
            sa.Enum(
                "female",
                "male",
                "intersex",
                "unknown",
                name="sex_at_birth",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("gender_identity", sa.String(length=50), nullable=True),
        sa.Column(
            "blood_group",
            sa.Enum(
                "A+",
                "A-",
                "B+",
                "B-",
                "AB+",
                "AB-",
                "O+",
                "O-",
                "unknown",
                name="blood_group",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("abha_number", sa.Text(), nullable=True),
        sa.Column("abha_number_bidx", sa.String(length=64), nullable=True),
        sa.Column("abha_address", sa.Text(), nullable=True),
        sa.Column("preferred_language", sa.String(length=10), server_default="en", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="Asia/Kolkata", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                "deceased",
                name="patient_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("deceased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'deceased') = (deceased_at IS NOT NULL)",
            name=op.f("ck_patient_profiles_deceased_consistent"),
        ),
        sa.CheckConstraint(
            "date_of_birth IS NULL OR date_of_birth <= CURRENT_DATE",
            name=op.f("ck_patient_profiles_dob_not_future"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_patient_profiles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_patient_profiles_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_patient_profiles_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_patient_profiles_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_profiles")),
    )
    op.create_index(
        "ix_patient_profiles_family_given",
        "patient_profiles",
        ["family_name", "given_name"],
        unique=False,
    )
    op.create_index(
        "uq_patient_profiles_abha_live",
        "patient_profiles",
        ["abha_number_bidx"],
        unique=True,
        postgresql_where=sa.text("abha_number_bidx IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_patient_profiles_user_live",
        "patient_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_table(
        "tests",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("loinc_code", sa.String(length=10), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "haematology",
                "biochemistry",
                "endocrinology",
                "immunology",
                "microbiology",
                "pathology",
                "urinalysis",
                "imaging",
                "cardiology",
                "other",
                name="test_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("specimen", sa.String(length=64), nullable=True),
        sa.Column("default_unit", sa.String(length=32), nullable=True),
        sa.Column("is_panel", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "loinc_code IS NULL OR loinc_code ~ '^[0-9]{1,7}-[0-9]$'",
            name=op.f("ck_tests_loinc_format"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_tests_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_tests_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tests")),
    )
    op.create_index("uq_tests_code", "tests", ["code"], unique=True)
    op.create_index(
        "uq_tests_loinc_code",
        "tests",
        ["loinc_code"],
        unique=True,
        postgresql_where=sa.text("loinc_code IS NOT NULL"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "admin",
                "doctor",
                "patient",
                "caregiver",
                name="role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_user_roles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_user_roles_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_user_roles_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
    )
    op.create_index(op.f("ix_user_roles_user_id"), "user_roles", ["user_id"], unique=False)
    op.create_index(
        "uq_user_roles_active",
        "user_roles",
        ["user_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "allowed",
                "denied",
                "error",
                name="audit_outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "changed_fields", sa.ARRAY(sa.String(length=64)), server_default="{}", nullable=False
        ),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_logs_hash_hex")),
        sa.CheckConstraint(
            "prev_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_logs_prev_hash_hex")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_audit_logs_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_action_occurred", "audit_logs", ["action", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_logs_actor_occurred", "audit_logs", ["actor_user_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_logs_patient_occurred", "audit_logs", ["patient_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"], unique=False
    )
    op.create_index("uq_audit_logs_seq", "audit_logs", ["seq"], unique=True)
    op.create_table(
        "caregiver_relationships",
        sa.Column("caregiver_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "relationship_type",
            sa.Enum(
                "parent",
                "child",
                "spouse_partner",
                "sibling",
                "other_relative",
                "friend",
                "professional_carer",
                "legal_guardian",
                "other",
                name="caregiver_relationship_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_guardian", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("guardian_basis", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "invited",
                "active",
                "declined",
                "revoked",
                "expired",
                name="caregiver_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name=op.f("ck_caregiver_relationships_revoked_consistent"),
        ),
        sa.CheckConstraint(
            "status <> 'active' OR (accepted_at IS NOT NULL AND revoked_at IS NULL)",
            name=op.f("ck_caregiver_relationships_active_is_accepted"),
        ),
        sa.CheckConstraint(
            "NOT is_guardian OR guardian_basis IS NOT NULL",
            name=op.f("ck_caregiver_relationships_guardian_has_basis"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name=op.f("ck_caregiver_relationships_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["caregiver_user_id"],
            ["users.id"],
            name=op.f("fk_caregiver_relationships_caregiver_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_caregiver_relationships_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name=op.f("fk_caregiver_relationships_invited_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_caregiver_relationships_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_caregiver_relationships_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_caregiver_relationships_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_caregiver_relationships")),
        sa.UniqueConstraint(
            "id", "patient_id", name=op.f("uq_caregiver_relationships_id_patient_id")
        ),
    )
    op.create_index(
        "ix_caregiver_relationships_caregiver_status",
        "caregiver_relationships",
        ["caregiver_user_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_caregiver_relationships_patient_id"),
        "caregiver_relationships",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "uq_caregiver_relationships_open",
        "caregiver_relationships",
        ["caregiver_user_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('invited', 'active')"),
    )
    op.create_table(
        "consent_records",
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "grantor_capacity",
            sa.Enum(
                "self",
                "guardian",
                "nominee",
                name="grantor_capacity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "grantee_type",
            sa.Enum(
                "doctor",
                "caregiver",
                "platform",
                "organization",
                name="grantee_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("grantee_user_id", sa.Uuid(), nullable=True),
        sa.Column("grantee_organization", sa.String(length=200), nullable=True),
        sa.Column(
            "purpose",
            sa.Enum(
                "care_delivery",
                "caregiver_support",
                "medication_reminders",
                "ai_processing",
                "sms_notifications",
                "whatsapp_notifications",
                "health_record_exchange",
                "research",
                name="consent_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "data_categories", sa.ARRAY(sa.String(length=32)), server_default="{}", nullable=False
        ),
        sa.Column(
            "access_level",
            sa.Enum(
                "read", "read_write", name="access_level", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "legal_basis",
            sa.Enum(
                "consent",
                "legitimate_use",
                name="legal_basis",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("notice_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "withdrawn",
                "expired",
                "superseded",
                name="consent_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by", sa.Uuid(), nullable=True),
        sa.Column("withdrawal_reason", sa.String(length=300), nullable=True),
        sa.Column("supersedes_consent_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(grantee_type IN ('doctor', 'caregiver')) = (grantee_user_id IS NOT NULL)",
            name=op.f("ck_consent_records_person_grantee_has_user"),
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)",
            name=op.f("ck_consent_records_withdrawn_consistent"),
        ),
        sa.CheckConstraint(
            "cardinality(data_categories) >= 1 OR grantee_type = 'platform'",
            name=op.f("ck_consent_records_sharing_has_categories"),
        ),
        sa.CheckConstraint(
            "data_categories <@ ARRAY['demographics', 'conditions', 'allergies', 'medications', 'prescriptions', 'visits_and_notes', 'tests_and_reports', 'documents', 'adherence', 'appointments', 'emergency']::varchar[]",
            name=op.f("ck_consent_records_data_categories_known"),
        ),
        sa.CheckConstraint(
            "grantee_type <> 'organization' OR grantee_organization IS NOT NULL",
            name=op.f("ck_consent_records_organization_grantee_named"),
        ),
        sa.CheckConstraint(
            "legal_basis = 'consent' OR purpose = 'care_delivery'",
            name=op.f("ck_consent_records_legitimate_use_only_for_care"),
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name=op.f("ck_consent_records_until_after_from"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_consent_records_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_consent_records_granted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grantee_user_id"],
            ["users.id"],
            name=op.f("fk_consent_records_grantee_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_consent_records_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_consent_id", "patient_id"],
            ["consent_records.id", "consent_records.patient_id"],
            name=op.f("fk_consent_records_supersedes_consent_id_patient_id_consent_records"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_consent_records_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by"],
            ["users.id"],
            name=op.f("fk_consent_records_withdrawn_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_records")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_consent_records_id_patient_id")),
    )
    op.create_index(
        "ix_consent_records_active_grantee",
        "consent_records",
        ["patient_id", "grantee_user_id", "purpose"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_consent_records_active_purpose",
        "consent_records",
        ["patient_id", "purpose"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_consent_records_patient_id"), "consent_records", ["patient_id"], unique=False
    )
    op.create_table(
        "doctor_patient_relationships",
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_patient",
                "pending_doctor",
                "active",
                "declined",
                "ended",
                name="relationship_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_primary_doctor", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.Uuid(), nullable=True),
        sa.Column("end_reason", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status = 'active') = (started_at IS NOT NULL AND ended_at IS NULL)",
            name=op.f("ck_doctor_patient_relationships_active_has_start_no_end"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_doctor_patient_relationships_end_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_doctor_patient_relationships_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_doctor_patient_relationships_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by"],
            ["users.id"],
            name=op.f("fk_doctor_patient_relationships_ended_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by"],
            ["users.id"],
            name=op.f("fk_doctor_patient_relationships_initiated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_doctor_patient_relationships_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_doctor_patient_relationships_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doctor_patient_relationships")),
    )
    op.create_index(
        "ix_doctor_patient_relationships_doctor_status",
        "doctor_patient_relationships",
        ["doctor_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_doctor_patient_relationships_patient_id"),
        "doctor_patient_relationships",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "uq_doctor_patient_relationships_open",
        "doctor_patient_relationships",
        ["doctor_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_patient', 'pending_doctor', 'active')"),
    )
    op.create_table(
        "doctor_visits",
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "visit_type",
            sa.Enum(
                "in_person",
                "teleconsult",
                "home_visit",
                "emergency",
                name="visit_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "in_progress",
                "completed",
                "cancelled",
                "entered_in_error",
                name="visit_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chief_complaint", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL",
            name=op.f("ck_doctor_visits_started_has_start_time"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_doctor_visits_end_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_doctor_visits_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_doctor_visits_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_doctor_visits_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_doctor_visits_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doctor_visits")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_doctor_visits_id_patient_id")),
    )
    op.create_index(
        "ix_doctor_visits_doctor_started",
        "doctor_visits",
        ["doctor_id", "started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_doctor_visits_patient_id"), "doctor_visits", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_doctor_visits_patient_started",
        "doctor_visits",
        ["patient_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "emergency_contacts",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("relationship_label", sa.String(length=50), nullable=True),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("contact_user_id", sa.Uuid(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("notify_on_sos", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 10", name=op.f("ck_emergency_contacts_priority_range")
        ),
        sa.ForeignKeyConstraint(
            ["contact_user_id"],
            ["users.id"],
            name=op.f("fk_emergency_contacts_contact_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_emergency_contacts_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_emergency_contacts_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_emergency_contacts_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_emergency_contacts_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emergency_contacts")),
    )
    op.create_index(
        op.f("ix_emergency_contacts_patient_id"), "emergency_contacts", ["patient_id"], unique=False
    )
    op.create_index(
        "uq_emergency_contacts_priority_live",
        "emergency_contacts",
        ["patient_id", "priority"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "emergency_profiles",
        sa.Column("critical_information", sa.Text(), nullable=True),
        sa.Column("advance_directive", sa.Text(), nullable=True),
        sa.Column(
            "organ_donor",
            sa.Enum(
                "yes",
                "no",
                "undecided",
                name="organ_donor_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("show_blood_group", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("show_allergies", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("show_conditions", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("show_medications", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_emergency_profiles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_emergency_profiles_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_emergency_profiles_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emergency_profiles")),
    )
    op.create_index(
        op.f("ix_emergency_profiles_patient_id"), "emergency_profiles", ["patient_id"], unique=False
    )
    op.create_index(
        "uq_emergency_profiles_patient", "emergency_profiles", ["patient_id"], unique=True
    )
    op.create_table(
        "notifications",
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "medication_reminder",
                "missed_dose",
                "refill",
                "appointment",
                "follow_up",
                "prescription",
                "lab_result",
                "care_team",
                "consent",
                "security",
                "emergency",
                "system",
                name="notification_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum(
                "in_app",
                "push",
                "sms",
                "email",
                "whatsapp",
                name="notification_channel",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "normal",
                "high",
                "critical",
                name="notification_priority",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "delivered",
                "read",
                "failed",
                "cancelled",
                "suppressed",
                name="notification_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("failure_reason", sa.String(length=300), nullable=True),
        sa.Column("provider_message_id", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL",
            name=op.f("ck_notifications_failed_has_reason"),
        ),
        sa.CheckConstraint(
            "status <> 'read' OR read_at IS NOT NULL", name=op.f("ck_notifications_read_has_time")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_notifications_attempt_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_notifications_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_notifications_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name=op.f("fk_notifications_recipient_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_notifications_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        "ix_notifications_inbox_unread",
        "notifications",
        ["recipient_user_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("channel = 'in_app' AND read_at IS NULL"),
    )
    op.create_index(
        op.f("ix_notifications_patient_id"), "notifications", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_notifications_pending_due",
        "notifications",
        ["scheduled_for"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_notifications_recipient_created",
        "notifications",
        ["recipient_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_notifications_idempotency_key", "notifications", ["idempotency_key"], unique=True
    )
    op.create_table(
        "allergies",
        sa.Column("substance", sa.String(length=200), nullable=False),
        sa.Column("substance_code", sa.String(length=64), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "medication",
                "food",
                "environment",
                "biologic",
                "other",
                name="allergen_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "allergy_type",
            sa.Enum(
                "allergy",
                "intolerance",
                "unknown",
                name="allergy_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("reaction", sa.String(length=300), nullable=True),
        sa.Column(
            "severity",
            sa.Enum(
                "mild",
                "moderate",
                "severe",
                "life_threatening",
                name="reaction_severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "clinical_status",
            sa.Enum(
                "active",
                "inactive",
                "resolved",
                name="allergy_clinical_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.Enum(
                "unconfirmed",
                "provisional",
                "confirmed",
                "refuted",
                "entered_in_error",
                name="condition_verification_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("onset_date", sa.Date(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="record_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_allergies_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_allergies_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_allergies_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_allergies_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_allergies_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_allergies")),
    )
    op.create_index(
        "ix_allergies_patient_active",
        "allergies",
        ["patient_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND clinical_status = 'active'"),
    )
    op.create_index(op.f("ix_allergies_patient_id"), "allergies", ["patient_id"], unique=False)
    op.create_table(
        "caregiver_permissions",
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "view_profile",
                "view_medications",
                "manage_medications",
                "log_doses",
                "view_records",
                "upload_records",
                "view_labs",
                "manage_appointments",
                "receive_alerts",
                "use_ai_assistant",
                "manage_emergency_info",
                "manage_caregivers",
                name="caregiver_permission_scope",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name=op.f("ck_caregiver_permissions_revoke_after_grant"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_caregiver_permissions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_caregiver_permissions_granted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_caregiver_permissions_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id", "patient_id"],
            ["caregiver_relationships.id", "caregiver_relationships.patient_id"],
            name=op.f(
                "fk_caregiver_permissions_relationship_id_patient_id_caregiver_relationships"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_caregiver_permissions_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_caregiver_permissions_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_caregiver_permissions")),
    )
    op.create_index(
        op.f("ix_caregiver_permissions_patient_id"),
        "caregiver_permissions",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_caregiver_permissions_relationship_id"),
        "caregiver_permissions",
        ["relationship_id"],
        unique=False,
    )
    op.create_index(
        "uq_caregiver_permissions_active",
        "caregiver_permissions",
        ["relationship_id", "scope"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "clinical_notes",
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("author_doctor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "note_type",
            sa.Enum(
                "progress",
                "consultation",
                "soap",
                "procedure",
                "discharge",
                "referral",
                "other",
                name="note_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "signed",
                "superseded",
                "entered_in_error",
                name="note_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_by", sa.Uuid(), nullable=True),
        sa.Column("supersedes_note_id", sa.Uuid(), nullable=True),
        sa.Column("amendment_reason", sa.String(length=300), nullable=True),
        sa.Column("ai_assisted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status = 'draft' OR (signed_at IS NOT NULL AND signed_by IS NOT NULL)",
            name=op.f("ck_clinical_notes_signed_has_signer"),
        ),
        sa.CheckConstraint(
            "supersedes_note_id IS NULL OR amendment_reason IS NOT NULL",
            name=op.f("ck_clinical_notes_amendment_has_reason"),
        ),
        sa.CheckConstraint(
            "supersedes_note_id IS NULL OR supersedes_note_id <> id",
            name=op.f("ck_clinical_notes_not_self_superseding"),
        ),
        sa.ForeignKeyConstraint(
            ["author_doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_clinical_notes_author_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_clinical_notes_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_clinical_notes_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signed_by"],
            ["users.id"],
            name=op.f("fk_clinical_notes_signed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_note_id", "patient_id"],
            ["clinical_notes.id", "clinical_notes.patient_id"],
            name=op.f("fk_clinical_notes_supersedes_note_id_patient_id_clinical_notes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_clinical_notes_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_clinical_notes_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinical_notes")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_clinical_notes_id_patient_id")),
    )
    op.create_index(
        "ix_clinical_notes_patient_created",
        "clinical_notes",
        ["patient_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_clinical_notes_patient_id"), "clinical_notes", ["patient_id"], unique=False
    )
    op.create_index(
        op.f("ix_clinical_notes_visit_id"), "clinical_notes", ["visit_id"], unique=False
    )
    op.create_index(
        "uq_clinical_notes_supersedes",
        "clinical_notes",
        ["supersedes_note_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_note_id IS NOT NULL"),
    )
    op.create_table(
        "health_documents",
        sa.Column(
            "document_type",
            sa.Enum(
                "prescription",
                "lab_report",
                "imaging_report",
                "discharge_summary",
                "consultation_note",
                "vaccination_record",
                "medical_certificate",
                "insurance",
                "other",
                name="document_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="record_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column(
            "scan_status",
            sa.Enum(
                "pending_upload",
                "pending_scan",
                "clean",
                "quarantined",
                "failed",
                name="scan_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/heic', 'image/webp')",
            name=op.f("ck_health_documents_allowed_content_type"),
        ),
        sa.CheckConstraint(
            "scan_status NOT IN ('clean', 'quarantined') OR (sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name=op.f("ck_health_documents_scanned_has_digest"),
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_health_documents_sha256_hex"),
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name=op.f("ck_health_documents_size_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_health_documents_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_health_documents_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_health_documents_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_health_documents_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_health_documents_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_documents")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_health_documents_id_patient_id")),
    )
    op.create_index(
        op.f("ix_health_documents_patient_id"), "health_documents", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_health_documents_patient_type_date",
        "health_documents",
        ["patient_id", "document_type", "document_date"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_health_documents_storage_key", "health_documents", ["storage_key"], unique=True
    )
    op.create_table(
        "medical_conditions",
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("icd10_code", sa.String(length=10), nullable=True),
        sa.Column(
            "clinical_status",
            sa.Enum(
                "active",
                "recurrence",
                "relapse",
                "inactive",
                "remission",
                "resolved",
                name="condition_clinical_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.Enum(
                "unconfirmed",
                "provisional",
                "confirmed",
                "refuted",
                "entered_in_error",
                name="condition_verification_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum(
                "mild",
                "moderate",
                "severe",
                name="severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("onset_date", sa.Date(), nullable=True),
        sa.Column(
            "onset_precision",
            sa.Enum(
                "year",
                "month",
                "day",
                name="onset_precision",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("abatement_date", sa.Date(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="record_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "icd10_code IS NULL OR icd10_code ~ '^[A-Z][0-9][0-9A-Z](\\.[0-9A-Z]{1,4})?$'",
            name=op.f("ck_medical_conditions_icd10_format"),
        ),
        sa.CheckConstraint(
            "abatement_date IS NULL OR onset_date IS NULL OR abatement_date >= onset_date",
            name=op.f("ck_medical_conditions_abatement_after_onset"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medical_conditions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_medical_conditions_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medical_conditions_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medical_conditions_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_medical_conditions_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medical_conditions")),
    )
    op.create_index(
        op.f("ix_medical_conditions_patient_id"), "medical_conditions", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_medical_conditions_patient_status",
        "medical_conditions",
        ["patient_id", "clinical_status"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "medical_history_entries",
        sa.Column(
            "category",
            sa.Enum(
                "surgical",
                "hospitalization",
                "family",
                "social",
                "immunization",
                "obstetric",
                "injury",
                "other",
                name="history_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column(
            "occurred_precision",
            sa.Enum(
                "year",
                "month",
                "day",
                name="occurred_precision",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("family_relation", sa.String(length=50), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="record_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "category <> 'family' OR family_relation IS NOT NULL",
            name=op.f("ck_medical_history_entries_family_has_relation"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medical_history_entries_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name=op.f("fk_medical_history_entries_deleted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medical_history_entries_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medical_history_entries_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_medical_history_entries_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medical_history_entries")),
    )
    op.create_index(
        "ix_medical_history_entries_patient_category",
        "medical_history_entries",
        ["patient_id", "category"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        op.f("ix_medical_history_entries_patient_id"),
        "medical_history_entries",
        ["patient_id"],
        unique=False,
    )
    op.create_table(
        "test_orders",
        sa.Column("ordering_doctor_id", sa.Uuid(), nullable=False),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ordered",
                "sample_collected",
                "partially_resulted",
                "completed",
                "cancelled",
                "entered_in_error",
                name="test_order_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "routine",
                "urgent",
                "stat",
                name="test_priority",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("clinical_indication", sa.Text(), nullable=True),
        sa.Column(
            "ordered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("due_by", sa.Date(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name=op.f("ck_test_orders_cancelled_consistent"),
        ),
        sa.CheckConstraint(
            "due_by IS NULL OR due_by >= ordered_at::date",
            name=op.f("ck_test_orders_due_after_order"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            name=op.f("fk_test_orders_cancelled_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_orders_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ordering_doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_test_orders_ordering_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_test_orders_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_orders_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_test_orders_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_orders")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_test_orders_id_patient_id")),
    )
    op.create_index(
        "ix_test_orders_doctor_open",
        "test_orders",
        ["ordering_doctor_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('ordered', 'sample_collected', 'partially_resulted')"),
    )
    op.create_index(op.f("ix_test_orders_patient_id"), "test_orders", ["patient_id"], unique=False)
    op.create_index(
        "ix_test_orders_patient_ordered", "test_orders", ["patient_id", "ordered_at"], unique=False
    )
    op.create_table(
        "prescriptions",
        sa.Column(
            "source",
            sa.Enum(
                "doctor_issued",
                "uploaded",
                "manual_entry",
                "integration",
                name="prescription_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "issued",
                "recorded",
                "cancelled",
                "superseded",
                "entered_in_error",
                name="prescription_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("prescriber_doctor_id", sa.Uuid(), nullable=True),
        sa.Column("external_prescriber_name", sa.String(length=200), nullable=True),
        sa.Column("external_prescriber_registration", sa.String(length=64), nullable=True),
        sa.Column("external_facility_name", sa.String(length=200), nullable=True),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_prescription_id", sa.Uuid(), nullable=True),
        sa.Column("prescribed_on", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diagnosis_as_written", sa.Text(), nullable=True),
        sa.Column("advice", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "unverified",
                "patient_verified",
                "doctor_verified",
                "rejected",
                name="verification_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name=op.f("ck_prescriptions_cancelled_consistent"),
        ),
        sa.CheckConstraint(
            "source <> 'doctor_issued' OR prescriber_doctor_id IS NOT NULL",
            name=op.f("ck_prescriptions_doctor_issued_has_prescriber"),
        ),
        sa.CheckConstraint(
            "source <> 'uploaded' OR document_id IS NOT NULL",
            name=op.f("ck_prescriptions_uploaded_has_document"),
        ),
        sa.CheckConstraint(
            "status <> 'issued' OR (source = 'doctor_issued' AND issued_at IS NOT NULL)",
            name=op.f("ck_prescriptions_issued_is_doctor_issued"),
        ),
        sa.CheckConstraint(
            "status <> 'recorded' OR (source <> 'doctor_issued' AND verification_status IN ('patient_verified', 'doctor_verified') AND verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name=op.f("ck_prescriptions_recorded_is_verified"),
        ),
        sa.CheckConstraint(
            "supersedes_prescription_id IS NULL OR supersedes_prescription_id <> id",
            name=op.f("ck_prescriptions_not_self_superseding"),
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR prescribed_on IS NULL OR valid_until >= prescribed_on",
            name=op.f("ck_prescriptions_valid_until_after_prescribed"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            name=op.f("fk_prescriptions_cancelled_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_prescriptions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["health_documents.id", "health_documents.patient_id"],
            name=op.f("fk_prescriptions_document_id_patient_id_health_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_prescriptions_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescriber_doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_prescriptions_prescriber_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_prescription_id", "patient_id"],
            ["prescriptions.id", "prescriptions.patient_id"],
            name=op.f("fk_prescriptions_supersedes_prescription_id_patient_id_prescriptions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_prescriptions_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name=op.f("fk_prescriptions_verified_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_prescriptions_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prescriptions")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_prescriptions_id_patient_id")),
    )
    op.create_index(
        op.f("ix_prescriptions_patient_id"), "prescriptions", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_prescriptions_patient_prescribed",
        "prescriptions",
        ["patient_id", "prescribed_on"],
        unique=False,
    )
    op.create_index(
        "ix_prescriptions_prescriber_created",
        "prescriptions",
        ["prescriber_doctor_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_prescriptions_supersedes",
        "prescriptions",
        ["supersedes_prescription_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_prescription_id IS NOT NULL"),
    )
    op.create_table(
        "test_order_items",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("instructions", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_order_items_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "patient_id"],
            ["test_orders.id", "test_orders.patient_id"],
            name=op.f("fk_test_order_items_order_id_patient_id_test_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_test_order_items_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
            name=op.f("fk_test_order_items_test_id_tests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_order_items_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_order_items")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_test_order_items_id_patient_id")),
        sa.UniqueConstraint(
            "order_id", "test_id", name=op.f("uq_test_order_items_order_id_test_id")
        ),
    )
    op.create_index(
        op.f("ix_test_order_items_order_id"), "test_order_items", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_order_items_patient_id"), "test_order_items", ["patient_id"], unique=False
    )
    op.create_table(
        "test_reports",
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="record_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "verified",
                "rejected",
                "entered_in_error",
                name="test_report_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("lab_name", sa.String(length=200), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name=op.f("ck_test_reports_verified_has_verifier"),
        ),
        sa.CheckConstraint(
            "reported_at IS NULL OR collected_at IS NULL OR reported_at >= collected_at",
            name=op.f("ck_test_reports_reported_after_collected"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_reports_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["health_documents.id", "health_documents.patient_id"],
            name=op.f("fk_test_reports_document_id_patient_id_health_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "patient_id"],
            ["test_orders.id", "test_orders.patient_id"],
            name=op.f("fk_test_reports_order_id_patient_id_test_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_test_reports_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_reports_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name=op.f("fk_test_reports_verified_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_reports")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_test_reports_id_patient_id")),
    )
    op.create_index(
        "ix_test_reports_patient_collected",
        "test_reports",
        ["patient_id", "collected_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_reports_patient_id"), "test_reports", ["patient_id"], unique=False
    )
    op.create_table(
        "follow_ups",
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("source_visit_id", sa.Uuid(), nullable=True),
        sa.Column("source_prescription_id", sa.Uuid(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "booked",
                "completed",
                "cancelled",
                name="follow_up_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_visit_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status <> 'completed' OR completed_at IS NOT NULL",
            name=op.f("ck_follow_ups_completed_has_time"),
        ),
        sa.ForeignKeyConstraint(
            ["completed_visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_follow_ups_completed_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_follow_ups_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_follow_ups_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_follow_ups_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_prescription_id", "patient_id"],
            ["prescriptions.id", "prescriptions.patient_id"],
            name=op.f("fk_follow_ups_source_prescription_id_patient_id_prescriptions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_visit_id", "patient_id"],
            ["doctor_visits.id", "doctor_visits.patient_id"],
            name=op.f("fk_follow_ups_source_visit_id_patient_id_doctor_visits"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_follow_ups_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_follow_ups")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_follow_ups_id_patient_id")),
    )
    op.create_index(
        "ix_follow_ups_doctor_open_due",
        "follow_ups",
        ["doctor_id", "due_date"],
        unique=False,
        postgresql_where=sa.text("status IN ('open', 'booked')"),
    )
    op.create_index(
        "ix_follow_ups_patient_due", "follow_ups", ["patient_id", "due_date"], unique=False
    )
    op.create_index(op.f("ix_follow_ups_patient_id"), "follow_ups", ["patient_id"], unique=False)
    op.create_table(
        "prescription_items",
        sa.Column("prescription_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), nullable=False),
        sa.Column("drug_name", sa.String(length=200), nullable=False),
        sa.Column("generic_name", sa.String(length=200), nullable=True),
        sa.Column("drug_code", sa.String(length=64), nullable=True),
        sa.Column("strength", sa.String(length=64), nullable=True),
        sa.Column("dosage_form", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=64), nullable=True),
        sa.Column("dose_amount", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("dose_unit", sa.String(length=32), nullable=True),
        sa.Column("frequency_text", sa.String(length=64), nullable=True),
        sa.Column("times_per_day", sa.SmallInteger(), nullable=True),
        sa.Column(
            "meal_relation",
            sa.Enum(
                "before_food",
                "after_food",
                "with_food",
                "empty_stomach",
                "bedtime",
                "any",
                name="meal_relation",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("duration_days", sa.SmallInteger(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("is_prn", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("prn_reason", sa.String(length=200), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "NOT is_prn OR prn_reason IS NOT NULL",
            name=op.f("ck_prescription_items_prn_has_reason"),
        ),
        sa.CheckConstraint(
            "dose_amount IS NULL OR dose_amount > 0",
            name=op.f("ck_prescription_items_dose_positive"),
        ),
        sa.CheckConstraint(
            "duration_days IS NULL OR duration_days > 0",
            name=op.f("ck_prescription_items_duration_positive"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0", name=op.f("ck_prescription_items_quantity_positive")
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_prescription_items_sequence_positive")),
        sa.CheckConstraint(
            "times_per_day IS NULL OR times_per_day BETWEEN 1 AND 24",
            name=op.f("ck_prescription_items_times_per_day_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_prescription_items_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_prescription_items_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id", "patient_id"],
            ["prescriptions.id", "prescriptions.patient_id"],
            name=op.f("fk_prescription_items_prescription_id_patient_id_prescriptions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_prescription_items_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prescription_items")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_prescription_items_id_patient_id")),
        sa.UniqueConstraint(
            "prescription_id",
            "sequence",
            name=op.f("uq_prescription_items_prescription_id_sequence"),
        ),
    )
    op.create_index(
        op.f("ix_prescription_items_patient_id"), "prescription_items", ["patient_id"], unique=False
    )
    op.create_index(
        op.f("ix_prescription_items_prescription_id"),
        "prescription_items",
        ["prescription_id"],
        unique=False,
    )
    op.create_table(
        "test_results",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("analyte_name", sa.String(length=200), nullable=False),
        sa.Column("value_numeric", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("value_text", sa.String(length=200), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("reference_low", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("reference_high", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("reference_text", sa.String(length=200), nullable=True),
        sa.Column(
            "flag",
            sa.Enum(
                "normal",
                "low",
                "high",
                "critical_low",
                "critical_high",
                "abnormal",
                "unknown",
                name="result_flag",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "reference_low IS NULL OR reference_high IS NULL OR reference_low <= reference_high",
            name=op.f("ck_test_results_reference_range_ordered"),
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_test_results_sequence_positive")),
        sa.CheckConstraint(
            "value_numeric IS NOT NULL OR value_text IS NOT NULL",
            name=op.f("ck_test_results_has_value"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_results_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_test_results_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "patient_id"],
            ["test_reports.id", "test_reports.patient_id"],
            name=op.f("fk_test_results_report_id_patient_id_test_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
            name=op.f("fk_test_results_test_id_tests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_results_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_results")),
        sa.UniqueConstraint(
            "report_id", "sequence", name=op.f("uq_test_results_report_id_sequence")
        ),
    )
    op.create_index(
        op.f("ix_test_results_patient_id"), "test_results", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_test_results_patient_test", "test_results", ["patient_id", "test_id"], unique=False
    )
    op.create_index(op.f("ix_test_results_report_id"), "test_results", ["report_id"], unique=False)
    op.create_table(
        "appointments",
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("follow_up_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "requested",
                "scheduled",
                "confirmed",
                "checked_in",
                "completed",
                "cancelled",
                "no_show",
                name="appointment_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum(
                "in_person",
                "teleconsult",
                "home_visit",
                name="appointment_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("teleconsult_url", sa.String(length=500), nullable=True),
        sa.Column("booked_by", sa.Uuid(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("doctor_id"), "="),
            (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            where=sa.text("status IN ('requested', 'scheduled', 'confirmed', 'checked_in')"),
            using="gist",
            name="no_doctor_double_booking",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name=op.f("ck_appointments_cancelled_consistent"),
        ),
        sa.CheckConstraint(
            "ends_at - starts_at <= interval '12 hours'",
            name=op.f("ck_appointments_duration_reasonable"),
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_appointments_ends_after_starts")),
        sa.ForeignKeyConstraint(
            ["booked_by"],
            ["users.id"],
            name=op.f("fk_appointments_booked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            name=op.f("fk_appointments_cancelled_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_appointments_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor_profiles.id"],
            name=op.f("fk_appointments_doctor_id_doctor_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["follow_up_id", "patient_id"],
            ["follow_ups.id", "follow_ups.patient_id"],
            name=op.f("fk_appointments_follow_up_id_patient_id_follow_ups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_appointments_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_appointments_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_appointments_id_patient_id")),
    )
    op.create_index(
        "ix_appointments_doctor_starts", "appointments", ["doctor_id", "starts_at"], unique=False
    )
    op.create_index(
        op.f("ix_appointments_patient_id"), "appointments", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_appointments_patient_starts", "appointments", ["patient_id", "starts_at"], unique=False
    )
    op.create_table(
        "medications",
        sa.Column(
            "source",
            sa.Enum(
                "prescription",
                "self_reported",
                "integration",
                name="medication_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("prescription_item_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("generic_name", sa.String(length=200), nullable=True),
        sa.Column("drug_code", sa.String(length=64), nullable=True),
        sa.Column("strength", sa.String(length=64), nullable=True),
        sa.Column("dosage_form", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=64), nullable=True),
        sa.Column("is_prn", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending_confirmation",
                "active",
                "paused",
                "completed",
                "stopped",
                "entered_in_error",
                name="medication_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_by", sa.Uuid(), nullable=True),
        sa.Column(
            "stop_source",
            sa.Enum(
                "doctor",
                "patient",
                "caregiver",
                "ai_extraction",
                "integration",
                "system",
                name="stop_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("stop_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'stopped') = (stopped_at IS NOT NULL)",
            name=op.f("ck_medications_stopped_consistent"),
        ),
        sa.CheckConstraint(
            "source <> 'prescription' OR prescription_item_id IS NOT NULL",
            name=op.f("ck_medications_prescription_source_has_item"),
        ),
        sa.CheckConstraint(
            "status <> 'stopped' OR (stopped_by IS NOT NULL AND stop_source IS NOT NULL)",
            name=op.f("ck_medications_stopped_has_actor"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('active', 'paused', 'completed', 'stopped') OR (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)",
            name=op.f("ck_medications_confirmed_before_active"),
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name=op.f("ck_medications_end_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name=op.f("fk_medications_confirmed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medications_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medications_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_item_id", "patient_id"],
            ["prescription_items.id", "prescription_items.patient_id"],
            name=op.f("fk_medications_prescription_item_id_patient_id_prescription_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stopped_by"],
            ["users.id"],
            name=op.f("fk_medications_stopped_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medications_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medications")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_medications_id_patient_id")),
    )
    op.create_index(op.f("ix_medications_patient_id"), "medications", ["patient_id"], unique=False)
    op.create_index(
        "ix_medications_patient_status", "medications", ["patient_id", "status"], unique=False
    )
    op.create_index(
        "uq_medications_prescription_item_live",
        "medications",
        ["prescription_item_id"],
        unique=True,
        postgresql_where=sa.text(
            "prescription_item_id IS NOT NULL AND status <> 'entered_in_error'"
        ),
    )
    op.create_table(
        "medication_adherence",
        sa.Column("medication_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scheduled_count", sa.SmallInteger(), nullable=False),
        sa.Column("taken_count", sa.SmallInteger(), nullable=False),
        sa.Column("taken_late_count", sa.SmallInteger(), nullable=False),
        sa.Column("skipped_count", sa.SmallInteger(), nullable=False),
        sa.Column("missed_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "adherence_ratio",
            sa.Numeric(precision=5, scale=4),
            sa.Computed(
                "CASE WHEN scheduled_count = 0 THEN NULL ELSE round(taken_count::numeric / scheduled_count, 4) END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "scheduled_count >= 0 AND taken_count >= 0 AND taken_late_count >= 0 AND skipped_count >= 0 AND missed_count >= 0",
            name=op.f("ck_medication_adherence_counts_non_negative"),
        ),
        sa.CheckConstraint(
            "taken_count + skipped_count + missed_count <= scheduled_count",
            name=op.f("ck_medication_adherence_outcomes_within_scheduled"),
        ),
        sa.CheckConstraint(
            "taken_late_count <= taken_count",
            name=op.f("ck_medication_adherence_late_within_taken"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medication_adherence_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medication_id", "patient_id"],
            ["medications.id", "medications.patient_id"],
            name=op.f("fk_medication_adherence_medication_id_patient_id_medications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medication_adherence_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medication_adherence_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medication_adherence")),
        sa.UniqueConstraint(
            "medication_id", "day", name=op.f("uq_medication_adherence_medication_id_day")
        ),
    )
    op.create_index(
        "ix_medication_adherence_patient_day",
        "medication_adherence",
        ["patient_id", "day"],
        unique=False,
    )
    op.create_index(
        op.f("ix_medication_adherence_patient_id"),
        "medication_adherence",
        ["patient_id"],
        unique=False,
    )
    op.create_table(
        "medication_schedules",
        sa.Column("medication_id", sa.Uuid(), nullable=False),
        sa.Column(
            "schedule_type",
            sa.Enum(
                "fixed_times",
                "interval",
                "as_needed",
                name="schedule_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("times_of_day", sa.ARRAY(sa.Time()), server_default="{}", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("recurrence_rule", sa.String(length=500), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("dose_amount", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("dose_unit", sa.String(length=32), nullable=True),
        sa.Column(
            "meal_relation",
            sa.Enum(
                "before_food",
                "after_food",
                "with_food",
                "empty_stomach",
                "bedtime",
                "any",
                name="meal_relation",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "paused",
                "ended",
                name="schedule_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("ended_reason", sa.String(length=200), nullable=True),
        sa.Column("supersedes_schedule_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "schedule_type <> 'fixed_times' OR cardinality(times_of_day) >= 1",
            name=op.f("ck_medication_schedules_fixed_times_has_times"),
        ),
        sa.CheckConstraint(
            "schedule_type <> 'interval' OR interval_minutes BETWEEN 15 AND 10080",
            name=op.f("ck_medication_schedules_interval_range"),
        ),
        sa.CheckConstraint(
            "status <> 'ended' OR (effective_until IS NOT NULL AND ended_reason IS NOT NULL)",
            name=op.f("ck_medication_schedules_ended_has_end_and_reason"),
        ),
        sa.CheckConstraint(
            "dose_amount IS NULL OR dose_amount > 0",
            name=op.f("ck_medication_schedules_dose_positive"),
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name=op.f("ck_medication_schedules_until_after_from"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medication_schedules_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medication_id", "patient_id"],
            ["medications.id", "medications.patient_id"],
            name=op.f("fk_medication_schedules_medication_id_patient_id_medications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medication_schedules_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_schedule_id", "patient_id"],
            ["medication_schedules.id", "medication_schedules.patient_id"],
            name=op.f(
                "fk_medication_schedules_supersedes_schedule_id_patient_id_medication_schedules"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medication_schedules_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medication_schedules")),
        sa.UniqueConstraint(
            "id",
            "medication_id",
            "patient_id",
            name=op.f("uq_medication_schedules_id_medication_id_patient_id"),
        ),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_medication_schedules_id_patient_id")),
    )
    op.create_index(
        "ix_medication_schedules_active",
        "medication_schedules",
        ["medication_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_medication_schedules_medication_id"),
        "medication_schedules",
        ["medication_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_medication_schedules_patient_id"),
        "medication_schedules",
        ["patient_id"],
        unique=False,
    )
    op.create_table(
        "medication_doses",
        sa.Column("medication_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "scheduled",
                "snoozed",
                "taken",
                "skipped",
                "missed",
                "cancelled",
                name="dose_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snooze_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dose_amount", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("dose_unit", sa.String(length=32), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "recorded_via",
            sa.Enum(
                "patient_app",
                "caregiver_app",
                "reminder_action",
                "doctor",
                "system",
                name="dose_recorded_via",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("skip_reason", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status = 'snoozed') = (snoozed_until IS NOT NULL)",
            name=op.f("ck_medication_doses_snoozed_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'taken') = (taken_at IS NOT NULL)",
            name=op.f("ck_medication_doses_taken_consistent"),
        ),
        sa.CheckConstraint(
            "schedule_id IS NOT NULL OR status = 'taken'",
            name=op.f("ck_medication_doses_prn_dose_is_taken"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('taken', 'skipped') OR recorded_via IS NOT NULL",
            name=op.f("ck_medication_doses_patient_action_has_channel"),
        ),
        sa.CheckConstraint(
            "(schedule_id IS NULL) = (scheduled_at IS NULL)",
            name=op.f("ck_medication_doses_scheduled_has_schedule"),
        ),
        sa.CheckConstraint(
            "dose_amount IS NULL OR dose_amount > 0", name=op.f("ck_medication_doses_dose_positive")
        ),
        sa.CheckConstraint(
            "snooze_count >= 0", name=op.f("ck_medication_doses_snooze_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_medication_doses_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medication_id", "patient_id"],
            ["medications.id", "medications.patient_id"],
            name=op.f("fk_medication_doses_medication_id_patient_id_medications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_medication_doses_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            name=op.f("fk_medication_doses_recorded_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id", "medication_id", "patient_id"],
            [
                "medication_schedules.id",
                "medication_schedules.medication_id",
                "medication_schedules.patient_id",
            ],
            name=op.f(
                "fk_medication_doses_schedule_id_medication_id_patient_id_medication_schedules"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_medication_doses_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medication_doses")),
        sa.UniqueConstraint(
            "schedule_id", "scheduled_at", name=op.f("uq_medication_doses_schedule_id_scheduled_at")
        ),
    )
    op.create_index(
        "ix_medication_doses_medication_scheduled",
        "medication_doses",
        ["medication_id", "scheduled_at"],
        unique=False,
    )
    op.create_index(
        "ix_medication_doses_open_due",
        "medication_doses",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('scheduled', 'snoozed')"),
    )
    op.create_index(
        op.f("ix_medication_doses_patient_id"), "medication_doses", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_medication_doses_patient_scheduled",
        "medication_doses",
        ["patient_id", "scheduled_at"],
        unique=False,
    )
    # doctor_visits -> appointments -> follow_ups -> doctor_visits is a cycle, so this
    # foreign key is added after all three tables exist.
    op.create_foreign_key(
        op.f("fk_doctor_visits_appointment_id_patient_id_appointments"),
        "doctor_visits",
        "appointments",
        ["appointment_id", "patient_id"],
        ["id", "patient_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_doctor_visits_appointment_id_patient_id_appointments"),
        "doctor_visits",
        type_="foreignkey",
    )
    op.drop_index("ix_medication_doses_patient_scheduled", table_name="medication_doses")
    op.drop_index(op.f("ix_medication_doses_patient_id"), table_name="medication_doses")
    op.drop_index(
        "ix_medication_doses_open_due",
        table_name="medication_doses",
        postgresql_where=sa.text("status IN ('scheduled', 'snoozed')"),
    )
    op.drop_index("ix_medication_doses_medication_scheduled", table_name="medication_doses")
    op.drop_table("medication_doses")
    op.drop_index(op.f("ix_medication_schedules_patient_id"), table_name="medication_schedules")
    op.drop_index(op.f("ix_medication_schedules_medication_id"), table_name="medication_schedules")
    op.drop_index(
        "ix_medication_schedules_active",
        table_name="medication_schedules",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("medication_schedules")
    op.drop_index(op.f("ix_medication_adherence_patient_id"), table_name="medication_adherence")
    op.drop_index("ix_medication_adherence_patient_day", table_name="medication_adherence")
    op.drop_table("medication_adherence")
    op.drop_index(
        "uq_medications_prescription_item_live",
        table_name="medications",
        postgresql_where=sa.text(
            "prescription_item_id IS NOT NULL AND status <> 'entered_in_error'"
        ),
    )
    op.drop_index("ix_medications_patient_status", table_name="medications")
    op.drop_index(op.f("ix_medications_patient_id"), table_name="medications")
    op.drop_table("medications")
    op.drop_index("ix_appointments_patient_starts", table_name="appointments")
    op.drop_index(op.f("ix_appointments_patient_id"), table_name="appointments")
    op.drop_index("ix_appointments_doctor_starts", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index(op.f("ix_test_results_report_id"), table_name="test_results")
    op.drop_index("ix_test_results_patient_test", table_name="test_results")
    op.drop_index(op.f("ix_test_results_patient_id"), table_name="test_results")
    op.drop_table("test_results")
    op.drop_index(op.f("ix_prescription_items_prescription_id"), table_name="prescription_items")
    op.drop_index(op.f("ix_prescription_items_patient_id"), table_name="prescription_items")
    op.drop_table("prescription_items")
    op.drop_index(op.f("ix_follow_ups_patient_id"), table_name="follow_ups")
    op.drop_index("ix_follow_ups_patient_due", table_name="follow_ups")
    op.drop_index(
        "ix_follow_ups_doctor_open_due",
        table_name="follow_ups",
        postgresql_where=sa.text("status IN ('open', 'booked')"),
    )
    op.drop_table("follow_ups")
    op.drop_index(op.f("ix_test_reports_patient_id"), table_name="test_reports")
    op.drop_index("ix_test_reports_patient_collected", table_name="test_reports")
    op.drop_table("test_reports")
    op.drop_index(op.f("ix_test_order_items_patient_id"), table_name="test_order_items")
    op.drop_index(op.f("ix_test_order_items_order_id"), table_name="test_order_items")
    op.drop_table("test_order_items")
    op.drop_index(
        "uq_prescriptions_supersedes",
        table_name="prescriptions",
        postgresql_where=sa.text("supersedes_prescription_id IS NOT NULL"),
    )
    op.drop_index("ix_prescriptions_prescriber_created", table_name="prescriptions")
    op.drop_index("ix_prescriptions_patient_prescribed", table_name="prescriptions")
    op.drop_index(op.f("ix_prescriptions_patient_id"), table_name="prescriptions")
    op.drop_table("prescriptions")
    op.drop_index("ix_test_orders_patient_ordered", table_name="test_orders")
    op.drop_index(op.f("ix_test_orders_patient_id"), table_name="test_orders")
    op.drop_index(
        "ix_test_orders_doctor_open",
        table_name="test_orders",
        postgresql_where=sa.text("status IN ('ordered', 'sample_collected', 'partially_resulted')"),
    )
    op.drop_table("test_orders")
    op.drop_index(
        op.f("ix_medical_history_entries_patient_id"), table_name="medical_history_entries"
    )
    op.drop_index(
        "ix_medical_history_entries_patient_category",
        table_name="medical_history_entries",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("medical_history_entries")
    op.drop_index(
        "ix_medical_conditions_patient_status",
        table_name="medical_conditions",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_medical_conditions_patient_id"), table_name="medical_conditions")
    op.drop_table("medical_conditions")
    op.drop_index("uq_health_documents_storage_key", table_name="health_documents")
    op.drop_index(
        "ix_health_documents_patient_type_date",
        table_name="health_documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_health_documents_patient_id"), table_name="health_documents")
    op.drop_table("health_documents")
    op.drop_index(
        "uq_clinical_notes_supersedes",
        table_name="clinical_notes",
        postgresql_where=sa.text("supersedes_note_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_clinical_notes_visit_id"), table_name="clinical_notes")
    op.drop_index(op.f("ix_clinical_notes_patient_id"), table_name="clinical_notes")
    op.drop_index("ix_clinical_notes_patient_created", table_name="clinical_notes")
    op.drop_table("clinical_notes")
    op.drop_index(
        "uq_caregiver_permissions_active",
        table_name="caregiver_permissions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(
        op.f("ix_caregiver_permissions_relationship_id"), table_name="caregiver_permissions"
    )
    op.drop_index(op.f("ix_caregiver_permissions_patient_id"), table_name="caregiver_permissions")
    op.drop_table("caregiver_permissions")
    op.drop_index(op.f("ix_allergies_patient_id"), table_name="allergies")
    op.drop_index(
        "ix_allergies_patient_active",
        table_name="allergies",
        postgresql_where=sa.text("deleted_at IS NULL AND clinical_status = 'active'"),
    )
    op.drop_table("allergies")
    op.drop_index("uq_notifications_idempotency_key", table_name="notifications")
    op.drop_index("ix_notifications_recipient_created", table_name="notifications")
    op.drop_index(
        "ix_notifications_pending_due",
        table_name="notifications",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_notifications_patient_id"), table_name="notifications")
    op.drop_index(
        "ix_notifications_inbox_unread",
        table_name="notifications",
        postgresql_where=sa.text("channel = 'in_app' AND read_at IS NULL"),
    )
    op.drop_table("notifications")
    op.drop_index("uq_emergency_profiles_patient", table_name="emergency_profiles")
    op.drop_index(op.f("ix_emergency_profiles_patient_id"), table_name="emergency_profiles")
    op.drop_table("emergency_profiles")
    op.drop_index(
        "uq_emergency_contacts_priority_live",
        table_name="emergency_contacts",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_emergency_contacts_patient_id"), table_name="emergency_contacts")
    op.drop_table("emergency_contacts")
    op.drop_index("ix_doctor_visits_patient_started", table_name="doctor_visits")
    op.drop_index(op.f("ix_doctor_visits_patient_id"), table_name="doctor_visits")
    op.drop_index("ix_doctor_visits_doctor_started", table_name="doctor_visits")
    op.drop_table("doctor_visits")
    op.drop_index(
        "uq_doctor_patient_relationships_open",
        table_name="doctor_patient_relationships",
        postgresql_where=sa.text("status IN ('pending_patient', 'pending_doctor', 'active')"),
    )
    op.drop_index(
        op.f("ix_doctor_patient_relationships_patient_id"),
        table_name="doctor_patient_relationships",
    )
    op.drop_index(
        "ix_doctor_patient_relationships_doctor_status", table_name="doctor_patient_relationships"
    )
    op.drop_table("doctor_patient_relationships")
    op.drop_index(op.f("ix_consent_records_patient_id"), table_name="consent_records")
    op.drop_index(
        "ix_consent_records_active_purpose",
        table_name="consent_records",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "ix_consent_records_active_grantee",
        table_name="consent_records",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("consent_records")
    op.drop_index(
        "uq_caregiver_relationships_open",
        table_name="caregiver_relationships",
        postgresql_where=sa.text("status IN ('invited', 'active')"),
    )
    op.drop_index(
        op.f("ix_caregiver_relationships_patient_id"), table_name="caregiver_relationships"
    )
    op.drop_index(
        "ix_caregiver_relationships_caregiver_status", table_name="caregiver_relationships"
    )
    op.drop_table("caregiver_relationships")
    op.drop_index("uq_audit_logs_seq", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_patient_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_occurred", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "uq_user_roles_active",
        table_name="user_roles",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(op.f("ix_user_roles_user_id"), table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index(
        "uq_tests_loinc_code",
        table_name="tests",
        postgresql_where=sa.text("loinc_code IS NOT NULL"),
    )
    op.drop_index("uq_tests_code", table_name="tests")
    op.drop_table("tests")
    op.drop_index(
        "uq_patient_profiles_user_live",
        table_name="patient_profiles",
        postgresql_where=sa.text("user_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_patient_profiles_abha_live",
        table_name="patient_profiles",
        postgresql_where=sa.text("abha_number_bidx IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_index("ix_patient_profiles_family_given", table_name="patient_profiles")
    op.drop_table("patient_profiles")
    op.drop_index("uq_doctor_profiles_user", table_name="doctor_profiles")
    op.drop_index(
        "uq_doctor_profiles_registration_live",
        table_name="doctor_profiles",
        postgresql_where=sa.text("verification_status IN ('pending', 'verified')"),
    )
    op.drop_index("ix_doctor_profiles_verification_status", table_name="doctor_profiles")
    op.drop_table("doctor_profiles")
    op.drop_index(
        "uq_users_phone_bidx_live",
        table_name="users",
        postgresql_where=sa.text("deleted_at IS NULL AND phone_bidx IS NOT NULL"),
    )
    op.drop_index(
        "uq_users_email_bidx_live",
        table_name="users",
        postgresql_where=sa.text("deleted_at IS NULL AND email_bidx IS NOT NULL"),
    )
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
