import uuid
from dataclasses import asdict
from datetime import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core.errors import ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.reminders import service

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
