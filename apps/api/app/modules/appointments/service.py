"""Appointments and follow-ups. Double booking is rejected by the database (exclusion
constraint) and surfaces as 409 Conflict."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.modules.appointments.models import (
    Appointment,
    AppointmentMode,
    AppointmentStatus,
    FollowUp,
    FollowUpStatus,
)

LIVE = (
    AppointmentStatus.REQUESTED,
    AppointmentStatus.SCHEDULED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CHECKED_IN,
)


async def list_for_patient(session: AsyncSession, patient_id: uuid.UUID) -> list[Appointment]:
    rows = await session.scalars(
        select(Appointment)
        .where(Appointment.patient_id == patient_id)
        .order_by(Appointment.starts_at.desc())
    )
    return list(rows.all())


async def book(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    starts_at: datetime,
    duration_minutes: int,
    mode: AppointmentMode,
    reason: str | None,
    location: str | None,
    follow_up_id: uuid.UUID | None,
) -> Appointment:
    if starts_at.tzinfo is None:
        raise ValidationFailedError("Start time must include a timezone.")
    if starts_at < datetime.now(UTC) - timedelta(minutes=5):
        raise ValidationFailedError("Appointments cannot be booked in the past.")
    if not 5 <= duration_minutes <= 240:
        raise ValidationFailedError("Duration must be between 5 and 240 minutes.")
    if follow_up_id is not None:
        follow_up = await _follow_up(session, patient_id, follow_up_id)
        if follow_up.status != FollowUpStatus.OPEN:
            raise ConflictError("This follow-up is not open.")
        follow_up.status = FollowUpStatus.BOOKED
        follow_up.updated_by = actor
    appt = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=duration_minutes),
        status=AppointmentStatus.SCHEDULED,
        mode=mode,
        reason=reason,
        location=location,
        follow_up_id=follow_up_id,
        booked_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    session.add(appt)
    await session.flush()
    return appt


async def for_doctor_between(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    patient_ids: list[uuid.UUID],
    start: datetime,
    end: datetime,
) -> list[Appointment]:
    if not patient_ids:
        return []
    rows = await session.scalars(
        select(Appointment)
        .where(
            Appointment.doctor_id == doctor_id,
            Appointment.patient_id.in_(patient_ids),
            Appointment.starts_at >= start,
            Appointment.starts_at < end,
            Appointment.status.in_([*LIVE, AppointmentStatus.COMPLETED]),
        )
        .order_by(Appointment.starts_at)
    )
    return list(rows.all())


# --- follow-ups ----------------------------------------------------------------------


async def _follow_up(
    session: AsyncSession, patient_id: uuid.UUID, follow_up_id: uuid.UUID
) -> FollowUp:
    row: FollowUp | None = await session.scalar(
        select(FollowUp)
        .where(FollowUp.id == follow_up_id, FollowUp.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError()
    return row


async def list_follow_ups(session: AsyncSession, patient_id: uuid.UUID) -> list[FollowUp]:
    rows = await session.scalars(
        select(FollowUp).where(FollowUp.patient_id == patient_id).order_by(FollowUp.due_date.desc())
    )
    return list(rows.all())


async def set_follow_up(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    due_date: date,
    reason: str | None,
    source_visit_id: uuid.UUID | None,
    source_prescription_id: uuid.UUID | None,
) -> FollowUp:
    if due_date < datetime.now(UTC).date() - timedelta(days=1):
        raise ValidationFailedError("A follow-up date cannot be in the past.")
    row = FollowUp(
        patient_id=patient_id,
        doctor_id=doctor_id,
        due_date=due_date,
        reason=reason,
        source_visit_id=source_visit_id,
        source_prescription_id=source_prescription_id,
        created_by=actor,
        updated_by=actor,
    )
    session.add(row)
    await session.flush()
    return row


async def close_follow_up(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    follow_up_id: uuid.UUID,
    actor: uuid.UUID,
    completed: bool,
) -> FollowUp:
    row = await _follow_up(session, patient_id, follow_up_id)
    if row.status not in (FollowUpStatus.OPEN, FollowUpStatus.BOOKED):
        raise ConflictError("This follow-up is already closed.")
    row.status = FollowUpStatus.COMPLETED if completed else FollowUpStatus.CANCELLED
    if completed:
        row.completed_at = datetime.now(UTC)
    row.updated_by = actor
    await session.flush()
    return row


async def upcoming_for_doctor(
    session: AsyncSession, doctor_id: uuid.UUID, patient_ids: list[uuid.UUID], until: date
) -> list[FollowUp]:
    if not patient_ids:
        return []
    rows = await session.scalars(
        select(FollowUp)
        .where(
            FollowUp.doctor_id == doctor_id,
            FollowUp.patient_id.in_(patient_ids),
            FollowUp.status.in_([FollowUpStatus.OPEN, FollowUpStatus.BOOKED]),
            FollowUp.due_date <= until,
        )
        .order_by(FollowUp.due_date)
        .limit(20)
    )
    return list(rows.all())
