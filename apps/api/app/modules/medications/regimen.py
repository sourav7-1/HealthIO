"""Regimen management: schedule changes, pause/resume, discontinue, course completion,
history, change requests to a doctor, and duplicate detection.

Every change here is made by a named person (patient, caregiver or doctor) and is
recorded in `medication_events`. The database refuses a regimen change without a human
actor, so no automated process (including AI) can change what a patient takes. The
prescription itself is never modified; only the patient's regimen is.

Clinically relevant changes to a prescribed medicine (change_policy.assess) need either
a doctor's approval in the app (change request) or the patient's record of which doctor
or pharmacist advised it. Timing-only changes need the warnings to be acknowledged.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MealRelation, RecordSource
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.modules.medications import change_policy as policy
from app.modules.medications.doses import _end_schedule, _medication, active_schedules
from app.modules.medications.models import (
    ActorRole,
    AdvisorRole,
    ChangeRequestKind,
    ChangeRequestStatus,
    Medication,
    MedicationChangeRequest,
    MedicationEvent,
    MedicationEventType,
    MedicationOrigin,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleStatus,
    ScheduleType,
)
from app.modules.medications.schedules import (
    DAILY,
    DayPattern,
    InvalidPattern,
    format_rule,
    parse_rule,
)
from app.modules.prescriptions.models import PrescriptionItem

MAX_TIMES_PER_DAY = 8
PRESCRIBED_ORIGINS = frozenset(
    {
        MedicationOrigin.DOCTOR_PRESCRIPTION,
        MedicationOrigin.UPLOADED_AI,
        MedicationOrigin.UPLOADED_TYPED,
        MedicationOrigin.CLINICIAN_RECORDED,
    }
)


class ConfirmationRequiredError(ConflictError):
    """A change needs acknowledgement or clinician confirmation first (409)."""

    def __init__(self, assessment: policy.Assessment) -> None:
        needs = assessment.requires
        detail = (
            "This change needs a doctor's or pharmacist's confirmation: "
            if needs == "clinician"
            else "Please confirm you have read these warnings: "
        ) + " ".join(f.message for f in assessment.findings)
        super().__init__(detail)
        self.assessment = assessment


@dataclass(frozen=True)
class Advice:
    """A clinician outside the platform who advised the change (as the patient reports)."""

    role: AdvisorRole
    name: str


@dataclass(frozen=True)
class ScheduleSpec:
    schedule_type: ScheduleType
    times: tuple[time, ...] = ()
    interval_minutes: int | None = None
    pattern: DayPattern = DAILY
    dose_amount: Decimal | None = None
    dose_unit: str | None = None
    meal_relation: MealRelation | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = "Asia/Kolkata"
    # Dates left out keep the medicine's current dates; this removes the end date.
    clear_end_date: bool = False

    def with_dates_of(self, med: Medication) -> "ScheduleSpec":
        return ScheduleSpec(
            **{
                **self.__dict__,
                "start_date": self.start_date or med.start_date,
                "end_date": None if self.clear_end_date else (self.end_date or med.end_date),
                "clear_end_date": False,
            }
        )

    def regimen(self) -> policy.Regimen:
        return policy.Regimen(
            schedule_type=self.schedule_type.value,
            times=self.times,
            interval_minutes=self.interval_minutes,
            pattern=self.pattern,
            dose_amount=self.dose_amount,
            dose_unit=self.dose_unit,
            meal_relation=self.meal_relation.value if self.meal_relation else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schedule_type": self.schedule_type.value,
            "times": [t.strftime("%H:%M") for t in self.times],
            "interval_minutes": self.interval_minutes,
            "rule": format_rule(self.pattern),
            "dose_amount": str(self.dose_amount) if self.dose_amount is not None else None,
            "dose_unit": self.dose_unit,
            "meal_relation": self.meal_relation.value if self.meal_relation else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScheduleSpec":
        return cls(
            schedule_type=ScheduleType(d["schedule_type"]),
            times=tuple(time.fromisoformat(t) for t in d.get("times", [])),
            interval_minutes=d.get("interval_minutes"),
            pattern=parse_rule(d.get("rule")),
            dose_amount=Decimal(d["dose_amount"]) if d.get("dose_amount") else None,
            dose_unit=d.get("dose_unit"),
            meal_relation=MealRelation(d["meal_relation"]) if d.get("meal_relation") else None,
            start_date=date.fromisoformat(d["start_date"]) if d.get("start_date") else None,
            end_date=date.fromisoformat(d["end_date"]) if d.get("end_date") else None,
            timezone=d.get("timezone") or "Asia/Kolkata",
        )


def validate_spec(spec: ScheduleSpec) -> ScheduleSpec:
    if spec.schedule_type == ScheduleType.FIXED_TIMES:
        times = tuple(sorted({t.replace(second=0, microsecond=0) for t in spec.times}))
        if not times:
            raise ValidationFailedError("Choose at least one time for reminders.")
        if len(times) > MAX_TIMES_PER_DAY:
            raise ValidationFailedError(f"At most {MAX_TIMES_PER_DAY} times a day.")
        if spec.pattern.kind == "weekdays" and not spec.pattern.weekdays:
            raise ValidationFailedError("Choose at least one day of the week.")
        spec = ScheduleSpec(**{**spec.__dict__, "times": times, "interval_minutes": None})
    elif spec.schedule_type == ScheduleType.INTERVAL:
        if spec.interval_minutes is None or not 60 <= spec.interval_minutes <= 48 * 60:
            raise ValidationFailedError("Choose an interval between 1 and 48 hours.")
        if len(spec.times) != 1:
            raise ValidationFailedError("Choose the time of the first dose.")
        if spec.pattern != DAILY:
            raise ValidationFailedError("Day patterns apply only to fixed times.")
    else:  # as needed
        spec = ScheduleSpec(
            **{**spec.__dict__, "times": (), "interval_minutes": None, "pattern": DAILY}
        )
    if spec.dose_amount is not None and spec.dose_amount <= 0:
        raise ValidationFailedError("The dose must be more than zero.")
    if spec.start_date and spec.end_date and spec.end_date < spec.start_date:
        raise ValidationFailedError("The end date cannot be before the start date.")
    return spec


def regimen_of(schedule: MedicationSchedule | None) -> policy.Regimen | None:
    if schedule is None:
        return None
    try:
        pattern = parse_rule(schedule.recurrence_rule)
    except InvalidPattern:
        pattern = DAILY
    return policy.Regimen(
        schedule_type=schedule.schedule_type.value,
        times=tuple(schedule.times_of_day),
        interval_minutes=schedule.interval_minutes,
        pattern=pattern,
        dose_amount=schedule.dose_amount,
        dose_unit=schedule.dose_unit,
        meal_relation=schedule.meal_relation.value if schedule.meal_relation else None,
    )


async def prescribed_for(session: AsyncSession, med: Medication) -> policy.Prescribed | None:
    if med.prescription_item_id is None:
        if med.origin == MedicationOrigin.CLINICIAN_RECORDED:
            return policy.Prescribed(None, None, None, None, med.is_prn, None)
        return None
    item = await session.get(PrescriptionItem, med.prescription_item_id)
    if item is None:
        return None
    return policy.Prescribed(
        times_per_day=item.times_per_day,
        dose_amount=item.dose_amount,
        dose_unit=item.dose_unit,
        meal_relation=item.meal_relation.value if item.meal_relation else None,
        is_prn=item.is_prn,
        frequency_text=item.frequency_text,
    )


def is_prescribed(med: Medication) -> bool:
    return med.origin in PRESCRIBED_ORIGINS


async def check(session: AsyncSession, med: Medication, spec: ScheduleSpec) -> policy.Assessment:
    spec = spec.with_dates_of(med)
    current = (await active_schedules(session, med.patient_id)).get(med.id)
    prescribed = await prescribed_for(session, med) if is_prescribed(med) else None
    result = policy.assess(spec.regimen(), prescribed=prescribed, current=regimen_of(current))
    if prescribed is not None and spec.end_date != med.end_date and med.end_date is not None:
        result.findings.append(
            policy.Finding(
                "duration_differs",
                "This changes how long the prescribed course lasts.",
                clinical=True,
            )
        )
    return result


def _authorised(
    assessment: policy.Assessment,
    *,
    acknowledged: bool,
    advice: Advice | None,
    doctor_confirmed: bool,
) -> None:
    needs = assessment.requires
    if needs == "none" or doctor_confirmed:
        return
    if needs == "acknowledgement" and acknowledged:
        return
    if needs == "clinician" and acknowledged and advice is not None and advice.name.strip():
        return
    raise ConfirmationRequiredError(assessment)


def _event(
    med: Medication,
    kind: MedicationEventType,
    *,
    actor: uuid.UUID | None,
    role: ActorRole,
    details: dict[str, Any] | None = None,
    reason: str | None = None,
    advice: Advice | None = None,
    change_request_id: uuid.UUID | None = None,
) -> MedicationEvent:
    return MedicationEvent(
        patient_id=med.patient_id,
        medication_id=med.id,
        event_type=kind,
        actor_user_id=actor,
        actor_role=role,
        details=json.dumps(details) if details else None,
        reason=reason,
        advised_by_role=advice.role if advice else None,
        advised_by_name=advice.name.strip() if advice else None,
        change_request_id=change_request_id,
        created_by=actor,
        updated_by=actor,
    )


def new_schedule(
    med: Medication,
    spec: ScheduleSpec,
    *,
    actor: uuid.UUID,
    now: datetime,
    supersedes: uuid.UUID | None = None,
) -> MedicationSchedule:
    return MedicationSchedule(
        patient_id=med.patient_id,
        medication_id=med.id,
        schedule_type=spec.schedule_type,
        times_of_day=list(spec.times),
        interval_minutes=spec.interval_minutes,
        recurrence_rule=format_rule(spec.pattern),
        timezone=spec.timezone,
        dose_amount=spec.dose_amount,
        dose_unit=spec.dose_unit,
        meal_relation=spec.meal_relation,
        effective_from=now,
        supersedes_schedule_id=supersedes,
        created_by=actor,
        updated_by=actor,
    )


def spec_of(schedule: MedicationSchedule, med: Medication) -> ScheduleSpec:
    return ScheduleSpec(
        schedule_type=schedule.schedule_type,
        times=tuple(schedule.times_of_day),
        interval_minutes=schedule.interval_minutes,
        pattern=parse_rule(schedule.recurrence_rule),
        dose_amount=schedule.dose_amount,
        dose_unit=schedule.dose_unit,
        meal_relation=schedule.meal_relation,
        start_date=med.start_date,
        end_date=med.end_date,
        timezone=schedule.timezone,
    )


@dataclass
class Applied:
    medication: Medication
    schedule: MedicationSchedule | None
    assessment: policy.Assessment = field(default_factory=policy.Assessment)


async def change_schedule(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    spec: ScheduleSpec,
    actor: uuid.UUID,
    role: ActorRole,
    acknowledged: bool = False,
    advice: Advice | None = None,
    doctor_confirmed: bool = False,
    reason: str | None = None,
    change_request_id: uuid.UUID | None = None,
) -> Applied:
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.ACTIVE:
        raise ConflictError("The schedule can be changed only for a medicine in use.")
    spec = validate_spec(spec.with_dates_of(med))
    if (spec.schedule_type == ScheduleType.AS_NEEDED) != med.is_prn and not is_prescribed(med):
        med.is_prn = spec.schedule_type == ScheduleType.AS_NEEDED  # the patient's own entry
    assessment = await check(session, med, spec)
    _authorised(
        assessment, acknowledged=acknowledged, advice=advice, doctor_confirmed=doctor_confirmed
    )
    now = datetime.now(UTC)
    current = (await active_schedules(session, patient_id)).get(med.id)
    before = spec_of(current, med).as_dict() if current else None
    if current is not None:
        await _end_schedule(session, current, actor, now, "schedule changed")
    schedule = new_schedule(
        med, spec, actor=actor, now=now, supersedes=current.id if current else None
    )
    session.add(schedule)
    med.start_date = spec.start_date if spec.start_date is not None else med.start_date
    med.end_date = spec.end_date
    med.updated_by = actor
    session.add(
        _event(
            med,
            MedicationEventType.SCHEDULE_CHANGED,
            actor=actor,
            role=role,
            details={
                "before": before,
                "after": spec.as_dict(),
                "findings": [f.code for f in assessment.findings],
                "confirmed_by": "doctor" if doctor_confirmed else ("advice" if advice else None),
            },
            reason=reason,
            advice=advice,
            change_request_id=change_request_id,
        )
    )
    await session.flush()
    return Applied(med, schedule, assessment)


def _stop_or_pause_assessment(med: Medication) -> policy.Assessment:
    if is_prescribed(med):
        return policy.Assessment([policy.STOP_OR_PAUSE_PRESCRIBED])
    return policy.Assessment()


def _authorise_stop(
    med: Medication,
    *,
    acknowledged: bool,
    advice: Advice | None,
    own_decision: bool,
    doctor_confirmed: bool,
) -> policy.Assessment:
    """Stopping or pausing a prescribed medicine: the doctor confirms, or the patient
    records who advised it, or records it as their own decision after acknowledging the
    warning. The app never advises it."""
    assessment = _stop_or_pause_assessment(med)
    if assessment.requires == "none" or doctor_confirmed:
        return assessment
    if acknowledged and ((advice is not None and advice.name.strip()) or own_decision):
        return assessment
    raise ConfirmationRequiredError(assessment)


async def pause(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
    reason: str | None,
    resume_on: date | None,
    acknowledged: bool = False,
    advice: Advice | None = None,
    own_decision: bool = False,
    doctor_confirmed: bool = False,
    change_request_id: uuid.UUID | None = None,
) -> Applied:
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.ACTIVE:
        raise ConflictError("Only a medicine in use can be paused.")
    assessment = _authorise_stop(
        med, acknowledged=acknowledged, advice=advice, own_decision=own_decision,
        doctor_confirmed=doctor_confirmed,
    )  # fmt: skip
    now = datetime.now(UTC)
    if resume_on is not None and resume_on <= now.date():
        raise ValidationFailedError("The restart date must be in the future.")
    current = (await active_schedules(session, patient_id)).get(med.id)
    if current is not None:
        await _end_schedule(session, current, actor, now, "medicine paused")
    med.status = MedicationStatus.PAUSED
    med.paused_at = now
    med.paused_by = actor
    med.pause_reason = reason
    med.resume_on = resume_on
    med.updated_by = actor
    session.add(
        _event(
            med,
            MedicationEventType.PAUSED,
            actor=actor,
            role=role,
            details={
                "resume_on": resume_on.isoformat() if resume_on else None,
                "own_decision": own_decision,
                "confirmed_by": "doctor" if doctor_confirmed else None,
            },
            reason=reason,
            advice=advice,
            change_request_id=change_request_id,
        )
    )
    await session.flush()
    return Applied(med, None, assessment)


async def resume(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
) -> Applied:
    """Restart with the schedule that was in use before the pause (a new schedule version)."""
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.PAUSED:
        raise ConflictError("This medicine is not paused.")
    last = await session.scalar(
        select(MedicationSchedule)
        .where(
            MedicationSchedule.medication_id == med.id, MedicationSchedule.patient_id == patient_id
        )
        .order_by(MedicationSchedule.effective_from.desc())
        .limit(1)
    )
    now = datetime.now(UTC)
    med.status = MedicationStatus.ACTIVE
    med.paused_at = None
    med.paused_by = None
    med.pause_reason = None
    med.resume_on = None
    med.updated_by = actor
    schedule = None
    if last is not None:
        schedule = new_schedule(med, spec_of(last, med), actor=actor, now=now, supersedes=last.id)
        session.add(schedule)
    session.add(_event(med, MedicationEventType.RESUMED, actor=actor, role=role))
    await session.flush()
    return Applied(med, schedule)


async def stop(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
    reason: str | None,
    acknowledged: bool = False,
    advice: Advice | None = None,
    own_decision: bool = False,
    doctor_confirmed: bool = False,
    change_request_id: uuid.UUID | None = None,
) -> Applied:
    """Discontinue a medicine before its planned end. The prescription is unchanged."""
    med = await _medication(session, patient_id, medication_id)
    if med.status not in (
        MedicationStatus.ACTIVE,
        MedicationStatus.PAUSED,
        MedicationStatus.PENDING_CONFIRMATION,
    ):
        raise ConflictError("This medicine is not in use.")
    assessment = _authorise_stop(
        med, acknowledged=acknowledged, advice=advice, own_decision=own_decision,
        doctor_confirmed=doctor_confirmed,
    )  # fmt: skip
    now = datetime.now(UTC)
    current = (await active_schedules(session, patient_id)).get(med.id)
    if current is not None:
        await _end_schedule(session, current, actor, now, "medicine stopped")
    if med.status == MedicationStatus.PENDING_CONFIRMATION:
        med.confirmed_at = now  # the constraint expects a decision on record
        med.confirmed_by = actor
    med.status = MedicationStatus.STOPPED
    med.paused_at = None
    med.paused_by = None
    med.stopped_at = now
    med.stopped_by = actor
    med.stop_source = {
        ActorRole.DOCTOR: RecordSource.DOCTOR,
        ActorRole.CAREGIVER: RecordSource.CAREGIVER,
    }.get(role, RecordSource.PATIENT)
    med.stop_reason = reason
    today = now.date()
    med.end_date = max(today, med.start_date) if med.start_date else today
    med.updated_by = actor
    session.add(
        _event(
            med,
            MedicationEventType.STOPPED,
            actor=actor,
            role=role,
            details={
                "own_decision": own_decision,
                "confirmed_by": "doctor" if doctor_confirmed else None,
            },
            reason=reason,
            advice=advice,
            change_request_id=change_request_id,
        )
    )
    await session.flush()
    return Applied(med, None, assessment)


async def complete_finished(session: AsyncSession, patient_id: uuid.UUID, today: date) -> int:
    """Courses whose end date has passed become COMPLETED (the only change a scheduled
    job makes, and it follows the prescribed end date)."""
    meds = list(
        (
            await session.scalars(
                select(Medication)
                .where(
                    Medication.patient_id == patient_id,
                    Medication.status == MedicationStatus.ACTIVE,
                    Medication.end_date.is_not(None),
                    Medication.end_date < today,
                )
                .with_for_update()
            )
        ).all()
    )
    if not meds:
        return 0
    schedules = await active_schedules(session, patient_id)
    now = datetime.now(UTC)
    for med in meds:
        current = schedules.get(med.id)
        if current is not None:
            current.status = ScheduleStatus.ENDED
            current.effective_until = now
            current.ended_reason = "course completed"
        med.status = MedicationStatus.COMPLETED
        session.add(
            _event(
                med,
                MedicationEventType.COMPLETED,
                actor=None,
                role=ActorRole.SYSTEM,
                details={"end_date": med.end_date.isoformat() if med.end_date else None},
            )
        )
    await session.flush()
    return len(meds)


async def history(
    session: AsyncSession, patient_id: uuid.UUID, medication_id: uuid.UUID
) -> list[MedicationEvent]:
    rows = await session.scalars(
        select(MedicationEvent)
        .where(
            MedicationEvent.patient_id == patient_id, MedicationEvent.medication_id == medication_id
        )
        .order_by(MedicationEvent.occurred_at, MedicationEvent.created_at)
    )
    return list(rows.all())


def record_created(
    med: Medication, *, actor: uuid.UUID, role: ActorRole, details: dict[str, Any] | None = None
) -> MedicationEvent:
    return _event(med, MedicationEventType.CREATED, actor=actor, role=role, details=details)


def record_confirmed(
    med: Medication, spec: ScheduleSpec, *, actor: uuid.UUID, role: ActorRole
) -> MedicationEvent:
    return _event(
        med,
        MedicationEventType.CONFIRMED,
        actor=actor,
        role=role,
        details={"after": spec.as_dict()},
    )


# --- duplicates -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Duplicate:
    medication_id: uuid.UUID
    duplicate_id: uuid.UUID
    kind: str  # same_name | same_generic


async def duplicates(session: AsyncSession, patient_id: uuid.UUID) -> list[Duplicate]:
    rows = await session.execute(
        text(
            "SELECT medication_id, duplicate_id, kind FROM medication_possible_duplicates "
            "WHERE patient_id = :p"
        ),
        {"p": patient_id},
    )
    return [Duplicate(r[0], r[1], r[2]) for r in rows]


async def note_duplicates(session: AsyncSession, med: Medication) -> list[Duplicate]:
    """Record in the new medicine's history that it looks like one already in use."""
    found = [d for d in await duplicates(session, med.patient_id) if d.medication_id == med.id]
    if found:
        session.add(
            _event(
                med,
                MedicationEventType.DUPLICATE_NOTED,
                actor=None,
                role=ActorRole.SYSTEM,
                details={
                    "duplicates": [{"id": str(d.duplicate_id), "kind": d.kind} for d in found]
                },
            )
        )
        await session.flush()
    return found


def is_duplicate_violation(exc: IntegrityError) -> bool:
    return "uq_medications_self_reported_open" in str(exc.orig)


# --- change requests --------------------------------------------------------------------------


async def request_change(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    kind: ChangeRequestKind,
    spec: ScheduleSpec | None,
    message: str | None,
    actor: uuid.UUID,
    role: ActorRole,
) -> MedicationChangeRequest:
    med = await _medication(session, patient_id, medication_id)
    if not is_prescribed(med):
        raise ValidationFailedError("You can change a medicine you added yourself directly.")
    if med.status not in (MedicationStatus.ACTIVE, MedicationStatus.PAUSED):
        raise ConflictError("This medicine is not in use.")
    if kind == ChangeRequestKind.SCHEDULE:
        if spec is None:
            raise ValidationFailedError("Describe the new schedule.")
        spec = validate_spec(spec)
    pending = await session.scalar(
        select(MedicationChangeRequest.id).where(
            MedicationChangeRequest.medication_id == med.id,
            MedicationChangeRequest.status == ChangeRequestStatus.PENDING,
        )
    )
    if pending is not None:
        raise ConflictError("There is already a request waiting for the doctor for this medicine.")
    req = MedicationChangeRequest(
        patient_id=patient_id,
        medication_id=med.id,
        kind=kind,
        requested_by=actor,
        requester_role=role,
        message=message,
        proposed=json.dumps(spec.as_dict()) if spec else None,
        created_by=actor,
        updated_by=actor,
    )
    session.add(req)
    await session.flush()
    session.add(
        _event(
            med,
            MedicationEventType.CHANGE_REQUESTED,
            actor=actor,
            role=role,
            details={"kind": kind.value, "proposed": spec.as_dict() if spec else None},
            reason=message,
            change_request_id=req.id,
        )
    )
    await session.flush()
    return req


async def get_request(
    session: AsyncSession, patient_id: uuid.UUID, request_id: uuid.UUID
) -> MedicationChangeRequest:
    req: MedicationChangeRequest | None = await session.scalar(
        select(MedicationChangeRequest)
        .where(
            MedicationChangeRequest.id == request_id,
            MedicationChangeRequest.patient_id == patient_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if req is None:
        raise NotFoundError()
    return req


async def list_requests(
    session: AsyncSession, patient_id: uuid.UUID
) -> list[MedicationChangeRequest]:
    rows = await session.scalars(
        select(MedicationChangeRequest)
        .where(MedicationChangeRequest.patient_id == patient_id)
        .order_by(MedicationChangeRequest.created_at.desc())
    )
    return list(rows.all())


async def resolve_request(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    request_id: uuid.UUID,
    approve: bool,
    note: str | None,
    doctor: uuid.UUID,
) -> MedicationChangeRequest:
    """A doctor approves (the proposal is applied exactly as asked) or declines."""
    req = await get_request(session, patient_id, request_id)
    if req.status != ChangeRequestStatus.PENDING:
        raise ConflictError("This request has already been answered.")
    now = datetime.now(UTC)
    if approve:
        if req.kind == ChangeRequestKind.SCHEDULE and req.proposed:
            await change_schedule(
                session, patient_id=patient_id, medication_id=req.medication_id,
                spec=ScheduleSpec.from_dict(json.loads(req.proposed)), actor=doctor,
                role=ActorRole.DOCTOR, doctor_confirmed=True, reason=note, change_request_id=req.id,
            )  # fmt: skip
        elif req.kind == ChangeRequestKind.STOP:
            await stop(
                session, patient_id=patient_id, medication_id=req.medication_id, actor=doctor,
                role=ActorRole.DOCTOR, reason=note or req.message, doctor_confirmed=True,
                change_request_id=req.id,
            )  # fmt: skip
        elif req.kind == ChangeRequestKind.PAUSE:
            await pause(
                session, patient_id=patient_id, medication_id=req.medication_id, actor=doctor,
                role=ActorRole.DOCTOR, reason=note or req.message, resume_on=None,
                doctor_confirmed=True, change_request_id=req.id,
            )  # fmt: skip
    req.status = ChangeRequestStatus.APPROVED if approve else ChangeRequestStatus.DECLINED
    req.resolved_by = doctor
    req.resolved_at = now
    req.resolution_note = note
    req.updated_by = doctor
    med = await _medication(session, patient_id, req.medication_id)
    session.add(
        _event(
            med,
            MedicationEventType.CHANGE_APPROVED if approve else MedicationEventType.CHANGE_DECLINED,
            actor=doctor,
            role=ActorRole.DOCTOR,
            reason=note,
            change_request_id=req.id,
        )
    )
    await session.flush()
    return req


async def withdraw_request(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    request_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
) -> MedicationChangeRequest:
    req = await get_request(session, patient_id, request_id)
    if req.status != ChangeRequestStatus.PENDING:
        raise ConflictError("This request has already been answered.")
    req.status = ChangeRequestStatus.WITHDRAWN
    req.resolved_at = datetime.now(UTC)
    req.updated_by = actor
    med = await _medication(session, patient_id, req.medication_id)
    session.add(
        _event(
            med,
            MedicationEventType.CHANGE_WITHDRAWN,
            actor=actor,
            role=role,
            change_request_id=req.id,
        )
    )
    await session.flush()
    return req


def origin_for_prescription(prescription_source: str, scan_mode: str | None) -> MedicationOrigin:
    if prescription_source == "doctor_issued":
        return MedicationOrigin.DOCTOR_PRESCRIPTION
    return MedicationOrigin.UPLOADED_AI if scan_mode == "ai" else MedicationOrigin.UPLOADED_TYPED


# --- starting medicines -----------------------------------------------------------------------


async def confirm_prescribed(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
) -> Applied:
    """The patient accepts a prescribed medicine and chooses reminder times. The dose and
    food relation come from the prescription; the prescription is not changed."""
    med = await _medication(session, patient_id, medication_id)
    if med.status != MedicationStatus.PENDING_CONFIRMATION:
        raise ConflictError("This medicine has already been confirmed.")
    item = (
        await session.get(PrescriptionItem, med.prescription_item_id)
        if med.prescription_item_id
        else None
    )
    spec = validate_spec(
        ScheduleSpec(
            schedule_type=ScheduleType.AS_NEEDED if med.is_prn else ScheduleType.FIXED_TIMES,
            times=tuple(times),
            dose_amount=item.dose_amount if item else None,
            dose_unit=item.dose_unit if item else None,
            meal_relation=meal_relation or (item.meal_relation if item else None),
            start_date=med.start_date,
            end_date=med.end_date,
            timezone=timezone,
        )
    )
    now = datetime.now(UTC)
    med.status = MedicationStatus.ACTIVE
    med.confirmed_at = now
    med.confirmed_by = actor
    med.updated_by = actor
    schedule = new_schedule(med, spec, actor=actor, now=now)
    session.add(schedule)
    # Timing warnings are shown to the person; confirming the prescription's own dose and
    # food relation is never a clinically relevant change.
    assessment = await check(session, med, spec)
    session.add(record_confirmed(med, spec, actor=actor, role=role))
    await session.flush()
    return Applied(med, schedule, assessment)


async def add_self_reported(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
    name: str,
    strength: str | None,
    dosage_form: str | None,
    instructions: str | None,
    spec: ScheduleSpec,
) -> Applied:
    if not name.strip():
        raise ValidationFailedError("Enter the medicine's name.")
    spec = validate_spec(spec)
    now = datetime.now(UTC)
    med = Medication(
        patient_id=patient_id,
        source=MedicationSource.SELF_REPORTED,
        origin=MedicationOrigin.SELF_REPORTED,
        name=name.strip(),
        strength=strength,
        dosage_form=dosage_form,
        instructions=instructions,
        is_prn=spec.schedule_type == ScheduleType.AS_NEEDED,
        status=MedicationStatus.ACTIVE,
        start_date=spec.start_date,
        end_date=spec.end_date,
        confirmed_at=now,
        confirmed_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    try:
        async with session.begin_nested():
            session.add(med)
            await session.flush()
    except IntegrityError as exc:
        if is_duplicate_violation(exc):
            raise ConflictError(
                f"{name.strip()} is already on the list. "
                "Change that entry instead of adding it again."
            ) from None
        raise
    schedule = new_schedule(med, spec, actor=actor, now=now)
    session.add(schedule)
    session.add(record_created(med, actor=actor, role=role, details={"after": spec.as_dict()}))
    await session.flush()
    await note_duplicates(session, med)
    return Applied(med, schedule, policy.assess(spec.regimen(), prescribed=None))


async def change_times(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    medication_id: uuid.UUID,
    actor: uuid.UUID,
    role: ActorRole,
    times: list[time],
    timezone: str,
    meal_relation: MealRelation | None,
    acknowledged: bool = False,
) -> Applied:
    """Reminder times only; everything else stays as it is."""
    current = (await active_schedules(session, patient_id)).get(medication_id)
    med = await _medication(session, patient_id, medication_id)
    base = spec_of(current, med) if current else ScheduleSpec(ScheduleType.FIXED_TIMES)
    spec = ScheduleSpec(
        **{
            **base.__dict__,
            "times": tuple(times),
            "timezone": timezone,
            "meal_relation": meal_relation if meal_relation is not None else base.meal_relation,
        }
    )
    return await change_schedule(
        session, patient_id=patient_id, medication_id=medication_id, spec=spec, actor=actor,
        role=role, acknowledged=acknowledged,
    )  # fmt: skip
