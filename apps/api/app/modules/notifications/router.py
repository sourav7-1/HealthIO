"""The signed-in user's notifications: in-app inbox and Web Push devices."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.access.dependencies import authenticated
from app.modules.identity.service import Principal
from app.modules.notifications import service
from app.modules.notifications.models import NotificationCategory

router = APIRouter(tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    category: NotificationCategory
    title: str | None
    body: str | None
    data: dict[str, Any]
    patient_id: uuid.UUID | None
    created_at: datetime
    read_at: datetime | None


class InboxOut(BaseModel):
    unread: int
    items: list[NotificationOut]


@router.get("/me/notifications", response_model=InboxOut)
async def my_notifications(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> InboxOut:
    rows, unread = await service.inbox(session, principal.user_id)
    return InboxOut(
        unread=unread,
        items=[NotificationOut.model_validate(r, from_attributes=True) for r in rows],
    )


@router.post("/me/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_notification(
    notification_id: uuid.UUID,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service.mark_read(session, principal.user_id, notification_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service.mark_read(session, principal.user_id, None)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class PushConfigOut(BaseModel):
    enabled: bool
    vapid_public_key: str | None


@router.get("/me/push/config", response_model=PushConfigOut)
async def push_config(
    request: Request, principal: Principal = Depends(authenticated())
) -> PushConfigOut:
    settings = request.app.state.settings
    key = settings.webpush_vapid_public_key
    return PushConfigOut(
        enabled=bool(key and settings.webpush_vapid_private_key), vapid_public_key=key
    )


class KeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=10, max_length=100)


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: KeysIn


class UnsubscribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=10, max_length=1000)


@router.post("/me/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def add_push_subscription(
    body: SubscriptionIn,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Register this browser for reminder notifications."""
    await service.subscribe(
        session,
        user_id=principal.user_id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/push-subscriptions/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_push_subscription(
    body: UnsubscribeIn,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service.unsubscribe(session, user_id=principal.user_id, endpoint=body.endpoint)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
