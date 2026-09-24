"""Queries on identity tables. Only this module touches them."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.identity.models import (
    ActionTokenPurpose,
    AuthSession,
    RefreshToken,
    SessionRevokeReason,
    User,
    UserActionToken,
    UserRole,
)


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    row: User | None = await session.scalar(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    return row


async def get_user_by_email_index(session: AsyncSession, email_bidx: str) -> User | None:
    row: User | None = await session.scalar(
        select(User).where(User.email_bidx == email_bidx, User.deleted_at.is_(None))
    )
    return row


async def lock_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Row lock so concurrent failed logins cannot race past the failure counter."""
    row: User | None = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def active_roles(session: AsyncSession, user_id: uuid.UUID) -> frozenset[Role]:
    rows = await session.scalars(
        select(UserRole.role).where(UserRole.user_id == user_id, UserRole.revoked_at.is_(None))
    )
    return frozenset(rows.all())


async def get_live_session(
    session: AsyncSession, session_id: uuid.UUID, now: datetime
) -> AuthSession | None:
    row: AuthSession | None = await session.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.absolute_expires_at > now,
        )
    )
    return row


async def list_live_sessions(
    session: AsyncSession, user_id: uuid.UUID, now: datetime
) -> list[AuthSession]:
    rows = await session.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.absolute_expires_at > now,
        )
        .order_by(AuthSession.last_seen_at.desc())
    )
    return list(rows.all())


async def revoke_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    reason: SessionRevokeReason,
    now: datetime,
    *,
    only: uuid.UUID | None = None,
    except_session: uuid.UUID | None = None,
) -> int:
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
        .execution_options(synchronize_session=False)
    )
    if only is not None:
        stmt = stmt.where(AuthSession.id == only)
    if except_session is not None:
        stmt = stmt.where(AuthSession.id != except_session)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def get_refresh_token_for_update(
    session: AsyncSession, token_hash: str
) -> RefreshToken | None:
    row: RefreshToken | None = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def get_session_for_update(
    session: AsyncSession, session_id: uuid.UUID
) -> AuthSession | None:
    row: AuthSession | None = await session.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def get_open_action_token_for_update(
    session: AsyncSession, token_hash: str, purpose: ActionTokenPurpose
) -> UserActionToken | None:
    row: UserActionToken | None = await session.scalar(
        select(UserActionToken)
        .where(
            UserActionToken.token_hash == token_hash,
            UserActionToken.purpose == purpose,
            UserActionToken.used_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def expire_open_action_tokens(
    session: AsyncSession, user_id: uuid.UUID, purpose: ActionTokenPurpose, now: datetime
) -> None:
    """Issuing a new link invalidates older unused ones."""
    await session.execute(
        update(UserActionToken)
        .where(
            UserActionToken.user_id == user_id,
            UserActionToken.purpose == purpose,
            UserActionToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
