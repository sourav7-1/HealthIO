import uuid
from datetime import date, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.medications import service
from app.modules.medications.models import Medication, MedicationSource, MedicationStatus

router = APIRouter(tags=["medications"])

_view = patient_request(Permission.VIEW_MEDICATIONS)
_write = patient_request(Permission.CHANGE_DOCTOR_PRESCRIPTION)
_adherence = patient_request(Permission.VIEW_ADHERENCE)


class ExistingMedicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    route: str | None = Field(default=None, max_length=64)
    instructions: str | None = Field(default=None, max_length=1000)
    start_date: date | None = None
    is_prn: bool = False


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


def _out(m: Medication) -> MedicationOut:
    return MedicationOut.model_validate(m, from_attributes=True)


@router.get("/patients/{patient_id}/medications", response_model=list[MedicationOut])
async def list_medications(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[MedicationOut]:
    meds = await service.list_for_patient(ctx.session, ctx.patient_id)
    await ctx.audit("medication.list", resource_type="medication")
    await ctx.session.commit()
    return [_out(m) for m in meds]


@router.post(
    "/patients/{patient_id}/medications",
    response_model=MedicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_existing_medication(
    patient_id: uuid.UUID, body: ExistingMedicationIn, ctx: PatientRequest = _write
) -> MedicationOut:
    """Record a medicine the patient already takes (e.g. started elsewhere), as reported
    to the doctor. New treatment is written as a prescription instead."""
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
