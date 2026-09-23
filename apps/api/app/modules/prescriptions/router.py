import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MealRelation, VerificationStatus
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.medications import service as medications
from app.modules.prescriptions import service
from app.modules.prescriptions.models import PrescriptionSource, PrescriptionStatus

router = APIRouter(tags=["prescriptions"])

_view = patient_request(Permission.VIEW_PRESCRIPTIONS)
_write = patient_request(Permission.CHANGE_DOCTOR_PRESCRIPTION)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ItemIn(_In):
    drug_name: str = Field(min_length=1, max_length=200)
    generic_name: str | None = Field(default=None, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    route: str | None = Field(default=None, max_length=64)
    dose_amount: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    dose_unit: str | None = Field(default=None, max_length=32)
    frequency_text: str | None = Field(default=None, max_length=64)
    times_per_day: int | None = Field(default=None, ge=1, le=24)
    meal_relation: MealRelation | None = None
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    is_prn: bool = False
    prn_reason: str | None = Field(default=None, max_length=200)
    instructions: str | None = Field(default=None, max_length=1000)


class PrescriptionIn(_In):
    items: list[ItemIn] = Field(min_length=1, max_length=30)
    visit_id: uuid.UUID | None = None
    prescribed_on: date | None = None
    valid_until: date | None = None
    diagnosis_as_written: str | None = Field(default=None, max_length=1000)
    advice: str | None = Field(default=None, max_length=2000)


class PrescriptionUpdate(_In):
    items: list[ItemIn] = Field(min_length=1, max_length=30)
    valid_until: date | None = None
    diagnosis_as_written: str | None = Field(default=None, max_length=1000)
    advice: str | None = Field(default=None, max_length=2000)


class CancelIn(_In):
    reason: str = Field(min_length=3, max_length=300)


class ItemOut(ItemIn):
    model_config = ConfigDict(extra="ignore")
    id: uuid.UUID
    sequence: int


class PrescriptionOut(BaseModel):
    id: uuid.UUID
    source: PrescriptionSource
    status: PrescriptionStatus
    verification_status: VerificationStatus
    prescriber_doctor_id: uuid.UUID | None
    prescriber_name: str | None
    prescribed_by_me: bool
    external_prescriber_name: str | None
    visit_id: uuid.UUID | None
    prescribed_on: date | None
    valid_until: date | None
    issued_at: datetime | None
    diagnosis_as_written: str | None
    advice: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    items: list[ItemOut]


def _to_input(item: ItemIn) -> service.ItemInput:
    return service.ItemInput(**item.model_dump())


async def _out(
    ctx: PatientRequest, rows: list[service.PrescriptionWithItems]
) -> list[PrescriptionOut]:
    doctors = {
        r.prescription.prescriber_doctor_id for r in rows if r.prescription.prescriber_doctor_id
    }
    names = await care_team.doctor_names(ctx.session, doctors)
    me = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    out = []
    for r in rows:
        p = r.prescription
        out.append(
            PrescriptionOut(
                id=p.id,
                source=p.source,
                status=p.status,
                verification_status=p.verification_status,
                prescriber_doctor_id=p.prescriber_doctor_id,
                prescriber_name=names.get(p.prescriber_doctor_id)
                if p.prescriber_doctor_id
                else None,
                prescribed_by_me=p.prescriber_doctor_id is not None
                and p.prescriber_doctor_id == me,
                external_prescriber_name=p.external_prescriber_name,
                visit_id=p.visit_id,
                prescribed_on=p.prescribed_on,
                valid_until=p.valid_until,
                issued_at=p.issued_at,
                diagnosis_as_written=p.diagnosis_as_written,
                advice=p.advice,
                cancelled_at=p.cancelled_at,
                cancel_reason=p.cancel_reason,
                items=[ItemOut.model_validate(i, from_attributes=True) for i in r.items],
            )
        )
    return out


@router.get("/patients/{patient_id}/prescriptions", response_model=list[PrescriptionOut])
async def list_prescriptions(
    patient_id: uuid.UUID, ctx: PatientRequest = _view
) -> list[PrescriptionOut]:
    rows = await service.list_for_patient(
        ctx.session,
        ctx.patient_id,
        viewer_doctor_id=await care_team.doctor_id_for(ctx.session, ctx.actor_id),
    )
    await ctx.audit("prescription.list", resource_type="prescription")
    await ctx.session.commit()
    return await _out(ctx, rows)


@router.post(
    "/patients/{patient_id}/prescriptions",
    response_model=PrescriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_prescription(
    patient_id: uuid.UUID, body: PrescriptionIn, ctx: PatientRequest = _write
) -> PrescriptionOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.create_draft(
        ctx.session,
        patient_id=ctx.patient_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        items=[_to_input(i) for i in body.items],
        visit_id=body.visit_id,
        prescribed_on=body.prescribed_on,
        valid_until=body.valid_until,
        diagnosis_as_written=body.diagnosis_as_written,
        advice=body.advice,
    )
    await ctx.audit(
        "prescription.create_draft", resource_type="prescription", resource_id=row.prescription.id
    )
    await ctx.session.commit()
    return (await _out(ctx, [row]))[0]


@router.put(
    "/patients/{patient_id}/prescriptions/{prescription_id}", response_model=PrescriptionOut
)
async def update_prescription(
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    body: PrescriptionUpdate,
    ctx: PatientRequest = _write,
) -> PrescriptionOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.replace_draft(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        items=[_to_input(i) for i in body.items],
        valid_until=body.valid_until,
        diagnosis_as_written=body.diagnosis_as_written,
        advice=body.advice,
    )
    await ctx.audit(
        "prescription.update_draft",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=["items", "valid_until", "diagnosis_as_written", "advice"],
    )
    await ctx.session.commit()
    return (await _out(ctx, [row]))[0]


@router.post(
    "/patients/{patient_id}/prescriptions/{prescription_id}/issue", response_model=PrescriptionOut
)
async def issue_prescription(
    patient_id: uuid.UUID, prescription_id: uuid.UUID, ctx: PatientRequest = _write
) -> PrescriptionOut:
    """Issue (freeze) a draft. Its medicines appear on the patient's list as awaiting the
    patient's confirmation; no reminder starts until they confirm the schedule."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.issue(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
    )
    await medications.create_from_prescription(
        ctx.session,
        patient_id=ctx.patient_id,
        start_date=row.prescription.prescribed_on or date.today(),
        actor=ctx.actor_id,
        lines=[
            medications.PrescribedLine(
                item_id=i.id,
                drug_name=i.drug_name,
                generic_name=i.generic_name,
                strength=i.strength,
                dosage_form=i.dosage_form,
                route=i.route,
                is_prn=i.is_prn,
                instructions=i.instructions,
                duration_days=i.duration_days,
            )
            for i in row.items
        ],
    )
    await ctx.audit(
        "prescription.issue",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=["status", "issued_at"],
        context={"items": str(len(row.items))},
    )
    await ctx.session.commit()
    return (await _out(ctx, [row]))[0]


@router.post(
    "/patients/{patient_id}/prescriptions/{prescription_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_prescription(
    patient_id: uuid.UUID, prescription_id: uuid.UUID, body: CancelIn, ctx: PatientRequest = _write
) -> Response:
    """Cancel an issued prescription (kept as history), or discard an unissued draft."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    await service.cancel(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        reason=body.reason,
    )
    await ctx.audit(
        "prescription.cancel",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=["status", "cancelled_at", "cancel_reason"],
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
