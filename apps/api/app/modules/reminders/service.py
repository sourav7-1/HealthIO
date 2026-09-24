"""Reminder preferences (defaults apply until the patient saves their own)."""

import uuid
from dataclasses import dataclass
from datetime import time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.modules.reminders.models import SNOOZE_CHOICES, ReminderPreference


@dataclass(frozen=True)
class Preferences:
    reminders_enabled: bool = True
    channel_push: bool = True
    channel_sms: bool = False
    channel_email: bool = False
    show_medicine_names: bool = False
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    default_snooze_minutes: int = 10
    missed_after_minutes: int = 120
    notify_caregivers_on_missed: bool = True

    @property
    def missed_after(self) -> timedelta:
        return timedelta(minutes=self.missed_after_minutes)


FIELDS = tuple(Preferences.__dataclass_fields__)


async def get(session: AsyncSession, patient_id: uuid.UUID) -> Preferences:
    row = await session.scalar(
        select(ReminderPreference).where(ReminderPreference.patient_id == patient_id)
    )
    if row is None:
        return Preferences()
    return Preferences(**{f: getattr(row, f) for f in FIELDS})


async def save(
    session: AsyncSession, patient_id: uuid.UUID, prefs: Preferences, actor: uuid.UUID
) -> Preferences:
    if prefs.default_snooze_minutes not in SNOOZE_CHOICES:
        raise ValidationFailedError(
            f"Snooze must be one of {', '.join(map(str, SNOOZE_CHOICES))} minutes."
        )
    if (prefs.quiet_hours_start is None) != (prefs.quiet_hours_end is None):
        raise ValidationFailedError("Set both the start and end of quiet hours, or neither.")
    if prefs.reminders_enabled and not (
        prefs.channel_push or prefs.channel_sms or prefs.channel_email
    ):
        raise ValidationFailedError(
            "Choose at least one way to be reminded, or turn reminders off."
        )
    row = await session.scalar(
        select(ReminderPreference)
        .where(ReminderPreference.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = ReminderPreference(patient_id=patient_id, created_by=actor)
        session.add(row)
    for f in FIELDS:
        setattr(row, f, getattr(prefs, f))
    row.updated_by = actor
    await session.flush()
    return prefs
