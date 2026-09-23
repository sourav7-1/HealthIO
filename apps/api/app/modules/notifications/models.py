"""Notifications: one row per recipient per channel, with delivery state.

`data` holds identifiers only (e.g. {"dose_id": "..."}), never clinical text. Rendered
text is encrypted because in-app messages can mention medicines. External channels use
PHI-safe templates (ARCHITECTURE.md §10).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import PATIENTS_ID, Base, Entity, str_enum, user_fk


class NotificationCategory(StrEnum):
    MEDICATION_REMINDER = "medication_reminder"
    MISSED_DOSE = "missed_dose"
    REFILL = "refill"
    APPOINTMENT = "appointment"
    FOLLOW_UP = "follow_up"
    PRESCRIPTION = "prescription"
    LAB_RESULT = "lab_result"
    CARE_TEAM = "care_team"  # invitations, relationship changes
    CONSENT = "consent"
    SECURITY = "security"  # new login, MFA change
    EMERGENCY = "emergency"  # SOS, emergency-profile access
    SYSTEM = "system"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    PUSH = "push"
    SMS = "sms"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class NotificationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"  # ignores quiet hours (SOS)


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"  # e.g. dose already logged before the reminder went out
    SUPPRESSED = "suppressed"  # preferences, quiet hours or missing consent


class Notification(Base, Entity):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("uq_notifications_idempotency_key", "idempotency_key", unique=True),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("status <> 'read' OR read_at IS NOT NULL", name="read_has_time"),
        CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL", name="failed_has_reason"
        ),
        Index(
            "ix_notifications_pending_due",
            "scheduled_for",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_notifications_inbox_unread",
            "recipient_user_id",
            "created_at",
            postgresql_where=text("channel = 'in_app' AND read_at IS NULL"),
        ),
        Index("ix_notifications_recipient_created", "recipient_user_id", "created_at"),
    )

    recipient_user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    # The patient the notification is about (may differ from the recipient: caregivers).
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey(PATIENTS_ID, ondelete="RESTRICT"), index=True
    )
    category: Mapped[NotificationCategory] = mapped_column(
        str_enum(NotificationCategory), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        str_enum(NotificationChannel), nullable=False
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        str_enum(NotificationPriority), nullable=False, default=NotificationPriority.NORMAL
    )
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(EncryptedString("notifications.title"))
    body: Mapped[str | None] = mapped_column(EncryptedString("notifications.body"))
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
