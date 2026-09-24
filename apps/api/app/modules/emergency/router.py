import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.emergency import service
from app.modules.emergency.models import EmergencyContact, OrganDonorStatus

router = APIRouter(tags=["emergency"])

_manage = patient_request(Permission.MANAGE_EMERGENCY_INFO)
_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}[0-9]$")


class EmergencyProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    critical_information: str | None = Field(default=None, max_length=1000)
    advance_directive: str | None = Field(default=None, max_length=2000)
    organ_donor: OrganDonorStatus | None = None
    show_blood_group: bool = True
    show_allergies: bool = True
    show_conditions: bool = False
    show_medications: bool = False


class EmergencyProfileOut(EmergencyProfileModel):
    last_reviewed_at: datetime | None = None


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=7, max_length=20)
    relationship_label: str | None = Field(default=None, max_length=50)
    notify_on_sos: bool = True

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not _PHONE.match(v):
            raise ValueError("Enter a phone number with digits only (you may start with +)")
        return v


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    relationship_label: str | None
    priority: int
    notify_on_sos: bool


def _contact(c: EmergencyContact) -> ContactOut:
    return ContactOut(
        id=c.id,
        name=c.name,
        phone=c.phone,
        relationship_label=c.relationship_label,
        priority=c.priority,
        notify_on_sos=c.notify_on_sos,
    )


@router.get("/patients/{patient_id}/emergency-profile", response_model=EmergencyProfileOut)
async def get_profile(patient_id: uuid.UUID, ctx: PatientRequest = _manage) -> EmergencyProfileOut:
    row = await service.get_profile(ctx.session, ctx.patient_id)
    await ctx.audit("emergency_profile.view", resource_type="emergency_profile")
    await ctx.session.commit()
    if row is None:
        return EmergencyProfileOut()
    return EmergencyProfileOut.model_validate(row, from_attributes=True)


@router.put("/patients/{patient_id}/emergency-profile", response_model=EmergencyProfileOut)
async def save_profile(
    patient_id: uuid.UUID, body: EmergencyProfileModel, ctx: PatientRequest = _manage
) -> EmergencyProfileOut:
    row = await service.save_profile(
        ctx.session, ctx.patient_id, service.ProfileInput(**body.model_dump()), ctx.actor_id
    )
    await ctx.audit(
        "emergency_profile.update",
        resource_type="emergency_profile",
        resource_id=row.id,
        changed_fields=sorted(body.model_dump()),
    )
    await ctx.session.commit()
    return EmergencyProfileOut.model_validate(row, from_attributes=True)


@router.get("/patients/{patient_id}/emergency-contacts", response_model=list[ContactOut])
async def list_contacts(patient_id: uuid.UUID, ctx: PatientRequest = _manage) -> list[ContactOut]:
    rows = await service.list_contacts(ctx.session, ctx.patient_id)
    await ctx.audit("emergency_contact.list", resource_type="emergency_contact")
    await ctx.session.commit()
    return [_contact(c) for c in rows]


@router.post(
    "/patients/{patient_id}/emergency-contacts",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_contact(
    patient_id: uuid.UUID, body: ContactIn, ctx: PatientRequest = _manage
) -> ContactOut:
    contact = await service.add_contact(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        name=body.name,
        phone=body.phone,
        relationship_label=body.relationship_label,
        notify_on_sos=body.notify_on_sos,
    )
    await ctx.audit(
        "emergency_contact.add", resource_type="emergency_contact", resource_id=contact.id
    )
    await ctx.session.commit()
    return _contact(contact)


@router.delete(
    "/patients/{patient_id}/emergency-contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_contact(
    patient_id: uuid.UUID, contact_id: uuid.UUID, ctx: PatientRequest = _manage
) -> Response:
    await service.remove_contact(
        ctx.session, patient_id=ctx.patient_id, contact_id=contact_id, actor=ctx.actor_id
    )
    await ctx.audit(
        "emergency_contact.remove", resource_type="emergency_contact", resource_id=contact_id
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
