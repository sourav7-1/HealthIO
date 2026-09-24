"""Reminder preferences: how and when a patient wants to be reminded about doses.

Defaults protect privacy: lock-screen and push text do not name medicines unless the
patient opts in (ARCHITECTURE.md §10).
"""

from datetime import time

from sqlalchemy import CheckConstraint, Index, SmallInteger, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, Entity, OptimisticLock, PatientOwned

SNOOZE_CHOICES = (5, 10, 15, 30, 60)


class ReminderPreference(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "reminder_preferences"
    __table_args__ = (
        Index("uq_reminder_preferences_patient", "patient_id", unique=True),
        CheckConstraint(f"default_snooze_minutes IN {SNOOZE_CHOICES}", name="snooze_choice"),
        CheckConstraint("missed_after_minutes BETWEEN 30 AND 720", name="missed_after_range"),
        CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)", name="quiet_hours_pair"
        ),
        CheckConstraint(
            "remind_again_after_minutes IS NULL OR remind_again_after_minutes BETWEEN 5 AND 120",
            name="remind_again_range",
        ),
    )

    reminders_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    channel_push: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    channel_sms: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    channel_email: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    show_medicine_names: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)
    default_snooze_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=10, server_default="10"
    )
    # After this long without an answer, a scheduled dose is recorded as missed.
    missed_after_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=120, server_default="120"
    )
    notify_caregivers_on_missed: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    # One more reminder if a dose is not answered after this long (NULL: remind once).
    remind_again_after_minutes: Mapped[int | None] = mapped_column(
        SmallInteger, default=15, server_default="15"
    )
