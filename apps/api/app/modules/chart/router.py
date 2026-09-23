"""Patient chart: profile, overview and timeline. Sections the caller may not see are
simply absent; `permissions` tells the UI which sections to offer."""

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.chart import service
from app.modules.patients import service as patients
from app.modules.patients.models import PatientProfile

router = APIRouter(tags=["patient chart"])

_any = patient_request()
_profile = patient_request(Permission.VIEW_PROFILE)


class ProfileOut(BaseModel):
    id: uuid.UUID
    given_name: str
    family_name: str | None
    display_name: str
    date_of_birth: date | None
    age_years: int | None
    sex_at_birth: str
    blood_group: str
    has_account: bool
    status: str


class OverviewOut(BaseModel):
    patient_id: uuid.UUID
    via: list[str]
    permissions: list[str]
    profile: ProfileOut | None
    active_conditions: list[str] | None
    allergies: list[str] | None
    active_medications: int | None
    open_test_orders: int | None
    next_appointment: datetime | None
    next_follow_up: date | None
    last_visit: datetime | None


class TimelineEventOut(BaseModel):
    at: datetime
    kind: str
    title: str
    detail: str | None
    status: str | None
    resource_id: uuid.UUID | None


def display_name(p: PatientProfile) -> str:
    return " ".join(x for x in (p.given_name, p.family_name) if x)


def age(dob: date | None, today: date | None = None) -> int | None:
    if dob is None:
        return None
    today = today or datetime.now(UTC).date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def profile_out(p: PatientProfile) -> ProfileOut:
    return ProfileOut(
        id=p.id,
        given_name=p.given_name,
        family_name=p.family_name,
        display_name=display_name(p),
        date_of_birth=p.date_of_birth,
        age_years=age(p.date_of_birth),
        sex_at_birth=p.sex_at_birth.value,
        blood_group=p.blood_group.value,
        has_account=p.user_id is not None,
        status=p.status.value,
    )


@router.get("/patients/{patient_id}/profile", response_model=ProfileOut)
async def get_profile(patient_id: uuid.UUID, ctx: PatientRequest = _profile) -> ProfileOut:
    profile = await patients.get_live_profile(ctx.session, ctx.patient_id)
    assert profile is not None  # the access check already confirmed it exists
    await ctx.audit("patient.profile_view", resource_type="patient_profile", resource_id=profile.id)
    await ctx.session.commit()
    return profile_out(profile)


@router.get("/patients/{patient_id}/overview", response_model=OverviewOut)
async def get_overview(patient_id: uuid.UUID, ctx: PatientRequest = _any) -> OverviewOut:
    profile = (
        await patients.get_live_profile(ctx.session, ctx.patient_id)
        if ctx.allows(Permission.VIEW_PROFILE)
        else None
    )
    ov = await service.overview(ctx.session, ctx.access)
    await ctx.audit(
        "patient.overview_view",
        resource_type="patient_chart",
        context={"sections": ",".join(ov.sections)},
    )
    await ctx.session.commit()
    return OverviewOut(
        patient_id=ctx.patient_id,
        via=sorted(ctx.access.via),
        permissions=sorted(p.value for p in ctx.access.permissions),
        profile=profile_out(profile) if profile else None,
        active_conditions=ov.active_conditions,
        allergies=ov.allergies,
        active_medications=ov.active_medications,
        open_test_orders=ov.open_test_orders,
        next_appointment=ov.next_appointment,
        next_follow_up=ov.next_follow_up,
        last_visit=ov.last_visit,
    )


@router.get("/patients/{patient_id}/timeline", response_model=list[TimelineEventOut])
async def get_timeline(patient_id: uuid.UUID, ctx: PatientRequest = _any) -> list[TimelineEventOut]:
    events = await service.timeline(
        ctx.session, ctx.access, await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    )
    await ctx.audit("patient.timeline_view", resource_type="patient_chart")
    await ctx.session.commit()
    return [TimelineEventOut(**e.__dict__) for e in events]
