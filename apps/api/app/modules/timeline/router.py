"""Medical record timeline: one chronological, filterable, paginated list per patient,
and the change history of each record."""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.errors import ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.care_team import service as care_team
from app.modules.timeline import service

router = APIRouter(tags=["timeline"])

_any = patient_request()

Kind = Literal[
    "visit",
    "symptom",
    "note",
    "assessment",
    "reported_condition",
    "prescription",
    "medication",
    "test_order",
    "report",
    "appointment",
    "follow_up",
    "document",
]


class DoctorOut(BaseModel):
    id: uuid.UUID | None
    name: str | None
    specialty: str | None


class TimelineEventOut(BaseModel):
    key: str
    at: datetime
    date_only: bool
    kind: Kind
    title: str
    detail: str | None
    status: str | None
    resource_id: uuid.UUID
    visit_id: uuid.UUID | None
    source: str | None
    doctor: DoctorOut | None
    amended: bool
    has_history: bool


class DoctorFacet(DoctorOut):
    count: int


class ValueCount(BaseModel):
    value: str
    count: int


class FacetsOut(BaseModel):
    kinds: list[ValueCount]
    doctors: list[DoctorFacet]
    specialties: list[ValueCount]
    earliest: datetime | None
    latest: datetime | None


class TimelinePageOut(BaseModel):
    items: list[TimelineEventOut]
    next_cursor: str | None
    total: int
    facets: FacetsOut


class FieldChangeOut(BaseModel):
    field: str
    before: str | None
    after: str | None


class ActorOut(BaseModel):
    role: Literal["doctor", "patient", "caregiver", "system"]
    name: str | None


class HistoryEntryOut(BaseModel):
    at: datetime
    action: Literal["created", "changed", "signed", "amended", "revised"]
    actor: ActorOut
    reason: str | None
    version: int | None
    changes: list[FieldChangeOut]


class RecordHistoryOut(BaseModel):
    kind: Kind
    resource_id: uuid.UUID
    entries: list[HistoryEntryOut]


def _event_out(e: service.TimelineEvent) -> TimelineEventOut:
    return TimelineEventOut(
        key=e.key,
        at=e.at,
        date_only=e.date_only,
        kind=e.kind,
        title=e.title,
        detail=e.detail,
        status=e.status,
        resource_id=e.resource_id,
        visit_id=e.visit_id,
        source=e.source,
        doctor=DoctorOut(**e.doctor.__dict__) if e.doctor else None,
        amended=e.amended,
        has_history=e.has_history,
    )


@router.get("/patients/{patient_id}/timeline", response_model=TimelinePageOut)
async def get_timeline(
    patient_id: uuid.UUID,
    ctx: PatientRequest = _any,
    kind: Annotated[list[Kind] | None, Query(description="Record types to include")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    doctor_id: uuid.UUID | None = None,
    specialty: Annotated[str | None, Query(max_length=120)] = None,
    order: Literal["newest", "oldest"] = "newest",
    cursor: Annotated[str | None, Query(max_length=400)] = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_LIMIT)] = 50,
) -> TimelinePageOut:
    """Everything the caller may see for this patient, in date order. Record types the
    caller has no permission or consent for are left out entirely; `facets` are counted
    over what the caller may see, before filters."""
    if date_from and date_to and date_from > date_to:
        raise ValidationFailedError("The start date must be before the end date.")
    viewer_doctor = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    events = await service.collect(ctx.session, ctx.access, viewer_doctor_id=viewer_doctor)
    flt = service.TimelineFilter(
        kinds=frozenset(kind or ()),
        date_from=date_from,
        date_to=date_to,
        doctor_id=doctor_id,
        specialty=specialty.strip() if specialty else None,
    )
    page = service.paginate(events, flt, newest_first=order == "newest", cursor=cursor, limit=limit)
    await ctx.audit(
        "patient.timeline_view",
        resource_type="patient_chart",
        context={"filtered": str(bool(kind or date_from or date_to or doctor_id or specialty))},
    )
    await ctx.session.commit()
    f = page.facets
    return TimelinePageOut(
        items=[_event_out(e) for e in page.events],
        next_cursor=page.next_cursor,
        total=page.total,
        facets=FacetsOut(
            kinds=[ValueCount(value=k, count=n) for k, n in f.kinds.items()],
            doctors=[
                DoctorFacet(id=d.id, name=d.name, specialty=d.specialty, count=n)
                for d, n in f.doctors
            ],
            specialties=[ValueCount(value=s, count=n) for s, n in f.specialties],
            earliest=f.earliest,
            latest=f.latest,
        ),
    )


@router.get(
    "/patients/{patient_id}/timeline/{kind}/{resource_id}/history", response_model=RecordHistoryOut
)
async def get_history(
    patient_id: uuid.UUID, kind: Kind, resource_id: uuid.UUID, ctx: PatientRequest = _any
) -> RecordHistoryOut:
    """Who created and changed this record, when, what changed and why. Records the
    caller cannot see return 404."""
    viewer_doctor = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    entries = await service.history(
        ctx.session, ctx.access, kind=kind, resource_id=resource_id, viewer_doctor_id=viewer_doctor
    )
    await ctx.audit(
        "record.history_view",
        resource_type=service.KIND_TABLE.get(kind, kind),
        resource_id=resource_id,
    )
    await ctx.session.commit()
    return RecordHistoryOut(
        kind=kind,
        resource_id=resource_id,
        entries=[
            HistoryEntryOut(
                at=e.at,
                action=e.action,
                actor=ActorOut(role=e.actor.role, name=e.actor.name),
                reason=e.reason,
                version=e.version,
                changes=[FieldChangeOut(**c.__dict__) for c in e.changes],
            )
            for e in entries
        ],
    )
