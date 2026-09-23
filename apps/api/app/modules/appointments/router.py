import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.appointments import service
from app.modules.appointments.models import (
    Appointment,
    AppointmentMode,
    AppointmentStatus,
    FollowUp,
    FollowUpStatus,
)
from app.modules.care_team import service as care_team

router = APIRouter(tags=["appointments"])

_view = patient_request(Permission.VIEW_APPOINTMENTS)
_book = patient_request(Permission.MANAGE_APPOINTMENTS)
_follow_up = patient_request(Permission.EDIT_CLINICAL_RECORDS)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AppointmentIn(_In):
    starts_at: datetime
    duration_minutes: int = Field(default=15, ge=5, le=240)
    mode: AppointmentMode = AppointmentMode.IN_PERSON
    reason: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    follow_up_id: uuid.UUID | None = None


class AppointmentOut(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    mode: AppointmentMode
    reason: str | None
    location: str | None
    doctor_id: uuid.UUID
    doctor_name: str | None
    follow_up_id: uuid.UUID | None


class FollowUpIn(_In):
    due_date: date
    reason: str | None = Field(default=None, max_length=500)
    source_visit_id: uuid.UUID | None = None
    source_prescription_id: uuid.UUID | None = None


class FollowUpOut(BaseModel):
    id: uuid.UUID
    due_date: date
    reason: str | None
    status: FollowUpStatus
    doctor_id: uuid.UUID
    doctor_name: str | None
    source_visit_id: uuid.UUID | None
    completed_at: datetime | None


async def appointments_out(session: AsyncSession, rows: list[Appointment]) -> list[AppointmentOut]:
    names = await care_team.doctor_names(session, {a.doctor_id for a in rows})
    return [
        AppointmentOut(
            id=a.id,
            starts_at=a.starts_at,
            ends_at=a.ends_at,
            status=a.status,
            mode=a.mode,
            reason=a.reason,
            location=a.location,
            doctor_id=a.doctor_id,
            doctor_name=names.get(a.doctor_id),
            follow_up_id=a.follow_up_id,
        )
        for a in rows
    ]


async def follow_ups_out(session: AsyncSession, rows: list[FollowUp]) -> list[FollowUpOut]:
    names = await care_team.doctor_names(session, {f.doctor_id for f in rows})
    return [
        FollowUpOut(
            id=f.id,
            due_date=f.due_date,
            reason=f.reason,
            status=f.status,
            doctor_id=f.doctor_id,
            doctor_name=names.get(f.doctor_id),
            source_visit_id=f.source_visit_id,
            completed_at=f.completed_at,
        )
        for f in rows
    ]


@router.get("/patients/{patient_id}/appointments", response_model=list[AppointmentOut])
async def list_appointments(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[AppointmentOut]:
    rows = await service.list_for_patient(ctx.session, ctx.patient_id)
    await ctx.audit("appointment.list", resource_type="appointment")
    await ctx.session.commit()
    return await appointments_out(ctx.session, rows)


@router.post(
    "/patients/{patient_id}/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def book_appointment(
    patient_id: uuid.UUID, body: AppointmentIn, ctx: PatientRequest = _book
) -> AppointmentOut:
    """A doctor books the patient into their own schedule (409 if the slot overlaps)."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    appt = await service.book(
        ctx.session,
        patient_id=ctx.patient_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        starts_at=body.starts_at,
        duration_minutes=body.duration_minutes,
        mode=body.mode,
        reason=body.reason,
        location=body.location,
        follow_up_id=body.follow_up_id,
    )
    await ctx.audit("appointment.book", resource_type="appointment", resource_id=appt.id)
    await ctx.session.commit()
    return (await appointments_out(ctx.session, [appt]))[0]


@router.get("/patients/{patient_id}/follow-ups", response_model=list[FollowUpOut])
async def list_follow_ups(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[FollowUpOut]:
    rows = await service.list_follow_ups(ctx.session, ctx.patient_id)
    await ctx.audit("follow_up.list", resource_type="follow_up")
    await ctx.session.commit()
    return await follow_ups_out(ctx.session, rows)


@router.post(
    "/patients/{patient_id}/follow-ups",
    response_model=FollowUpOut,
    status_code=status.HTTP_201_CREATED,
)
async def set_follow_up(
    patient_id: uuid.UUID, body: FollowUpIn, ctx: PatientRequest = _follow_up
) -> FollowUpOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.set_follow_up(
        ctx.session,
        patient_id=ctx.patient_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        due_date=body.due_date,
        reason=body.reason,
        source_visit_id=body.source_visit_id,
        source_prescription_id=body.source_prescription_id,
    )
    await ctx.audit("follow_up.create", resource_type="follow_up", resource_id=row.id)
    await ctx.session.commit()
    return (await follow_ups_out(ctx.session, [row]))[0]


@router.post(
    "/patients/{patient_id}/follow-ups/{follow_up_id}/{outcome}", response_model=FollowUpOut
)
async def close_follow_up(
    patient_id: uuid.UUID,
    follow_up_id: uuid.UUID,
    outcome: Literal["complete", "cancel"],
    ctx: PatientRequest = _follow_up,
) -> FollowUpOut:
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.close_follow_up(
        ctx.session,
        patient_id=ctx.patient_id,
        follow_up_id=follow_up_id,
        actor=ctx.actor_id,
        completed=outcome == "complete",
    )
    await ctx.audit(
        f"follow_up.{outcome}",
        resource_type="follow_up",
        resource_id=row.id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return (await follow_ups_out(ctx.session, [row]))[0]
