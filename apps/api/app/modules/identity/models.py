"""Identity: people who can sign in. Health data lives in patient_profiles, not here.

A user can hold several roles at once (for example a doctor who is also a patient and a
caregiver for a parent). Credentials, sessions and MFA tables arrive in Phase 3.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import Role
from app.core.models import Base, Entity, SoftDelete, str_enum, user_fk


class UserStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    LOCKED = "locked"  # temporary, e.g. too many failed logins
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
