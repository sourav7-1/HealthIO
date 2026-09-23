"""Identity: people who can sign in. Health data lives in patient_profiles, not here.

A user can hold several roles at once (for example a doctor who is also a patient and a
caregiver for a parent). Sessions and refresh tokens back the token invalidation
strategy (see app/modules/identity/service.py); MFA factors arrive with step-up auth.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import Role
from app.core.models import Base, Entity, SoftDelete, str_enum, user_fk


class UserStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    LOCKED = "locked"  # administrative lock (brute-force locks use users.locked_until)
    SUSPENDED = "suspended"  # administrative action
    CLOSED = "closed"  # account closed; row kept (pseudonymised) for referential history


class User(Base, Entity, SoftDelete):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email_bidx IS NOT NULL OR phone_bidx IS NOT NULL", name="has_login_identifier"
        ),
        # Identifiers are unique among live accounts only, so a closed account's number
        # can be reused by someone else later.
        Index(
            "uq_users_email_bidx_live",
            "email_bidx",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND email_bidx IS NOT NULL"),
        ),
        Index(
            "uq_users_phone_bidx_live",
            "phone_bidx",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND phone_bidx IS NOT NULL"),
        ),
    )

    # Encrypted identifiers + HMAC blind indexes for lookup (app/core/crypto.py).
    email: Mapped[str | None] = mapped_column(EncryptedString("users.email"))
    email_bidx: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(EncryptedString("users.phone"))
    phone_bidx: Mapped[str | None] = mapped_column(String(64))

    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus), nullable=False, default=UserStatus.PENDING_VERIFICATION
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255)
    )  # Argon2id; OTP-only users have none
    email_verified_at: Mapped[datetime | None]
    phone_verified_at: Mapped[datetime | None]
    mfa_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    preferred_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="en", server_default="en"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    last_login_at: Mapped[datetime | None]

    # Brute-force protection: consecutive failures and a temporary lock.
    failed_login_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None]
    password_changed_at: Mapped[datetime | None]


class UserRole(Base, Entity):
    """Role grants. Revoking sets revoked_at, so the history of who held which role stays."""

    __tablename__ = "user_roles"
    __table_args__ = (
        Index(
            "uq_user_roles_active",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False, index=True)
    role: Mapped[Role] = mapped_column(str_enum(Role), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = user_fk()


class SessionRevokeReason(StrEnum):
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 (enum label)
    REFRESH_REUSE = "refresh_reuse"  # a rotated refresh token was replayed: likely theft
    ACCOUNT_DISABLED = "account_disabled"
    ADMIN = "admin"


class AuthSession(Base, Entity):
    """One signed-in device. Access tokens carry its id (`sid`) and are rejected as soon
    as the session is revoked, so logout takes effect immediately."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("absolute_expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)", name="revoked_has_reason"
        ),
        Index(
            "ix_auth_sessions_user_live",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[SessionRevokeReason | None] = mapped_column(str_enum(SessionRevokeReason))
    ip_address: Mapped[str | None] = mapped_column(String(45))  # truncated like audit
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))


class RefreshToken(Base, Entity):
    """Opaque refresh tokens, stored only as SHA-256 hashes. Each use rotates the token;
    presenting an already-used token revokes the whole session (reuse detection)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("uq_refresh_tokens_token_hash", "token_hash", unique=True),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_hex"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]


class ActionTokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"  # noqa: S105 (enum label)


class UserActionToken(Base, Entity):
    """Single-use, short-lived tokens sent by email. Only the SHA-256 hash is stored."""

    __tablename__ = "user_action_tokens"
    __table_args__ = (
        Index("uq_user_action_tokens_token_hash", "token_hash", unique=True),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_hex"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index(
            "ix_user_action_tokens_open",
            "user_id",
            "purpose",
            postgresql_where=text("used_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    purpose: Mapped[ActionTokenPurpose] = mapped_column(
        str_enum(ActionTokenPurpose), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]
