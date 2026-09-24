"""Web Push subscriptions: one row per browser/device that allowed notifications.

The endpoint URL and keys are encrypted (they let anyone holding them push to the
device); a blind index keeps endpoints unique without storing them in clear.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import Base, Entity, user_fk


class PushSubscription(Base, Entity):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        Index(
            "uq_push_subscriptions_endpoint_live",
            "endpoint_bidx",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_push_subscriptions_user_live",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint("failure_count >= 0", name="failure_count_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    endpoint: Mapped[str] = mapped_column(
        EncryptedString("push_subscriptions.endpoint"), nullable=False
    )
    endpoint_bidx: Mapped[str] = mapped_column(String(64), nullable=False)
    p256dh: Mapped[str] = mapped_column(
        EncryptedString("push_subscriptions.p256dh"), nullable=False
    )
    auth: Mapped[str] = mapped_column(EncryptedString("push_subscriptions.auth"), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(200))
    last_success_at: Mapped[datetime | None]
    failure_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    revoked_at: Mapped[datetime | None]
