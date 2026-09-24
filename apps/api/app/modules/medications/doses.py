"""Regimens and dose events for the patient side.

- A prescribed medicine becomes ACTIVE only when the patient (or an authorised caregiver)
  confirms it and chooses reminder times. The doctor's prescription is never changed.
- Patients may add self-reported medicines and stop only those; prescribed medicines are
  stopped by the prescriber.
- Reminder times can change at any time: the current schedule is ended (kept as history)
  and a new one starts. Future unanswered doses of the old schedule are cancelled.
- Dose rows are materialised idempotently for a rolling window (unique on
  schedule_id + scheduled_at). The reminder engine (roadmap Phase 10) runs the same code
  on a schedule; the API also runs it when a patient opens their doses.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MealRelation, RecordSource
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.ids import uuid7
from app.modules.medications.models import (
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleStatus,
    ScheduleType,
)
from app.modules.medications.schedules import expand

MAX_TIMES_PER_DAY = 8
MAX_SNOOZES = 3
OPEN = (DoseStatus.SCHEDULED, DoseStatus.SNOOZED)


def _validate_times(times: list[time], is_prn: bool) -> list[time]:
    if is_prn:
        return []
    unique = sorted({t.replace(second=0, microsecond=0) for t in times})
    if not unique:
        raise ValidationFailedError("Choose at least one time for reminders.")
    if len(unique) > MAX_TIMES_PER_DAY:
        raise ValidationFailedError(f"At most {MAX_TIMES_PER_DAY} times a day.")
    return unique


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


def _new_schedule(
    med: Medication,
    *,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
    actor: uuid.UUID,
    now: datetime,
    supersedes: uuid.UUID | None = None,
) -> MedicationSchedule:
    return MedicationSchedule(
        patient_id=med.patient_id,
        medication_id=med.id,
        schedule_type=ScheduleType.AS_NEEDED if med.is_prn else ScheduleType.FIXED_TIMES,
        times_of_day=times,
        timezone=timezone,
        meal_relation=meal_relation,
        effective_from=now,
        supersedes_schedule_id=supersedes,
        created_by=actor,
        updated_by=actor,
    )


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


async def confirm(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
) -> tuple[Medication, MedicationSchedule]:
    """The patient accepts a prescribed medicine and chooses when to be reminded."""
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.PENDING_CONFIRMATION:
        raise ConflictError("This medicine has already been confirmed.")
    clean = _validate_times(times, med.is_prn)
    now = datetime.now(UTC)
    med.status = MedicationStatus.ACTIVE
    med.confirmed_at = now
    med.confirmed_by = actor
    med.updated_by = actor
    schedule = _new_schedule(
        med, times=clean, timezone=timezone, meal_relation=meal_relation, actor=actor, now=now
    )
    session.add(schedule)
    await session.flush()
    return med, schedule


async def add_self_reported(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    strength: str | None,
    dosage_form: str | None,
    instructions: str | None,
    start_date: date | None,
    is_prn: bool,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
) -> tuple[Medication, MedicationSchedule]:
    if not name.strip():
        raise ValidationFailedError("Enter the medicine's name.")
    clean = _validate_times(times, is_prn)
    now = datetime.now(UTC)
    med = Medication(
        patient_id=patient_id,
        source=MedicationSource.SELF_REPORTED,
        name=name.strip(),
        strength=strength,
        dosage_form=dosage_form,
        instructions=instructions,
        is_prn=is_prn,
        status=MedicationStatus.ACTIVE,
        start_date=start_date,
        confirmed_at=now,
        confirmed_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    session.add(med)
    await session.flush()
    schedule = _new_schedule(
        med, times=clean, timezone=timezone, meal_relation=meal_relation, actor=actor, now=now
    )
    session.add(schedule)
    await session.flush()
    return med, schedule


async def change_times(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
) -> MedicationSchedule:
    """Change reminder times only. The medicine and any prescription stay unchanged."""
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.ACTIVE:
        raise ConflictError("Reminder times can be changed only for an active medicine.")
    clean = _validate_times(times, med.is_prn)
    now = datetime.now(UTC)
    current = (await active_schedules(session, patient_id)).get(med.id)
    if current is not None:
        await _end_schedule(session, current, actor, now, "reminder times changed")
    schedule = _new_schedule(
        med,
        times=clean,
        timezone=timezone,
        meal_relation=meal_relation
        if meal_relation is not None
        else (current.meal_relation if current else None),
        actor=actor,
        now=now,
        supersedes=current.id if current else None,
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def stop_self_reported(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str | None,
    source: RecordSource = RecordSource.PATIENT,
) -> Medication:
    med = await _medication(session, patient_id, medication_id)
    if med.source != MedicationSource.SELF_REPORTED:
        raise ForbiddenError(
            "Medicines prescribed or recorded by a doctor can only be changed by the doctor. "
            "Talk to your doctor before stopping a prescribed medicine."
        )
    if med.status not in (MedicationStatus.ACTIVE, MedicationStatus.PAUSED):
        raise ConflictError("This medicine is not active.")
    now = datetime.now(UTC)
    current = (await active_schedules(session, patient_id)).get(med.id)
    if current is not None:
        await _end_schedule(session, current, actor, now, "medicine stopped")
    med.status = MedicationStatus.STOPPED
    med.stopped_at = now
    med.stopped_by = actor
    med.stop_source = source
    med.stop_reason = reason
    med.end_date = now.date()
    med.updated_by = actor
    await session.flush()
    return med


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
                MedicationSchedule.schedule_type == ScheduleType.FIXED_TIMES,
                Medication.status == MedicationStatus.ACTIVE,
            )
        )
    ).all()
    values = []
    for schedule, med in rows:
        for at in expand(
            times_of_day=schedule.times_of_day,
            timezone=schedule.timezone,
            window_start=now - timedelta(hours=24),
            window_end=now + horizon,
            effective_from=schedule.effective_from,
            effective_until=schedule.effective_until,
            start_date=med.start_date,
            end_date=med.end_date,
        ):
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
