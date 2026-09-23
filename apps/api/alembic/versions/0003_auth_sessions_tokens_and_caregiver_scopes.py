"""Auth sessions, refresh and action tokens, lockout columns; caregiver scopes aligned
with the permission catalogue (app/modules/access/permissions.py).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23 19:19:32.535620+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TOUCH_TABLES = ("auth_sessions", "refresh_tokens", "user_action_tokens")

NEW_SCOPES = (
    "view_profile",
    "view_medical_history",
    "view_medications",
    "log_doses",
    "manage_reminders",
    "view_appointments",
    "manage_appointments",
    "view_reports",
    "upload_reports",
    "receive_alerts",
    "use_ai_assistant",
    "manage_emergency_info",
    "manage_caregivers",
)
OLD_SCOPES = (
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
)
# Renamed scopes keep their meaning. "manage_medications" is not carried over: caregivers
# must never change prescribed medication, so such grants become view-only.
UPGRADE_MAP = {
    "view_records": "view_reports",
    "view_labs": "view_reports",
    "upload_records": "upload_reports",
    "manage_medications": "view_medications",
}
DOWNGRADE_MAP = {
    "view_medical_history": "view_records",
    "manage_reminders": "view_medications",
    "view_appointments": "manage_appointments",
    "view_reports": "view_records",
    "upload_reports": "upload_records",
}
SCOPE_CHECK = "ck_caregiver_permissions_caregiver_permission_scope"


def _set_scope_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(SCOPE_CHECK), "caregiver_permissions", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        "caregiver_permission_scope", "caregiver_permissions", f"scope IN ({allowed})"
    )


def _remap_scopes(mapping: dict[str, str]) -> None:
    revoke_colliding = sa.text(
        """
        UPDATE caregiver_permissions p SET revoked_at = now()
        WHERE p.scope = :old AND p.revoked_at IS NULL AND EXISTS (
            SELECT 1 FROM caregiver_permissions q
            WHERE q.relationship_id = p.relationship_id AND q.scope = :new
              AND q.revoked_at IS NULL)
        """
    )
    rename = sa.text("UPDATE caregiver_permissions SET scope = :new WHERE scope = :old")
    for old, new in mapping.items():
        # A remap can collide with an existing active grant of the target scope.
        op.execute(revoke_colliding.bindparams(old=old, new=new))
        op.execute(rename.bindparams(old=old, new=new))


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoke_reason",
            sa.Enum(
                "logout",
                "logout_all",
                "password_changed",
                "refresh_reuse",
                "account_disabled",
                "admin",
                name="session_revoke_reason",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
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
            "(revoked_at IS NULL) = (revoke_reason IS NULL)",
            name=op.f("ck_auth_sessions_revoked_has_reason"),
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at", name=op.f("ck_auth_sessions_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_auth_sessions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_auth_sessions_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(
        "ix_auth_sessions_user_live",
        "auth_sessions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "user_action_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "email_verification",
                "password_reset",
                name="action_token_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_user_action_tokens_token_hash_hex")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_user_action_tokens_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_user_action_tokens_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_user_action_tokens_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_action_tokens_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_action_tokens")),
    )
    op.create_index(
        "ix_user_action_tokens_open",
        "user_action_tokens",
        ["user_id", "purpose"],
        unique=False,
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_index(
        "uq_user_action_tokens_token_hash", "user_action_tokens", ["token_hash"], unique=True
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_refresh_tokens_token_hash_hex")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_refresh_tokens_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"], unique=False
    )
    op.create_index("uq_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
    op.add_column(
        "users",
        sa.Column("failed_login_count", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    for table in TOUCH_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_touch_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION hio_touch_updated_at()"
        )
    # Widen the check, remap stored values, then narrow to the new scope set.
    _set_scope_check(tuple(sorted(set(OLD_SCOPES) | set(NEW_SCOPES))))
    _remap_scopes(UPGRADE_MAP)
    _set_scope_check(NEW_SCOPES)


def downgrade() -> None:
    _set_scope_check(tuple(sorted(set(OLD_SCOPES) | set(NEW_SCOPES))))
    _remap_scopes(DOWNGRADE_MAP)
    _set_scope_check(OLD_SCOPES)
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_index("uq_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_session_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("uq_user_action_tokens_token_hash", table_name="user_action_tokens")
    op.drop_index(
        "ix_user_action_tokens_open",
        table_name="user_action_tokens",
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.drop_table("user_action_tokens")
    op.drop_index(
        "ix_auth_sessions_user_live",
        table_name="auth_sessions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("auth_sessions")
