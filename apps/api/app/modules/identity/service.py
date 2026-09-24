"""Authentication: registration, login, sessions, token rotation, email verification and
password reset. Everything security-relevant is written to the audit log.

Token invalidation strategy
---------------------------
* Access token: short-lived JWT (10 min) that names its session (`sid`). Every request
  re-checks that the session is live and the account active, so logout, logout-all,
  password reset and account suspension take effect on the very next request.
* Refresh token: opaque, stored hashed, single use. Each refresh rotates it. Presenting
  a token that was already used means it was copied; the whole session is revoked.
* Sessions have a sliding refresh lifetime and an absolute cap.

Brute-force protection
----------------------
* Per-IP rate limit on the login route (router) and per-address limits on auth emails.
* Per-account counter: after N consecutive failures the account is locked for a period.
  During the lock even a correct password fails with the same generic error.
* Unknown accounts cost the same Argon2 verification as known ones (no timing oracle),
  and every failure returns the same error (no account enumeration).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import ClientInfo
from app.core.config import Settings
from app.core.crypto import email_index, normalize_email
from app.core.enums import Role
from app.core.errors import (
    AccountDisabledError,
    InvalidActionTokenError,
    InvalidCredentialsError,
    InvalidTokenError,
    ValidationFailedError,
)
from app.core.ids import uuid7
from app.core.logging import get_logger
from app.core.mail import Mailer, OutgoingEmail
from app.core.rate_limit import hit
from app.modules.audit.models import AuditOutcome
from app.modules.audit.service import AuditEvent, record_event, truncate_ip
from app.modules.care_team import service as care_team
from app.modules.identity import repo
from app.modules.identity.models import (
    ActionTokenPurpose,
    AuthSession,
    RefreshToken,
    SessionRevokeReason,
    User,
    UserActionToken,
    UserRole,
    UserStatus,
)
from app.modules.identity.security import (
    InvalidAccessTokenError,
    Passwords,
    TokenSigner,
    hash_token,
    new_opaque_token,
)
from app.modules.patients import service as patients

log = get_logger(__name__)

# Statuses that may hold a session. PENDING_VERIFICATION can sign in (to finish
# verification) but authorization dependencies deny it anything else.
_SIGN_IN_STATUSES = frozenset({UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION})
_COMMON_PASSWORDS = frozenset(
    {
        "password123",
        "password1234",
        "qwerty12345",
        "1234567890",
        "12345678910",
        "iloveyou123",
        "welcome1234",
        "letmein1234",
        "admin12345",
        "healthio123",
    }
)


class RegistrationRole(StrEnum):
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    DOCTOR = "doctor"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, rebuilt from the database on every request."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    roles: frozenset[Role]
    status: UserStatus
    email_verified: bool

    def has_role(self, role: Role) -> bool:
        return role in self.roles


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user_id: uuid.UUID


def password_problems(password: str, *, email: str | None, settings: Settings) -> list[str]:
    problems = []
    if len(password) < settings.password_min_length:
        problems.append(f"Use at least {settings.password_min_length} characters.")
    if len(password) > settings.password_max_length:
        problems.append(f"Use at most {settings.password_max_length} characters.")
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS or len(set(password)) < 4:
        problems.append("This password is too easy to guess.")
    if email:
        local = normalize_email(email).split("@")[0]
        if len(local) >= 4 and local in lowered:
            problems.append("Do not include your email address in the password.")
    return problems


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        passwords: Passwords,
        signer: TokenSigner,
        mailer: Mailer,
        redis: Redis,
        client: ClientInfo,
    ) -> None:
        self.db = session
        self.settings = settings
        self.passwords = passwords
        self.signer = signer
        self.mailer = mailer
        self.redis = redis
        self.client = client

    # --- helpers ---------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(UTC)

    async def _audit(
        self,
        action: str,
        outcome: AuditOutcome = AuditOutcome.ALLOWED,
        *,
        user_id: uuid.UUID | None = None,
        reason: str | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        await record_event(
            self.db,
            AuditEvent(
                action=action,
                outcome=outcome,
                actor_user_id=user_id,
                reason_code=reason,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=self.client.request_id,
                ip_address=self.client.ip,
                user_agent=self.client.user_agent,
                context=context or {},
            ),
        )

    def _check_password(self, password: str, email: str | None) -> None:
        problems = password_problems(password, email=email, settings=self.settings)
        if problems:
            raise ValidationFailedError(" ".join(problems))

    async def _email_allowed(self, bidx: str) -> bool:
        allowed, _ = await hit(
            self.redis, f"authmail:{bidx}", self.settings.auth_email_rate_limit_per_hour, 3600
        )
        return allowed

    async def _send_action_email(
        self, user: User, purpose: ActionTokenPurpose, ttl_seconds: int
    ) -> None:
        if user.email is None or user.email_bidx is None:
            return
        if not await self._email_allowed(user.email_bidx):
            log.warning("auth_email_rate_limited", purpose=purpose.value, user_id=str(user.id))
            return
        now = self._now()
        await repo.expire_open_action_tokens(self.db, user.id, purpose, now)
        token = new_opaque_token()
        self.db.add(
            UserActionToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_token(token),
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_by=user.id,
            )
        )
        await self.db.flush()
        # The token travels in the URL fragment, which browsers never send to servers,
        # so it cannot end up in access logs or Referer headers.
        if purpose == ActionTokenPurpose.EMAIL_VERIFICATION:
            subject = "Confirm your email address"
            link = f"{self.settings.public_web_url}/verify-email#token={token}"
            body = f"Confirm your email address to finish setting up Health Io:\n\n{link}\n"
        else:
            subject = "Reset your password"
            link = f"{self.settings.public_web_url}/reset-password#token={token}"
            minutes = ttl_seconds // 60
            body = (
                f"Use this link within {minutes} minutes to choose a new password:\n\n{link}\n\n"
                "If you did not ask for this, you can ignore this email."
            )
        await self.mailer.send(
            OutgoingEmail(
                to=user.email,
                subject=subject,
                text=body,
                meta={"purpose": purpose.value, "token": token},
            )
        )

    async def _open_action_token(
        self, token: str, purpose: ActionTokenPurpose
    ) -> tuple[UserActionToken, User]:
        row = await repo.get_open_action_token_for_update(self.db, hash_token(token), purpose)
        if row is None or row.expires_at <= self._now():
            raise InvalidActionTokenError()
        user = await repo.get_user(self.db, row.user_id)
        if user is None:
            raise InvalidActionTokenError()
        return row, user

    async def _start_session(self, user: User) -> IssuedTokens:
        now = self._now()
        auth_session = AuthSession(
            id=uuid7(),
            user_id=user.id,
            last_seen_at=now,
            absolute_expires_at=now + timedelta(seconds=self.settings.session_absolute_ttl_seconds),
            ip_address=truncate_ip(self.client.ip),
            user_agent_hash=(
                hashlib.sha256(self.client.user_agent.encode()).hexdigest()
                if self.client.user_agent
                else None
            ),
            created_by=user.id,
        )
        self.db.add(auth_session)
        await self.db.flush()
        return await self._issue_refresh(user.id, auth_session, now)

    async def _issue_refresh(
        self, user_id: uuid.UUID, auth_session: AuthSession, now: datetime
    ) -> IssuedTokens:
        refresh = new_opaque_token()
        expires = min(
            now + timedelta(seconds=self.settings.refresh_token_ttl_seconds),
            auth_session.absolute_expires_at,
        )
        self.db.add(
            RefreshToken(
                session_id=auth_session.id,
                token_hash=hash_token(refresh),
                expires_at=expires,
                created_by=user_id,
            )
        )
        await self.db.flush()
        return IssuedTokens(
            access_token=self.signer.issue(user_id, auth_session.id, now),
            access_expires_in=self.signer.ttl_seconds,
            refresh_token=refresh,
            refresh_expires_at=expires,
            user_id=user_id,
        )

    # --- registration ----------------------------------------------------------------

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        role: RegistrationRole,
        registration_council: str | None = None,
        registration_number: str | None = None,
    ) -> None:
        """Always completes the same way, whether or not the email is taken, so the
        endpoint cannot be used to discover who has an account."""
        self._check_password(password, email)
        bidx = email_index(email)
        existing = await repo.get_user_by_email_index(self.db, bidx)
        if existing is not None:
            if existing.email and await self._email_allowed(bidx):
                await self.mailer.send(
                    OutgoingEmail(
                        to=existing.email,
                        subject="Someone tried to register with your email",
                        text=(
                            "An attempt was made to create a Health Io account with this email "
                            "address, which already has an account. If this was you, sign in or "
                            "reset your password. Otherwise, you can ignore this email."
                        ),
                        meta={"purpose": "register_existing"},
                    )
                )
            await self._audit(
                "auth.register", AuditOutcome.DENIED, user_id=existing.id, reason="email_in_use"
            )
            await self.db.commit()
            return

        user_id = uuid7()
        user = User(
            id=user_id,
            email=normalize_email(email),
            email_bidx=bidx,
            display_name=display_name.strip(),
            password_hash=self.passwords.hash(password),
            password_changed_at=self._now(),
            status=UserStatus.PENDING_VERIFICATION,
            created_by=user_id,
            updated_by=user_id,
        )
        self.db.add(user)
        await self.db.flush()
        self.db.add(UserRole(user_id=user_id, role=Role(role.value), created_by=user_id))
        if role == RegistrationRole.PATIENT:
            await patients.create_self_profile(
                self.db, user_id=user_id, display_name=user.display_name
            )
        elif role == RegistrationRole.DOCTOR:
            await care_team.create_doctor_profile(
                self.db,
                user_id=user_id,
                display_name=user.display_name,
                registration_council=registration_council,
                registration_number=registration_number,
            )
        await self.db.flush()
        await self._send_action_email(
            user,
            ActionTokenPurpose.EMAIL_VERIFICATION,
            self.settings.email_verification_ttl_seconds,
        )
        await self._audit(
            "auth.register",
            user_id=user_id,
            resource_type="user",
            resource_id=user_id,
            context={"role": role.value},
        )
        await self.db.commit()

    # --- login -----------------------------------------------------------------------

    async def login(self, *, email: str, password: str) -> IssuedTokens:
        now = self._now()
        found = await repo.get_user_by_email_index(self.db, email_index(email))
        user = await repo.lock_user(self.db, found.id) if found else None

        locked = user is not None and user.locked_until is not None and user.locked_until > now
        # Always pay for one Argon2 verification, whatever the account state.
        password_ok = self.passwords.verify(user.password_hash if user else None, password)

        if user is None or locked or not password_ok:
            reason = "unknown_account" if user is None else "locked" if locked else "bad_password"
            if user is not None and not locked:
                user.failed_login_count += 1
                if user.failed_login_count >= self.settings.login_max_failures:
                    user.locked_until = now + timedelta(seconds=self.settings.login_lockout_seconds)
                    user.failed_login_count = 0
                    await self._audit(
                        "auth.lockout",
                        AuditOutcome.DENIED,
                        user_id=user.id,
                        reason="too_many_failures",
                    )
            await self._audit(
                "auth.login", AuditOutcome.DENIED, user_id=user.id if user else None, reason=reason
            )
            await self.db.commit()
            raise InvalidCredentialsError()

        # The password is right, so revealing the account state is safe now.
        if user.status not in _SIGN_IN_STATUSES:
            await self._audit(
                "auth.login", AuditOutcome.DENIED, user_id=user.id, reason=f"status_{user.status}"
            )
            await self.db.commit()
            raise AccountDisabledError()

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        if user.password_hash and self.passwords.needs_rehash(user.password_hash):
            user.password_hash = self.passwords.hash(password)
        tokens = await self._start_session(user)
        await self._audit("auth.login", user_id=user.id)
        await self.db.commit()
        return tokens

    # --- refresh / logout ------------------------------------------------------------

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        now = self._now()
        row = await repo.get_refresh_token_for_update(self.db, hash_token(refresh_token))
        if row is None:
            raise InvalidTokenError()
        auth_session = await repo.get_session_for_update(self.db, row.session_id)
        if auth_session is None:
            raise InvalidTokenError()

        if row.used_at is not None:
            # Replay of a rotated token: someone else has a copy. Kill the session.
            if auth_session.revoked_at is None:
                auth_session.revoked_at = now
                auth_session.revoke_reason = SessionRevokeReason.REFRESH_REUSE
            await self._audit(
                "auth.refresh_reuse_detected",
                AuditOutcome.DENIED,
                user_id=auth_session.user_id,
                reason="refresh_token_reuse",
                resource_type="auth_session",
                resource_id=auth_session.id,
            )
            await self.db.commit()
            raise InvalidTokenError()

        if (
            auth_session.revoked_at is not None
            or auth_session.absolute_expires_at <= now
            or row.expires_at <= now
        ):
            raise InvalidTokenError()

        user = await repo.get_user(self.db, auth_session.user_id)
        if user is None or user.status not in _SIGN_IN_STATUSES:
            auth_session.revoked_at = now
            auth_session.revoke_reason = SessionRevokeReason.ACCOUNT_DISABLED
            await self.db.commit()
            raise InvalidTokenError()

        row.used_at = now
        auth_session.last_seen_at = now
        tokens = await self._issue_refresh(user.id, auth_session, now)
        await self.db.commit()
        return tokens

    async def logout(self, principal: Principal, *, everywhere: bool = False) -> int:
        now = self._now()
        count = await repo.revoke_sessions(
            self.db,
            principal.user_id,
            SessionRevokeReason.LOGOUT_ALL if everywhere else SessionRevokeReason.LOGOUT,
            now,
            only=None if everywhere else principal.session_id,
        )
        await self._audit(
            "auth.logout_all" if everywhere else "auth.logout",
            user_id=principal.user_id,
            resource_type="auth_session",
            resource_id=None if everywhere else principal.session_id,
            context={"sessions_revoked": str(count)},
        )
        await self.db.commit()
        return count

    async def revoke_session(self, principal: Principal, session_id: uuid.UUID) -> None:
        count = await repo.revoke_sessions(
            self.db, principal.user_id, SessionRevokeReason.LOGOUT, self._now(), only=session_id
        )
        if count:
            await self._audit(
                "auth.session_revoked",
                user_id=principal.user_id,
                resource_type="auth_session",
                resource_id=session_id,
            )
        await self.db.commit()

    async def change_password(self, principal: Principal, current: str, new: str) -> int:
        """Requires the current password; signs out every other device."""
        user = await repo.lock_user(self.db, principal.user_id)
        if user is None or not self.passwords.verify(user.password_hash, current):
            await self._audit(
                "auth.password_change",
                AuditOutcome.DENIED,
                user_id=principal.user_id,
                reason="bad_current_password",
            )
            await self.db.commit()
            raise InvalidCredentialsError("Your current password is not correct.")
        self._check_password(new, user.email)
        now = self._now()
        user.password_hash = self.passwords.hash(new)
        user.password_changed_at = now
        user.updated_by = user.id
        # Every other device is signed out; the one making the change stays signed in.
        revoked = await repo.revoke_sessions(
            self.db,
            user.id,
            SessionRevokeReason.PASSWORD_CHANGED,
            now,
            except_session=principal.session_id,
        )
        await self._audit(
            "auth.password_change", user_id=user.id, context={"sessions_revoked": str(revoked)}
        )
        await self.db.commit()
        return revoked

    async def update_account(
        self,
        principal: Principal,
        *,
        display_name: str | None,
        timezone: str | None,
        preferred_language: str | None,
    ) -> User:
        user = await repo.get_user(self.db, principal.user_id)
        if user is None:
            raise InvalidTokenError()
        changed = []
        for field, value in (
            ("display_name", display_name),
            ("timezone", timezone),
            ("preferred_language", preferred_language),
        ):
            if value is not None:
                setattr(user, field, value)
                changed.append(field)
        user.updated_by = user.id
        await self._audit("account.update", user_id=user.id, context={"fields": ",".join(changed)})
        await self.db.commit()
        return user

    async def list_sessions(self, principal: Principal) -> list[AuthSession]:
        return await repo.list_live_sessions(self.db, principal.user_id, self._now())

    # --- email verification ----------------------------------------------------------

    async def request_email_verification(self, principal: Principal) -> None:
        user = await repo.get_user(self.db, principal.user_id)
        if user is not None and user.email_verified_at is None:
            await self._send_action_email(
                user,
                ActionTokenPurpose.EMAIL_VERIFICATION,
                self.settings.email_verification_ttl_seconds,
            )
            await self.db.commit()

    async def confirm_email(self, token: str) -> None:
        row, user = await self._open_action_token(token, ActionTokenPurpose.EMAIL_VERIFICATION)
        now = self._now()
        row.used_at = now
        user.email_verified_at = user.email_verified_at or now
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
        user.updated_by = user.id
        await self._audit("auth.email_verified", user_id=user.id)
        await self.db.commit()

    # --- password reset --------------------------------------------------------------

    async def request_password_reset(self, email: str) -> None:
        """Same response whether or not the account exists."""
        user = await repo.get_user_by_email_index(self.db, email_index(email))
        if user is not None and user.status in _SIGN_IN_STATUSES:
            await self._send_action_email(
                user, ActionTokenPurpose.PASSWORD_RESET, self.settings.password_reset_ttl_seconds
            )
            await self._audit("auth.password_reset_requested", user_id=user.id)
        await self.db.commit()

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        row, user = await self._open_action_token(token, ActionTokenPurpose.PASSWORD_RESET)
        # Validate first: a rejected password must not burn the single-use link.
        self._check_password(new_password, user.email)
        now = self._now()
        row.used_at = now
        user.password_hash = self.passwords.hash(new_password)
        user.password_changed_at = now
        user.failed_login_count = 0
        user.locked_until = None
        # Receiving the link proves control of the mailbox, the same proof verification uses.
        if user.email_verified_at is None:
            user.email_verified_at = now
            if user.status == UserStatus.PENDING_VERIFICATION:
                user.status = UserStatus.ACTIVE
        user.updated_by = user.id
        revoked = await repo.revoke_sessions(
            self.db, user.id, SessionRevokeReason.PASSWORD_CHANGED, now
        )
        await self._audit(
            "auth.password_reset_completed",
            user_id=user.id,
            context={"sessions_revoked": str(revoked)},
        )
        await self.db.commit()
        if user.email:
            await self.mailer.send(
                OutgoingEmail(
                    to=user.email,
                    subject="Your password was changed",
                    text=(
                        "Your Health Io password was just changed and all devices were signed "
                        "out. If this was not you, reset your password immediately."
                    ),
                    meta={"purpose": "password_changed"},
                )
            )

    # --- per-request authentication --------------------------------------------------

    async def authenticate(self, access_token: str) -> Principal:
        try:
            claims = self.signer.verify(access_token)
        except InvalidAccessTokenError as exc:
            log.info("access_token_rejected", reason=str(exc))
            raise InvalidTokenError() from exc
        now = self._now()
        auth_session = await repo.get_live_session(self.db, claims.session_id, now)
        if auth_session is None or auth_session.user_id != claims.user_id:
            raise InvalidTokenError()
        user = await repo.get_user(self.db, claims.user_id)
        if user is None or user.status not in _SIGN_IN_STATUSES:
            raise InvalidTokenError()
        return Principal(
            user_id=user.id,
            session_id=auth_session.id,
            roles=await repo.active_roles(self.db, user.id),
            status=user.status,
            email_verified=user.email_verified_at is not None,
        )


async def grant_role(
    session: AsyncSession, *, user_id: uuid.UUID, role: Role, granted_by: uuid.UUID
) -> bool:
    """Idempotently give a user a role (e.g. CAREGIVER on accepting an invitation)."""
    if role in await repo.active_roles(session, user_id):
        return False
    session.add(UserRole(user_id=user_id, role=role, created_by=granted_by))
    await session.flush()
    return True


async def find_user_id_by_email(session: AsyncSession, email: str) -> uuid.UUID | None:
    user = await repo.get_user_by_email_index(session, email_index(email))
    return user.id if user and user.status in _SIGN_IN_STATUSES else None


async def get_account(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await repo.get_user(session, user_id)


async def display_names(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Display names for showing who someone is (e.g. a patient's caregivers)."""
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
    return dict(rows.tuples().all())
