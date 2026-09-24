import uuid
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.core.errors import ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.medications import doses
from app.modules.medications.models import DoseStatus, Medication, MedicationDose, MedicationStatus
from app.modules.reminders import engine, service

router = APIRouter(tags=["reminders"])

_manage = patient_request(Permission.MANAGE_REMINDERS)


class PreferencesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminders_enabled: bool
    channel_push: bool
    channel_sms: bool
    channel_email: bool
    show_medicine_names: bool
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    default_snooze_minutes: Literal[5, 10, 15, 30, 60]
    missed_after_minutes: int
    notify_caregivers_on_missed: bool
    remind_again_after_minutes: Literal[5, 10, 15, 30, 60] | None = 15


@router.get("/patients/{patient_id}/reminder-preferences", response_model=PreferencesModel)
async def get_preferences(patient_id: uuid.UUID, ctx: PatientRequest = _manage) -> PreferencesModel:
    prefs = await service.get(ctx.session, ctx.patient_id)
    return PreferencesModel(**asdict(prefs))


@router.put("/patients/{patient_id}/reminder-preferences", response_model=PreferencesModel)
async def save_preferences(
    patient_id: uuid.UUID, body: PreferencesModel, ctx: PatientRequest = _manage
) -> PreferencesModel:
    if not 30 <= body.missed_after_minutes <= 720:
        raise ValidationFailedError("Missed-dose time must be between 30 minutes and 12 hours.")
    prefs = await service.save(
        ctx.session, ctx.patient_id, service.Preferences(**body.model_dump()), ctx.actor_id
    )
    await ctx.audit(
        "reminder_preferences.update",
        resource_type="reminder_preferences",
        changed_fields=sorted(body.model_dump()),
    )
    await ctx.session.commit()
    return PreferencesModel(**asdict(prefs))


# --- due reminders: drive the in-app "Time for your medication" prompt ----------------------


class GuidanceOut(BaseModel):
    message: str
    instructions_as_written: str | None
    instructions_verified: bool
    source_label: str


class ReminderOut(BaseModel):
    dose_id: uuid.UUID
    medication_id: uuid.UUID
    medicine: str
    dose: str | None
    meal: str | None
    instructions: str | None
    instructions_verified: bool
    source_label: str
    origin: str
    scheduled_at: datetime
    status: str
    snooze_count: int
    can_snooze: bool
    default_snooze_minutes: int
    guidance: GuidanceOut | None = None


_view = patient_request(Permission.VIEW_MEDICATIONS)


async def _refresh(ctx: PatientRequest, now: datetime) -> None:
    prefs = await service.get(ctx.session, ctx.patient_id)
    await doses.materialize(ctx.session, ctx.patient_id, now=now)
    await doses.mark_missed(ctx.session, ctx.patient_id, now=now, after=prefs.missed_after)


def _out(
    c: engine.Context, d: MedicationDose, prefs: service.Preferences, guidance: bool
) -> ReminderOut:
    content = c.content(d)
    med = c.meds[d.medication_id]
    return ReminderOut(
        dose_id=d.id,
        medication_id=d.medication_id,
        medicine=content.medicine,
        dose=content.dose,
        meal=content.meal,
        instructions=content.instructions,
        instructions_verified=content.instructions_verified,
        source_label=content.source_label,
        origin=med.origin.value,
        scheduled_at=d.scheduled_at or d.created_at,
        status=d.status.value,
        snooze_count=d.snooze_count,
        can_snooze=d.status != DoseStatus.MISSED and d.snooze_count < doses.MAX_SNOOZES,
        default_snooze_minutes=prefs.default_snooze_minutes,
        guidance=GuidanceOut.model_validate(engine.missed_guidance(content)) if guidance else None,
    )


@router.get("/patients/{patient_id}/reminders/due", response_model=list[ReminderOut])
async def due_reminders(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[ReminderOut]:
    """Doses to take now: due, reminded, or back from snooze. Works without a worker (the
    app polls this), and alongside Web Push when a worker sends reminders."""
    now = datetime.now(UTC)
    await _refresh(ctx, now)
    rows = list(
        (
            await ctx.session.scalars(
                select(MedicationDose)
                .join(Medication, Medication.id == MedicationDose.medication_id)
                .where(
                    MedicationDose.patient_id == ctx.patient_id,
                    Medication.status == MedicationStatus.ACTIVE,
                    or_(
                        (MedicationDose.status.in_([DoseStatus.SCHEDULED, DoseStatus.NOTIFIED]))
                        & (MedicationDose.scheduled_at <= now),
                        (MedicationDose.status == DoseStatus.SNOOZED)
                        & (MedicationDose.snoozed_until <= now),
                    ),
                )
                .order_by(MedicationDose.scheduled_at)
                .limit(20)
            )
        ).all()
    )
    prefs = await service.get(ctx.session, ctx.patient_id)
    c = await engine.load_context(ctx.session, rows)
    await ctx.audit("reminder.due", resource_type="medication_dose")
    await ctx.session.commit()
    return [_out(c, d, prefs, guidance=False) for d in rows]


@router.get("/patients/{patient_id}/reminders/missed", response_model=list[ReminderOut])
async def recent_missed(
    patient_id: uuid.UUID, hours: int = 24, ctx: PatientRequest = _view
) -> list[ReminderOut]:
    """Recently missed doses with the medicine's instructions as written and a pointer to
    the doctor or pharmacist. Never advice on what to do."""
    if not 1 <= hours <= 72:
        raise ValidationFailedError("hours must be between 1 and 72")
    now = datetime.now(UTC)
    await _refresh(ctx, now)
    rows = list(
        (
            await ctx.session.scalars(
                select(MedicationDose)
                .where(
                    MedicationDose.patient_id == ctx.patient_id,
                    MedicationDose.status == DoseStatus.MISSED,
                    MedicationDose.scheduled_at > now - timedelta(hours=hours),
                )
                .order_by(MedicationDose.scheduled_at.desc())
            )
        ).all()
    )
    prefs = await service.get(ctx.session, ctx.patient_id)
    c = await engine.load_context(ctx.session, rows)
    await ctx.audit("reminder.missed", resource_type="medication_dose")
    await ctx.session.commit()
    return [_out(c, d, prefs, guidance=True) for d in rows if d.medication_id in c.meds]
