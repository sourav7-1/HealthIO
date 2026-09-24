"""Notifications service: in-app inbox, Web Push subscriptions and delivery.

Every notification is one row per recipient per channel, keyed by an idempotency key,
so a job that runs twice (or two workers) never sends the same reminder twice.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_keyring
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.ids import uuid7
from app.modules.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
)
from app.modules.notifications.push import PushMessage, PushSender, Target
from app.modules.notifications.push_models import PushSubscription

MAX_SUBSCRIPTIONS_PER_USER = 10
MAX_PUSH_FAILURES = 5


def _endpoint_index(endpoint: str) -> str:
    return get_keyring().blind_index(endpoint.strip(), "push_subscriptions.endpoint")


# --- subscriptions ----------------------------------------------------------------------------


async def subscribe(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None,
) -> PushSubscription:
    if not endpoint.startswith("https://"):
        raise ValidationFailedError("A push endpoint must be an https URL.")
    bidx = _endpoint_index(endpoint)
    existing: PushSubscription | None = await session.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint_bidx == bidx, PushSubscription.revoked_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    if existing is not None:
        if existing.user_id != user_id:  # a device changed hands: the old owner loses it
            existing.revoked_at = now
            await session.flush()
        else:
            existing.p256dh, existing.auth, existing.failure_count = p256dh, auth, 0
            existing.updated_by = user_id
            await session.flush()
            return existing
    count = await session.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.user_id == user_id, PushSubscription.revoked_at.is_(None))
    )
    if (count or 0) >= MAX_SUBSCRIPTIONS_PER_USER:
        raise ValidationFailedError("Too many devices have notifications turned on.")
    sub = PushSubscription(
        user_id=user_id,
        endpoint=endpoint.strip(),
        endpoint_bidx=bidx,
        p256dh=p256dh,
        auth=auth,
        user_agent=(user_agent or "")[:200] or None,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(sub)
    await session.flush()
    return sub


async def unsubscribe(session: AsyncSession, *, user_id: uuid.UUID, endpoint: str) -> bool:
    result = await session.execute(
        update(PushSubscription)
        .where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint_bidx == _endpoint_index(endpoint),
            PushSubscription.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), updated_by=user_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def active_subscriptions(
    session: AsyncSession, user_ids: set[uuid.UUID]
) -> list[PushSubscription]:
    if not user_ids:
        return []
    rows = await session.scalars(
        select(PushSubscription).where(
            PushSubscription.user_id.in_(user_ids), PushSubscription.revoked_at.is_(None)
        )
    )
    return list(rows.all())


# --- creating notifications -------------------------------------------------------------------


@dataclass(frozen=True)
class Draft:
    recipient_user_id: uuid.UUID
    patient_id: uuid.UUID | None
    category: NotificationCategory
    template_key: str
    title: str
    body: str
    data: dict[str, Any]
    idempotency_key: str
    priority: NotificationPriority = NotificationPriority.NORMAL


async def create_in_app(session: AsyncSession, draft: Draft, *, now: datetime) -> bool:
    """Insert once per idempotency key; returns False if it already existed."""
    stmt = (
        insert(Notification)
        .values(
            id=uuid7(),
            recipient_user_id=draft.recipient_user_id,
            patient_id=draft.patient_id,
            category=draft.category.value,
            channel=NotificationChannel.IN_APP.value,
            priority=draft.priority.value,
            template_key=draft.template_key,
            title=draft.title,  # encrypted by the column type
            body=draft.body,
            data=draft.data,
            status=NotificationStatus.SENT.value,
            idempotency_key=f"{draft.idempotency_key}:in_app",
            scheduled_for=now,
            sent_at=now,
            attempt_count=1,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def deliver_push(
    session: AsyncSession,
    sender: PushSender,
    draft: Draft,
    message: PushMessage,
    *,
    now: datetime,
    suppressed_reason: str | None = None,
) -> int:
    """Send to every device of the recipient; one notification row per delivery attempt
    set (idempotent). Returns the number of devices reached."""
    key = f"{draft.idempotency_key}:push"
    status = NotificationStatus.SUPPRESSED if suppressed_reason else NotificationStatus.PENDING
    inserted = await session.execute(
        insert(Notification)
        .values(
            id=uuid7(),
            recipient_user_id=draft.recipient_user_id,
            patient_id=draft.patient_id,
            category=draft.category.value,
            channel=NotificationChannel.PUSH.value,
            priority=draft.priority.value,
            template_key=draft.template_key,
            data=draft.data,
            status=status.value,
            idempotency_key=key,
            scheduled_for=now,
            failure_reason=None,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Notification.id)
    )
    row_id = inserted.scalar_one_or_none()
    if row_id is None or suppressed_reason:  # already handled, or deliberately not sent
        return 0
    subs = await active_subscriptions(session, {draft.recipient_user_id})
    reached = 0
    errors: list[str] = []
    for sub in subs:
        result = await sender.send(Target(sub.endpoint, sub.p256dh, sub.auth), message)
        if result.ok:
            reached += 1
            sub.last_success_at, sub.failure_count = now, 0
        else:
            errors.append(result.error or "error")
            sub.failure_count += 1
            if result.gone or sub.failure_count >= MAX_PUSH_FAILURES:
                sub.revoked_at = now
    await session.execute(
        update(Notification)
        .where(Notification.id == row_id)
        .values(
            status=(
                NotificationStatus.SENT
                if reached
                else (NotificationStatus.SUPPRESSED if not subs else NotificationStatus.FAILED)
            ).value,
            sent_at=now if reached else None,
            attempt_count=1,
            failure_reason=None if reached or not subs else ", ".join(errors)[:300],
        )
    )
    await session.flush()
    return reached


# --- inbox ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InboxItem:
    notification: Notification


async def inbox(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 50
) -> tuple[list[Notification], int]:
    rows = list(
        (
            await session.scalars(
                select(Notification)
                .where(
                    Notification.recipient_user_id == user_id,
                    Notification.channel == NotificationChannel.IN_APP,
                )
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    unread = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_user_id == user_id,
            Notification.channel == NotificationChannel.IN_APP,
            Notification.read_at.is_(None),
        )
    )
    return rows, int(unread or 0)


async def mark_read(
    session: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID | None
) -> int:
    stmt = update(Notification).where(
        Notification.recipient_user_id == user_id,
        Notification.channel == NotificationChannel.IN_APP,
        Notification.read_at.is_(None),
    )
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    now = datetime.now(UTC)
    result = await session.execute(
        stmt.values(read_at=now, status=NotificationStatus.READ).execution_options(
            synchronize_session=False
        )
    )
    if notification_id is not None and not result.rowcount:  # type: ignore[attr-defined]
        exists = await session.scalar(
            select(Notification.id).where(
                Notification.id == notification_id, Notification.recipient_user_id == user_id
            )
        )
        if exists is None:
            raise NotFoundError()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
