"""Regimens and dose events for the patient side.

- Regimen changes (confirming, schedule changes, pause, stop) live in regimen.py.
- When a schedule ends, its future unanswered doses are cancelled (history is kept).
- Dose rows are materialised idempotently for a rolling window (unique on
  schedule_id + scheduled_at). The reminder engine (roadmap Phase 10) runs the same code
  on a schedule; the API also runs it when a patient opens their doses.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MealRelation, RecordSource
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.ids import uuid7
from app.modules.medications.models import (
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationSchedule,
    MedicationStatus,
    ScheduleStatus,
    ScheduleType,
)
from app.modules.medications.schedules import (
    InvalidPattern,
    expand,
    expand_interval,
    parse_rule,
)

MAX_TIMES_PER_DAY = 8
MAX_SNOOZES = 3
OPEN = (DoseStatus.SCHEDULED, DoseStatus.NOTIFIED, DoseStatus.SNOOZED)


async def _medication(
    session: AsyncSession, patient_id: uuid.UUID, medication_id: uuid.UUID
) -> Medication:
    med: Medication | None = await session.scalar(
        select(Medication)
        .where(Medication.id == medication_id, Medication.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if med is None:
        raise NotFoundError()
    return med


async def active_schedules(
    session: AsyncSession, patient_id: uuid.UUID
) -> dict[uuid.UUID, MedicationSchedule]:
    rows = await session.scalars(
        select(MedicationSchedule).where(
            MedicationSchedule.patient_id == patient_id,
            MedicationSchedule.status == ScheduleStatus.ACTIVE,
        )
    )
    return {s.medication_id: s for s in rows.all()}


async def _end_schedule(
    session: AsyncSession,
    schedule: MedicationSchedule,
    actor: uuid.UUID,
    now: datetime,
    reason: str,
) -> None:
    schedule.status = ScheduleStatus.ENDED
    schedule.effective_until = now
    schedule.ended_reason = reason
    schedule.updated_by = actor
    # Unanswered future doses of the old schedule no longer apply.
    await session.execute(
        update(MedicationDose)
        .where(
            MedicationDose.schedule_id == schedule.id,
            MedicationDose.status.in_(OPEN),
            MedicationDose.scheduled_at >= now,
        )
        .values(status=DoseStatus.CANCELLED, snoozed_until=None, updated_by=actor)
        .execution_options(synchronize_session=False)
    )


# --- dose events -----------------------------------------------------------------------------


async def materialize(
    session: AsyncSession,
    patient_id: uuid.UUID,
    *,
    now: datetime,
    horizon: timedelta = timedelta(hours=48),
) -> int:
    """Create dose rows for active fixed-time schedules from 24 h ago to `horizon` ahead.
    Safe to run repeatedly (ON CONFLICT DO NOTHING)."""
    rows = (
        await session.execute(
            select(MedicationSchedule, Medication)
            .join(
                Medication,
                (Medication.id == MedicationSchedule.medication_id)
                & (Medication.patient_id == MedicationSchedule.patient_id),
            )
            .where(
                MedicationSchedule.patient_id == patient_id,
                MedicationSchedule.status == ScheduleStatus.ACTIVE,
                MedicationSchedule.schedule_type.in_(
                    [ScheduleType.FIXED_TIMES, ScheduleType.INTERVAL]
                ),
                Medication.status == MedicationStatus.ACTIVE,
            )
        )
    ).all()
    values = []
    for schedule, med in rows:
        window = {
            "timezone": schedule.timezone,
            "window_start": now - timedelta(hours=24),
            "window_end": now + horizon,
            "effective_from": schedule.effective_from,
            "effective_until": schedule.effective_until,
            "start_date": med.start_date,
            "end_date": med.end_date,
        }
        if schedule.schedule_type == ScheduleType.INTERVAL:
            if not schedule.interval_minutes or not schedule.times_of_day:
                continue
            instants = expand_interval(
                interval_minutes=schedule.interval_minutes,
                anchor_time=schedule.times_of_day[0],
                **window,
            )
        else:
            try:
                pattern = parse_rule(schedule.recurrence_rule)
            except InvalidPattern:
                continue  # never guess a day pattern
            instants = expand(times_of_day=schedule.times_of_day, pattern=pattern, **window)
        for at in instants:
            values.append(
                {
                    "id": uuid7(),
                    "patient_id": patient_id,
                    "medication_id": med.id,
                    "schedule_id": schedule.id,
                    "scheduled_at": at,
                    "status": DoseStatus.SCHEDULED.value,
                    "snooze_count": 0,
                    "dose_amount": schedule.dose_amount,
                    "dose_unit": schedule.dose_unit,
                }
            )
    if not values:
        return 0
    stmt = (
        insert(MedicationDose)
        .values(values)
        .on_conflict_do_nothing(index_elements=["schedule_id", "scheduled_at"])
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def mark_missed(
    session: AsyncSession, patient_id: uuid.UUID, *, now: datetime, after: timedelta
) -> int:
    """Doses with no answer long after their time (or their snooze) become MISSED."""
    cutoff = now - after
    result = await session.execute(
        update(MedicationDose)
        .where(
            MedicationDose.patient_id == patient_id,
            MedicationDose.status.in_(OPEN),
            MedicationDose.scheduled_at < cutoff,
            (MedicationDose.snoozed_until.is_(None)) | (MedicationDose.snoozed_until < cutoff),
        )
        .values(status=DoseStatus.MISSED, snoozed_until=None, recorded_via=DoseRecordedVia.SYSTEM)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class DoseView:
    dose: MedicationDose
    medication: Medication
    meal_relation: MealRelation | None


async def list_doses(
    session: AsyncSession, patient_id: uuid.UUID, start: datetime, end: datetime
) -> list[DoseView]:
    rows = await session.execute(
        select(MedicationDose, Medication, MedicationSchedule.meal_relation)
        .join(
            Medication,
            (Medication.id == MedicationDose.medication_id)
            & (Medication.patient_id == MedicationDose.patient_id),
        )
        .outerjoin(MedicationSchedule, MedicationSchedule.id == MedicationDose.schedule_id)
        .where(
            MedicationDose.patient_id == patient_id,
            MedicationDose.status != DoseStatus.CANCELLED,
            ((MedicationDose.scheduled_at >= start) & (MedicationDose.scheduled_at < end))
            | (
                MedicationDose.schedule_id.is_(None)
                & (MedicationDose.taken_at >= start)
                & (MedicationDose.taken_at < end)
            ),
        )
        .order_by(MedicationDose.scheduled_at.nulls_last(), MedicationDose.taken_at)
    )
    return [DoseView(d, m, meal) for d, m, meal in rows]


async def _dose(session: AsyncSession, patient_id: uuid.UUID, dose_id: uuid.UUID) -> MedicationDose:
    dose: MedicationDose | None = await session.scalar(
        select(MedicationDose)
        .where(MedicationDose.id == dose_id, MedicationDose.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if dose is None:
        raise NotFoundError()
    return dose


async def record_dose(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    dose_id: uuid.UUID,
    actor: uuid.UUID,
    via: DoseRecordedVia,
    action: str,
    reason: str | None = None,
    snooze_minutes: int | None = None,
    now: datetime | None = None,
) -> MedicationDose:
    """take | skip | snooze. A missed dose can still be recorded as taken (late) or skipped."""
    now = now or datetime.now(UTC)
    dose = await _dose(session, patient_id, dose_id)
    if dose.scheduled_at and dose.scheduled_at > now + timedelta(hours=12):
        raise ConflictError("This dose is not due yet.")
    if action == "take":
        if dose.status not in (*OPEN, DoseStatus.MISSED, DoseStatus.SKIPPED):
            raise ConflictError("This dose has already been recorded.")
        dose.status = DoseStatus.TAKEN
        dose.taken_at = now
        dose.snoozed_until = None
        dose.skip_reason = None
    elif action == "skip":
        if dose.status not in (*OPEN, DoseStatus.MISSED):
            raise ConflictError("This dose has already been recorded.")
        dose.status = DoseStatus.SKIPPED
        dose.skip_reason = reason
        dose.snoozed_until = None
    elif action == "snooze":
        if dose.status not in OPEN:
            raise ConflictError("Only an upcoming dose can be snoozed.")
        if dose.snooze_count >= MAX_SNOOZES:
            raise ConflictError(f"A reminder can be snoozed at most {MAX_SNOOZES} times.")
        minutes = snooze_minutes or 10
        base = max(now, dose.snoozed_until or now)
        dose.status = DoseStatus.SNOOZED
        dose.snoozed_until = base + timedelta(minutes=minutes)
        dose.snooze_count += 1
    else:
        raise NotFoundError()
    dose.recorded_by = actor
    dose.recorded_via = via
    dose.updated_by = actor
    await session.flush()
    return dose


async def log_as_needed(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    via: DoseRecordedVia,
    taken_at: datetime | None,
) -> MedicationDose:
    med = await _medication(session, patient_id, medication_id)
    if not med.is_prn or med.status != MedicationStatus.ACTIVE:
        raise ConflictError("Only an active 'when needed' medicine can be logged this way.")
    now = datetime.now(UTC)
    when = taken_at or now
    if when > now + timedelta(minutes=5) or when < now - timedelta(days=7):
        raise ValidationFailedError("The time taken must be within the last 7 days.")
    dose = MedicationDose(
        patient_id=patient_id,
        medication_id=med.id,
        status=DoseStatus.TAKEN,
        taken_at=when,
        recorded_by=actor,
        recorded_via=via,
        created_by=actor,
        updated_by=actor,
    )
    session.add(dose)
    await session.flush()
    return dose


async def stop_for_prescription_items(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    actor: uuid.UUID,
    reason: str,
) -> int:
    """The prescriber cancelled or corrected a prescription: its medicines stop (recorded
    as stopped by the doctor, with the reason) and their future reminders are cancelled."""
    if not item_ids:
        return 0
    meds = list(
        (
            await session.scalars(
                select(Medication)
                .where(
                    Medication.patient_id == patient_id,
                    Medication.prescription_item_id.in_(item_ids),
                    Medication.status.in_(
                        [
                            MedicationStatus.PENDING_CONFIRMATION,
                            MedicationStatus.ACTIVE,
                            MedicationStatus.PAUSED,
                        ]
                    ),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    if not meds:
        return 0
    now = datetime.now(UTC)
    schedules = await active_schedules(session, patient_id)
    for med in meds:
        current = schedules.get(med.id)
        if current is not None:
            await _end_schedule(session, current, actor, now, reason)
        med.status = MedicationStatus.STOPPED
        med.stopped_at = now
        med.stopped_by = actor
        med.stop_source = RecordSource.DOCTOR
        med.stop_reason = reason[:200]
        today = now.date()
        med.end_date = max(today, med.start_date) if med.start_date else today
        med.updated_by = actor
    await session.flush()
    return len(meds)
