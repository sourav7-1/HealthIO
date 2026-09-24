"""Medications: the doctor-facing record, and the patient's regimens, reminders and doses.

Every medicine carries its `source`, so the UI can always tell *doctor prescribed* from
*patient self-reported*. Patients never change what a doctor wrote: they choose reminder
times for it, and record whether each dose was taken.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import MealRelation
from app.core.errors import ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.medications import doses, service
from app.modules.medications.models import (
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleType,
)
from app.modules.medications.schedules import local_day_bounds
from app.modules.patients import service as patients
from app.modules.prescriptions import service as prescriptions
from app.modules.prescriptions.models import PrescriptionItem
from app.modules.reminders import service as reminders

router = APIRouter(tags=["medications"])

_view = patient_request(Permission.VIEW_MEDICATIONS)
_write = patient_request(Permission.CHANGE_DOCTOR_PRESCRIPTION)
_adherence = patient_request(Permission.VIEW_ADHERENCE)
_self_report = patient_request(Permission.REPORT_HEALTH_INFO)
_reminders = patient_request(Permission.MANAGE_REMINDERS)
_log = patient_request(Permission.LOG_DOSES)


def _check_tz(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown timezone") from None
    return value


class ExistingMedicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    route: str | None = Field(default=None, max_length=64)
    instructions: str | None = Field(default=None, max_length=1000)
    start_date: date | None = None
    is_prn: bool = False


class ScheduleOut(BaseModel):
    type: ScheduleType
    times_of_day: list[time]
    timezone: str
    meal_relation: MealRelation | None


class MedicationOut(BaseModel):
    id: uuid.UUID
    name: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    route: str | None
    instructions: str | None
    is_prn: bool
    source: MedicationSource
    status: MedicationStatus
    start_date: date | None
    end_date: date | None
    prescription_item_id: uuid.UUID | None
    confirmed_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    prescribed_directions: str | None = Field(
        default=None, description="The prescription line exactly as the doctor wrote it"
    )
    schedule: ScheduleOut | None = None


class AdherenceLineOut(BaseModel):
    medication_id: uuid.UUID
    name: str
    scheduled: int
    taken: int
    skipped: int
    missed: int
    rate: float | None


class AdherenceOut(BaseModel):
    days: int
    has_schedules: bool
    total_recorded: int
    lines: list[AdherenceLineOut]


def _directions(item: PrescriptionItem | None) -> str | None:
    if item is None:
        return None
    parts = [
        f"{item.dose_amount.normalize():f} {item.dose_unit or ''}".strip()
        if item.dose_amount
        else None,
        item.frequency_text,
        item.meal_relation.value.replace("_", " ") if item.meal_relation else None,
        f"for {item.duration_days} days" if item.duration_days else None,
        f"when needed: {item.prn_reason}" if item.is_prn and item.prn_reason else None,
        item.instructions,
    ]
    return " · ".join(p for p in parts if p) or None


def _out(
    m: Medication,
    schedule: MedicationSchedule | None = None,
    item: PrescriptionItem | None = None,
) -> MedicationOut:
    out = MedicationOut.model_validate(m, from_attributes=True)
    out.prescribed_directions = _directions(item)
    if schedule is not None:
        out.schedule = ScheduleOut(
            type=schedule.schedule_type,
            times_of_day=list(schedule.times_of_day),
            timezone=schedule.timezone,
            meal_relation=schedule.meal_relation,
        )
    return out


async def _outs(ctx: PatientRequest, meds: list[Medication]) -> list[MedicationOut]:
    schedules = await doses.active_schedules(ctx.session, ctx.patient_id)
    items = await prescriptions.items_by_ids(
        ctx.session,
        ctx.patient_id,
        [m.prescription_item_id for m in meds if m.prescription_item_id],
    )
    return [
        _out(
            m,
            schedules.get(m.id),
            items.get(m.prescription_item_id) if m.prescription_item_id else None,
        )
        for m in meds
    ]


async def _one(ctx: PatientRequest, medication_id: uuid.UUID) -> MedicationOut:
    med = next(
        m
        for m in await service.list_for_patient(ctx.session, ctx.patient_id)
        if m.id == medication_id
    )
    return (await _outs(ctx, [med]))[0]


@router.get("/patients/{patient_id}/medications", response_model=list[MedicationOut])
async def list_medications(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[MedicationOut]:
    meds = await service.list_for_patient(ctx.session, ctx.patient_id)
    await ctx.audit("medication.list", resource_type="medication")
    await ctx.session.commit()
    return await _outs(ctx, meds)


@router.post(
    "/patients/{patient_id}/medications",
    response_model=MedicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_existing_medication(
    patient_id: uuid.UUID, body: ExistingMedicationIn, ctx: PatientRequest = _write
) -> MedicationOut:
    """Doctor: record a medicine the patient already takes (e.g. started elsewhere).
    New treatment is written as a prescription instead."""
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    med = await service.record_existing(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        name=body.name,
        strength=body.strength,
        dosage_form=body.dosage_form,
        route=body.route,
        instructions=body.instructions,
        start_date=body.start_date,
        is_prn=body.is_prn,
    )
    await ctx.audit("medication.record_existing", resource_type="medication", resource_id=med.id)
    await ctx.session.commit()
    return _out(med)


@router.get("/patients/{patient_id}/adherence", response_model=AdherenceOut)
async def adherence(patient_id: uuid.UUID, ctx: PatientRequest = _adherence) -> AdherenceOut:
    """Only recorded dose events; nothing is estimated."""
    summary = await service.adherence_summary(ctx.session, ctx.patient_id)
    await ctx.audit("adherence.view", resource_type="medication_adherence")
    await ctx.session.commit()
    return AdherenceOut(
        days=summary.days,
        has_schedules=summary.has_schedules,
        total_recorded=summary.total_recorded,
        lines=[
            AdherenceLineOut(
                medication_id=line.medication_id,
                name=line.name,
                scheduled=line.scheduled,
                taken=line.taken,
                skipped=line.skipped,
                missed=line.missed,
                rate=line.rate,
            )
            for line in summary.lines
        ],
    )


# --- patient-side regimens --------------------------------------------------------------------


class ReminderTimesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    times_of_day: list[time] = Field(default_factory=list, max_length=8)
    timezone: str | None = Field(default=None, description="Defaults to the patient's timezone")
    meal_relation: MealRelation | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        return _check_tz(v)


class SelfReportedIn(ReminderTimesIn):
    name: str = Field(min_length=1, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    instructions: str | None = Field(default=None, max_length=1000)
    start_date: date | None = None
    is_prn: bool = False


class StopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=300)


async def _patient_tz(ctx: PatientRequest, requested: str | None) -> str:
    if requested:
        return requested
    profile = await patients.get_live_profile(ctx.session, ctx.patient_id)
    return profile.timezone if profile else "Asia/Kolkata"


@router.post(
    "/patients/{patient_id}/medications/self-reported",
    response_model=MedicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_self_reported(
    patient_id: uuid.UUID, body: SelfReportedIn, ctx: PatientRequest = _self_report
) -> MedicationOut:
    """A medicine the patient takes that no doctor prescribed here. Always labelled
    patient-reported."""
    med, schedule = await doses.add_self_reported(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        name=body.name,
        strength=body.strength,
        dosage_form=body.dosage_form,
        instructions=body.instructions,
        start_date=body.start_date,
        is_prn=body.is_prn,
        times=body.times_of_day,
        timezone=await _patient_tz(ctx, body.timezone),
        meal_relation=body.meal_relation,
    )
    await ctx.audit("medication.self_reported", resource_type="medication", resource_id=med.id)
    await ctx.session.commit()
    return _out(med, schedule)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/confirm", response_model=MedicationOut
)
async def confirm_medication(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ReminderTimesIn,
    ctx: PatientRequest = _reminders,
) -> MedicationOut:
    """Start a prescribed medicine by choosing reminder times. The prescription itself
    is not changed."""
    med, _ = await doses.confirm(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        times=body.times_of_day,
        timezone=await _patient_tz(ctx, body.timezone),
        meal_relation=body.meal_relation,
    )
    await ctx.audit(
        "medication.confirm",
        resource_type="medication",
        resource_id=med.id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return await _one(ctx, med.id)


@router.put(
    "/patients/{patient_id}/medications/{medication_id}/reminder-times",
    response_model=MedicationOut,
)
async def change_reminder_times(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ReminderTimesIn,
    ctx: PatientRequest = _reminders,
) -> MedicationOut:
    await doses.change_times(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        times=body.times_of_day,
        timezone=await _patient_tz(ctx, body.timezone),
        meal_relation=body.meal_relation,
    )
    await ctx.audit(
        "medication.reminder_times", resource_type="medication", resource_id=medication_id
    )
    await ctx.session.commit()
    return await _one(ctx, medication_id)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/stop", response_model=MedicationOut
)
async def stop_medication(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: StopIn,
    ctx: PatientRequest = _self_report,
) -> MedicationOut:
    """Only self-reported medicines. Prescribed medicines are changed by the doctor."""
    med = await doses.stop_self_reported(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        reason=body.reason,
        source=ctx.reporter_source,
    )
    await ctx.audit(
        "medication.stop_self_reported",
        resource_type="medication",
        resource_id=med.id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return _out(med)


# --- doses -------------------------------------------------------------------------------------


class DoseOut(BaseModel):
    id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    strength: str | None
    instructions: str | None
    meal_relation: MealRelation | None
    source: MedicationSource
    scheduled_at: datetime | None
    status: DoseStatus
    snoozed_until: datetime | None
    snooze_count: int
    taken_at: datetime | None
    skip_reason: str | None
    as_needed: bool


class DoseActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=200)
    minutes: Literal[5, 10, 15, 30, 60] | None = None


class AsNeededIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taken_at: datetime | None = None


def _via(ctx: PatientRequest) -> DoseRecordedVia:
    return (
        DoseRecordedVia.PATIENT_APP if "self" in ctx.access.via else DoseRecordedVia.CAREGIVER_APP
    )


def dose_out(d: MedicationDose, m: Medication, meal: MealRelation | None) -> DoseOut:
    return DoseOut(
        id=d.id,
        medication_id=m.id,
        medication_name=m.name,
        strength=m.strength,
        instructions=m.instructions,
        meal_relation=meal,
        source=m.source,
        scheduled_at=d.scheduled_at,
        status=d.status,
        snoozed_until=d.snoozed_until,
        snooze_count=d.snooze_count,
        taken_at=d.taken_at,
        skip_reason=d.skip_reason,
        as_needed=d.schedule_id is None,
    )


@router.get("/patients/{patient_id}/doses", response_model=list[DoseOut])
async def list_doses(
    patient_id: uuid.UUID,
    day: date | None = None,
    days: int = 1,
    ctx: PatientRequest = _view,
) -> list[DoseOut]:
    """Doses for `days` calendar days from `day` (default: today in the patient's timezone).
    Opening the list also creates upcoming dose rows and records long-unanswered doses as
    missed; the reminder engine (Phase 10) does the same on a schedule."""
    if not 1 <= days <= 7:
        raise ValidationFailedError("days must be between 1 and 7")
    tz = await _patient_tz(ctx, None)
    now = datetime.now(UTC)
    prefs = await reminders.get(ctx.session, ctx.patient_id)
    await doses.materialize(ctx.session, ctx.patient_id, now=now)
    await doses.mark_missed(ctx.session, ctx.patient_id, now=now, after=prefs.missed_after)
    start, _ = local_day_bounds(day or now.astimezone(ZoneInfo(tz)).date(), tz)
    rows = await doses.list_doses(ctx.session, ctx.patient_id, start, start + timedelta(days=days))
    await ctx.audit("dose.list", resource_type="medication_dose")
    await ctx.session.commit()
    return [dose_out(r.dose, r.medication, r.meal_relation) for r in rows]


async def _dose_view(ctx: PatientRequest, dose: MedicationDose) -> DoseOut:
    at = dose.scheduled_at or dose.taken_at or datetime.now(UTC)
    rows = await doses.list_doses(
        ctx.session, ctx.patient_id, at - timedelta(seconds=1), at + timedelta(seconds=1)
    )
    row = next(r for r in rows if r.dose.id == dose.id)
    return dose_out(row.dose, row.medication, row.meal_relation)


@router.post("/patients/{patient_id}/doses/{dose_id}/{action}", response_model=DoseOut)
async def record_dose(
    patient_id: uuid.UUID,
    dose_id: uuid.UUID,
    action: Literal["take", "skip", "snooze"],
    body: DoseActionIn,
    ctx: PatientRequest = _log,
) -> DoseOut:
    minutes: int | None = body.minutes
    if action == "snooze" and minutes is None:
        minutes = (await reminders.get(ctx.session, ctx.patient_id)).default_snooze_minutes
    dose = await doses.record_dose(
        ctx.session,
        patient_id=ctx.patient_id,
        dose_id=dose_id,
        actor=ctx.actor_id,
        via=_via(ctx),
        action=action,
        reason=body.reason,
        snooze_minutes=minutes,
    )
    await ctx.audit(
        f"dose.{action}",
        resource_type="medication_dose",
        resource_id=dose.id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return await _dose_view(ctx, dose)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/as-needed-dose",
    response_model=DoseOut,
    status_code=status.HTTP_201_CREATED,
)
async def log_as_needed(
    patient_id: uuid.UUID, medication_id: uuid.UUID, body: AsNeededIn, ctx: PatientRequest = _log
) -> DoseOut:
    dose = await doses.log_as_needed(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        via=_via(ctx),
        taken_at=body.taken_at,
    )
    await ctx.audit("dose.as_needed", resource_type="medication_dose", resource_id=dose.id)
    await ctx.session.commit()
    return await _dose_view(ctx, dose)
