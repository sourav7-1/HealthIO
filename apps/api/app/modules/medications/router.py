"""Medications: the doctor-facing record, and the patient's regimens, reminders and doses.

Every medicine carries its `origin` (doctor e-prescription, paper prescription read by AI
or typed in and checked, added by the patient, recorded by a doctor), shown as its label.
Patients never change what a doctor wrote. They manage their regimen; clinically
relevant changes to a prescribed medicine need a doctor's approval or the patient's
record of the doctor or pharmacist who advised it (change_policy.py).
"""

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import MealRelation
from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.identity import service as identity
from app.modules.medications import change_policy, doses, regimen, service
from app.modules.medications.models import (
    ActorRole,
    AdvisorRole,
    ChangeRequestKind,
    ChangeRequestStatus,
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationChangeRequest,
    MedicationDose,
    MedicationEventType,
    MedicationOrigin,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleType,
)
from app.modules.medications.schedules import (
    DAILY,
    DayPattern,
    InvalidPattern,
    local_day_bounds,
    parse_rule,
)
from app.modules.patients import service as patients
from app.modules.prescriptions import service as prescriptions
from app.modules.prescriptions.models import Prescription, PrescriptionItem
from app.modules.reminders import service as reminders
from app.modules.safety import service as safety

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


class PatternOut(BaseModel):
    kind: Literal["daily", "every_n_days", "weekdays"]
    every: int
    weekdays: list[int]


class ScheduleOut(BaseModel):
    type: ScheduleType
    times_of_day: list[time]
    interval_minutes: int | None
    pattern: PatternOut
    pattern_text: str
    dose_amount: Decimal | None
    dose_unit: str | None
    timezone: str
    meal_relation: MealRelation | None
    effective_from: datetime


class PrescribedOut(BaseModel):
    """The prescription line as the doctor wrote it (never changed by the patient)."""

    frequency_text: str | None
    times_per_day: int | None
    dose_amount: Decimal | None
    dose_unit: str | None
    meal_relation: MealRelation | None
    duration_days: int | None
    is_prn: bool
    prescription_id: uuid.UUID
    verification_status: str


class DuplicateOut(BaseModel):
    medication_id: uuid.UUID
    name: str
    origin: MedicationOrigin
    kind: Literal["same_name", "same_generic"]


class ChangeRequestOut(BaseModel):
    id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    kind: ChangeRequestKind
    status: ChangeRequestStatus
    requester_role: ActorRole
    message: str | None
    proposed: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


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
    origin: MedicationOrigin
    status: MedicationStatus
    start_date: date | None
    end_date: date | None
    prescription_item_id: uuid.UUID | None
    confirmed_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    paused_at: datetime | None
    pause_reason: str | None
    resume_on: date | None
    prescribed_directions: str | None = Field(
        default=None, description="The prescription line exactly as the doctor wrote it"
    )
    prescribed: PrescribedOut | None = None
    schedule: ScheduleOut | None = None
    duplicates: list[DuplicateOut] = Field(default_factory=list)
    pending_request: ChangeRequestOut | None = None


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


def _schedule_out(schedule: MedicationSchedule) -> ScheduleOut:
    try:
        pattern = parse_rule(schedule.recurrence_rule)
    except InvalidPattern:
        pattern = DAILY
    return ScheduleOut(
        type=schedule.schedule_type,
        times_of_day=list(schedule.times_of_day),
        interval_minutes=schedule.interval_minutes,
        pattern=PatternOut(
            kind=pattern.kind,
            every=pattern.every,
            weekdays=sorted(pattern.weekdays),
        ),
        pattern_text=pattern.describe(),
        dose_amount=schedule.dose_amount,
        dose_unit=schedule.dose_unit,
        timezone=schedule.timezone,
        meal_relation=schedule.meal_relation,
        effective_from=schedule.effective_from,
    )


def request_out(req: MedicationChangeRequest, name: str) -> ChangeRequestOut:
    return ChangeRequestOut(
        id=req.id,
        medication_id=req.medication_id,
        medication_name=name,
        kind=req.kind,
        status=req.status,
        requester_role=req.requester_role,
        message=req.message,
        proposed=json.loads(req.proposed) if req.proposed else None,
        created_at=req.created_at,
        resolved_at=req.resolved_at,
        resolution_note=req.resolution_note,
    )


def _out(
    m: Medication,
    schedule: MedicationSchedule | None = None,
    item: PrescriptionItem | None = None,
    rx: Prescription | None = None,
) -> MedicationOut:
    out = MedicationOut.model_validate(m, from_attributes=True)
    out.prescribed_directions = _directions(item)
    if item is not None and rx is not None:
        out.prescribed = PrescribedOut(
            frequency_text=item.frequency_text,
            times_per_day=item.times_per_day,
            dose_amount=item.dose_amount,
            dose_unit=item.dose_unit,
            meal_relation=item.meal_relation,
            duration_days=item.duration_days,
            is_prn=item.is_prn,
            prescription_id=rx.id,
            verification_status=rx.verification_status.value,
        )
    if schedule is not None:
        out.schedule = _schedule_out(schedule)
    return out


async def _outs(ctx: PatientRequest, meds: list[Medication]) -> list[MedicationOut]:
    schedules = await doses.active_schedules(ctx.session, ctx.patient_id)
    items = await prescriptions.items_by_ids(
        ctx.session,
        ctx.patient_id,
        [m.prescription_item_id for m in meds if m.prescription_item_id],
    )
    rxs = await prescriptions.by_ids(
        ctx.session, ctx.patient_id, {i.prescription_id for i in items.values()}
    )
    all_meds = {m.id: m for m in await service.list_for_patient(ctx.session, ctx.patient_id)}
    dups: dict[uuid.UUID, list[DuplicateOut]] = {}
    for d in await regimen.duplicates(ctx.session, ctx.patient_id):
        other = all_meds.get(d.duplicate_id)
        if other is not None:
            dups.setdefault(d.medication_id, []).append(
                DuplicateOut(
                    medication_id=other.id, name=other.name, origin=other.origin, kind=d.kind
                )
            )
    pending = {
        r.medication_id: r
        for r in await regimen.list_requests(ctx.session, ctx.patient_id)
        if r.status == ChangeRequestStatus.PENDING
    }
    out = []
    for m in meds:
        item = items.get(m.prescription_item_id) if m.prescription_item_id else None
        o = _out(m, schedules.get(m.id), item, rxs.get(item.prescription_id) if item else None)
        o.duplicates = dups.get(m.id, [])
        if m.id in pending:
            o.pending_request = request_out(pending[m.id], m.name)
        out.append(o)
    return out


async def _one(ctx: PatientRequest, medication_id: uuid.UUID) -> MedicationOut:
    med = next(
        (
            m
            for m in await service.list_for_patient(ctx.session, ctx.patient_id)
            if m.id == medication_id
        ),
        None,
    )
    if med is None:
        raise NotFoundError()
    return (await _outs(ctx, [med]))[0]


async def _today(ctx: PatientRequest) -> date:
    tz = await _patient_tz(ctx, None)
    return datetime.now(UTC).astimezone(ZoneInfo(tz)).date()


@router.get("/patients/{patient_id}/medications", response_model=list[MedicationOut])
async def list_medications(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[MedicationOut]:
    """All medicines: in use, paused, waiting to be set up, completed and discontinued."""
    await regimen.complete_finished(ctx.session, ctx.patient_id, await _today(ctx))
    meds = await service.list_for_patient(ctx.session, ctx.patient_id)
    await ctx.audit("medication.list", resource_type="medication")
    await ctx.session.commit()
    return await _outs(ctx, meds)


@router.get("/patients/{patient_id}/medications/{medication_id}", response_model=MedicationOut)
async def get_medication(
    patient_id: uuid.UUID, medication_id: uuid.UUID, ctx: PatientRequest = _view
) -> MedicationOut:
    out = await _one(ctx, medication_id)
    await ctx.audit("medication.read", resource_type="medication", resource_id=medication_id)
    await ctx.session.commit()
    return out


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
    await safety.recheck(
        ctx.session, ctx.patient_id, trigger="record_existing_medication", actor=ctx.actor_id
    )
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
    acknowledged: bool = False

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        return _check_tz(v)


class PatternIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["daily", "every_n_days", "weekdays"] = "daily"
    every: int = Field(default=1, ge=1, le=30)
    weekdays: list[int] = Field(default_factory=list, max_length=7)

    def pattern(self) -> DayPattern:
        if self.kind == "every_n_days":
            return DAILY if self.every == 1 else DayPattern("every_n_days", self.every)
        if self.kind == "weekdays":
            if any(d < 1 or d > 7 for d in self.weekdays):
                raise ValidationFailedError("Weekdays are 1 (Monday) to 7 (Sunday).")
            return DayPattern("weekdays", 1, frozenset(self.weekdays))
        return DAILY


class ScheduleIn(BaseModel):
    """A complete regimen: how often, when, how much, and for how long."""

    model_config = ConfigDict(extra="forbid")

    schedule_type: ScheduleType = ScheduleType.FIXED_TIMES
    times_of_day: list[time] = Field(default_factory=list, max_length=8)
    interval_hours: float | None = Field(default=None, ge=1, le=48)
    pattern: PatternIn = Field(default_factory=PatternIn)
    dose_amount: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    dose_unit: str | None = Field(default=None, max_length=32)
    meal_relation: MealRelation | None = None
    start_date: date | None = None
    end_date: date | None = None
    clear_end_date: bool = False
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        return _check_tz(v)

    def spec(self, tz: str) -> regimen.ScheduleSpec:
        return regimen.ScheduleSpec(
            schedule_type=self.schedule_type,
            times=tuple(self.times_of_day),
            interval_minutes=round(self.interval_hours * 60) if self.interval_hours else None,
            pattern=self.pattern.pattern(),
            dose_amount=self.dose_amount,
            dose_unit=(self.dose_unit or "").strip() or None,
            meal_relation=self.meal_relation,
            start_date=self.start_date,
            end_date=self.end_date,
            timezone=self.timezone or tz,
            clear_end_date=self.clear_end_date,
        )


class AdviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: AdvisorRole
    name: str = Field(min_length=2, max_length=120)

    def advice(self) -> regimen.Advice:
        return regimen.Advice(role=self.role, name=self.name)


class ConfirmationIn(BaseModel):
    """How a change was confirmed: warnings acknowledged, and (for clinically relevant
    changes) which doctor or pharmacist advised it."""

    model_config = ConfigDict(extra="forbid")
    acknowledged: bool = False
    advice: AdviceIn | None = None
    reason: str | None = Field(default=None, max_length=300)


class ScheduleChangeIn(ScheduleIn):
    acknowledged: bool = False
    advice: AdviceIn | None = None
    reason: str | None = Field(default=None, max_length=300)


class SelfReportedIn(ScheduleIn):
    name: str = Field(min_length=1, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    instructions: str | None = Field(default=None, max_length=1000)
    is_prn: bool = False


class StopIn(ConfirmationIn):
    own_decision: bool = False


class PauseIn(ConfirmationIn):
    own_decision: bool = False
    resume_on: date | None = None


class FindingOut(BaseModel):
    code: str
    message: str
    clinical: bool


class CheckOut(BaseModel):
    requires: Literal["none", "acknowledgement", "clinician"]
    findings: list[FindingOut]


class ChangeRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ChangeRequestKind
    schedule: ScheduleIn | None = None
    message: str | None = Field(default=None, max_length=500)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class EventOut(BaseModel):
    id: uuid.UUID
    event_type: MedicationEventType
    occurred_at: datetime
    actor_role: ActorRole
    actor_name: str | None
    reason: str | None
    advised_by_role: AdvisorRole | None
    advised_by_name: str | None
    details: dict[str, Any] | None


def _check_out(a: change_policy.Assessment) -> CheckOut:
    return CheckOut(
        requires=a.requires,
        findings=[
            FindingOut(code=f.code, message=f.message, clinical=f.clinical) for f in a.findings
        ],
    )


def _role(ctx: PatientRequest) -> ActorRole:
    if "self" in ctx.access.via:
        return ActorRole.PATIENT
    if "caregiver" in ctx.access.via:
        return ActorRole.CAREGIVER
    return ActorRole.DOCTOR


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
    patient-reported. The same medicine cannot be added twice while in use."""
    spec = body.spec(await _patient_tz(ctx, body.timezone))
    if body.is_prn:
        spec = regimen.ScheduleSpec(**{**spec.__dict__, "schedule_type": ScheduleType.AS_NEEDED})
    applied = await regimen.add_self_reported(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        role=_role(ctx),
        name=body.name,
        strength=body.strength,
        dosage_form=body.dosage_form,
        instructions=body.instructions,
        spec=spec,
    )
    await ctx.audit(
        "medication.self_reported", resource_type="medication", resource_id=applied.medication.id
    )
    await safety.recheck(
        ctx.session, ctx.patient_id, trigger="add_self_reported", actor=ctx.actor_id
    )
    await ctx.session.commit()
    return await _one(ctx, applied.medication.id)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/confirm", response_model=MedicationOut
)
async def confirm_medication(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ReminderTimesIn,
    ctx: PatientRequest = _reminders,
) -> MedicationOut:
    """Start a prescribed medicine by choosing reminder times. The dose and food relation
    come from the prescription, which is not changed."""
    applied = await regimen.confirm_prescribed(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        role=_role(ctx),
        times=body.times_of_day,
        timezone=await _patient_tz(ctx, body.timezone),
        meal_relation=body.meal_relation,
    )
    await ctx.audit(
        "medication.confirm",
        resource_type="medication",
        resource_id=applied.medication.id,
        changed_fields=["status"],
    )
    await safety.recheck(
        ctx.session, ctx.patient_id, trigger="confirm_medication", actor=ctx.actor_id
    )
    await ctx.session.commit()
    return await _one(ctx, applied.medication.id)


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
    """Reminder times only. A different number of doses a day for a prescribed medicine
    needs a clinician (use /schedule); warnings must be acknowledged."""
    await regimen.change_times(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        role=_role(ctx),
        times=body.times_of_day,
        timezone=await _patient_tz(ctx, body.timezone),
        meal_relation=body.meal_relation,
        acknowledged=body.acknowledged,
    )
    await ctx.audit(
        "medication.reminder_times", resource_type="medication", resource_id=medication_id
    )
    await ctx.session.commit()
    return await _one(ctx, medication_id)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/schedule/check", response_model=CheckOut
)
async def check_schedule(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ScheduleIn,
    ctx: PatientRequest = _reminders,
) -> CheckOut:
    """What confirming this schedule change would need (nothing is changed)."""
    med = next(
        (
            m
            for m in await service.list_for_patient(ctx.session, ctx.patient_id)
            if m.id == medication_id
        ),
        None,
    )
    if med is None:
        raise NotFoundError()
    spec = regimen.validate_spec(body.spec(await _patient_tz(ctx, body.timezone)))
    return _check_out(await regimen.check(ctx.session, med, spec))


@router.put(
    "/patients/{patient_id}/medications/{medication_id}/schedule", response_model=MedicationOut
)
async def change_schedule(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ScheduleChangeIn,
    ctx: PatientRequest = _reminders,
) -> MedicationOut:
    """Change dose, times, frequency, days, food relation or dates. Clinically relevant
    changes to a prescribed medicine need `advice` (the doctor or pharmacist who advised
    it) or a doctor's approval through a change request."""
    applied = await regimen.change_schedule(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        spec=body.spec(await _patient_tz(ctx, body.timezone)),
        actor=ctx.actor_id,
        role=_role(ctx),
        acknowledged=body.acknowledged,
        advice=body.advice.advice() if body.advice else None,
        reason=body.reason,
    )
    await ctx.audit(
        "medication.schedule_changed",
        resource_type="medication",
        resource_id=medication_id,
        changed_fields=["schedule"],
        context={
            "requires": applied.assessment.requires,
            "findings": ",".join(f.code for f in applied.assessment.findings),
            "advised": "yes" if body.advice else "no",
        },
    )
    await ctx.session.commit()
    return await _one(ctx, medication_id)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/pause", response_model=MedicationOut
)
async def pause_medication(
    patient_id: uuid.UUID, medication_id: uuid.UUID, body: PauseIn, ctx: PatientRequest = _reminders
) -> MedicationOut:
    await regimen.pause(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        role=_role(ctx),
        reason=body.reason,
        resume_on=body.resume_on,
        acknowledged=body.acknowledged,
        advice=body.advice.advice() if body.advice else None,
        own_decision=body.own_decision,
    )
    await ctx.audit(
        "medication.paused", resource_type="medication", resource_id=medication_id,
        changed_fields=["status"],
    )  # fmt: skip
    await ctx.session.commit()
    return await _one(ctx, medication_id)


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/resume", response_model=MedicationOut
)
async def resume_medication(
    patient_id: uuid.UUID, medication_id: uuid.UUID, ctx: PatientRequest = _reminders
) -> MedicationOut:
    await regimen.resume(
        ctx.session, patient_id=ctx.patient_id, medication_id=medication_id,
        actor=ctx.actor_id, role=_role(ctx),
    )  # fmt: skip
    await ctx.audit(
        "medication.resumed", resource_type="medication", resource_id=medication_id,
        changed_fields=["status"],
    )  # fmt: skip
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
    """Discontinue. For a prescribed medicine: acknowledge the warning and record who
    advised it (doctor or pharmacist) or that it is your own decision. The prescription
    is unchanged."""
    await regimen.stop(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        actor=ctx.actor_id,
        role=_role(ctx),
        reason=body.reason,
        acknowledged=body.acknowledged,
        advice=body.advice.advice() if body.advice else None,
        own_decision=body.own_decision,
    )
    await ctx.audit(
        "medication.stopped",
        resource_type="medication",
        resource_id=medication_id,
        changed_fields=["status"],
        context={"own_decision": str(body.own_decision), "advised": "yes" if body.advice else "no"},
    )
    await safety.recheck(ctx.session, ctx.patient_id, trigger="stop_medication", actor=ctx.actor_id)
    await ctx.session.commit()
    return await _one(ctx, medication_id)


@router.get(
    "/patients/{patient_id}/medications/{medication_id}/history", response_model=list[EventOut]
)
async def medication_history(
    patient_id: uuid.UUID, medication_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[EventOut]:
    events = await regimen.history(ctx.session, ctx.patient_id, medication_id)
    names = await identity.display_names(
        ctx.session, {e.actor_user_id for e in events if e.actor_user_id}
    )
    await ctx.audit("medication.history", resource_type="medication", resource_id=medication_id)
    await ctx.session.commit()
    return [
        EventOut(
            id=e.id,
            event_type=e.event_type,
            occurred_at=e.occurred_at,
            actor_role=e.actor_role,
            actor_name=names.get(e.actor_user_id) if e.actor_user_id else None,
            reason=e.reason,
            advised_by_role=e.advised_by_role,
            advised_by_name=e.advised_by_name,
            details=json.loads(e.details) if e.details else None,
        )
        for e in events
    ]


# --- change requests to the doctor ----------------------------------------------------------------


async def _names(ctx: PatientRequest) -> dict[uuid.UUID, str]:
    return {m.id: m.name for m in await service.list_for_patient(ctx.session, ctx.patient_id)}


@router.post(
    "/patients/{patient_id}/medications/{medication_id}/change-requests",
    response_model=ChangeRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_change(
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    body: ChangeRequestIn,
    ctx: PatientRequest = _reminders,
) -> ChangeRequestOut:
    """Ask a linked doctor to confirm a change to a prescribed medicine. Nothing changes
    until a doctor approves."""
    tz = await _patient_tz(ctx, body.schedule.timezone if body.schedule else None)
    req = await regimen.request_change(
        ctx.session,
        patient_id=ctx.patient_id,
        medication_id=medication_id,
        kind=body.kind,
        spec=body.schedule.spec(tz) if body.schedule else None,
        message=body.message,
        actor=ctx.actor_id,
        role=_role(ctx),
    )
    await ctx.audit(
        "medication.change_requested",
        resource_type="medication_change_request",
        resource_id=req.id,
        context={"kind": body.kind.value},
    )
    await ctx.session.commit()
    return request_out(req, (await _names(ctx)).get(req.medication_id, ""))


@router.get(
    "/patients/{patient_id}/medication-change-requests", response_model=list[ChangeRequestOut]
)
async def list_change_requests(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[ChangeRequestOut]:
    rows = await regimen.list_requests(ctx.session, ctx.patient_id)
    names = await _names(ctx)
    await ctx.audit("medication.change_requests", resource_type="medication_change_request")
    await ctx.session.commit()
    return [request_out(r, names.get(r.medication_id, "")) for r in rows]


@router.post(
    "/patients/{patient_id}/medication-change-requests/{request_id}/{decision}",
    response_model=ChangeRequestOut,
)
async def resolve_change_request(
    patient_id: uuid.UUID,
    request_id: uuid.UUID,
    decision: Literal["approve", "decline"],
    body: ResolveIn,
    ctx: PatientRequest = _write,
) -> ChangeRequestOut:
    """A linked doctor approves (the proposal is applied exactly as asked) or declines.
    To change the prescription itself, correct it as a new version as well."""
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    req = await regimen.resolve_request(
        ctx.session,
        patient_id=ctx.patient_id,
        request_id=request_id,
        approve=decision == "approve",
        note=body.note,
        doctor=ctx.actor_id,
    )
    await ctx.audit(
        f"medication.change_{'approved' if decision == 'approve' else 'declined'}",
        resource_type="medication_change_request",
        resource_id=req.id,
    )
    await safety.recheck(
        ctx.session, ctx.patient_id, trigger="resolve_change_request", actor=ctx.actor_id
    )
    await ctx.session.commit()
    return request_out(req, (await _names(ctx)).get(req.medication_id, ""))


@router.post(
    "/patients/{patient_id}/medication-change-requests/{request_id}/withdraw",
    response_model=ChangeRequestOut,
)
async def withdraw_change_request(
    patient_id: uuid.UUID, request_id: uuid.UUID, ctx: PatientRequest = _reminders
) -> ChangeRequestOut:
    req = await regimen.withdraw_request(
        ctx.session, patient_id=ctx.patient_id, request_id=request_id, actor=ctx.actor_id,
        role=_role(ctx),
    )  # fmt: skip
    await ctx.audit(
        "medication.change_withdrawn", resource_type="medication_change_request", resource_id=req.id
    )
    await ctx.session.commit()
    return request_out(req, (await _names(ctx)).get(req.medication_id, ""))


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
    await regimen.complete_finished(
        ctx.session, ctx.patient_id, now.astimezone(ZoneInfo(tz)).date()
    )
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
