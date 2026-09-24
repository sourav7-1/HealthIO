"""Visits, clinical notes and the problem list (diagnoses as documented by the doctor)."""

import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.clinical import service
from app.modules.clinical.models import (
    AllergenCategory,
    ClinicalNote,
    ConditionClinicalStatus,
    ConditionVerificationStatus,
    DoctorVisit,
    MedicalCondition,
    NoteStatus,
    NoteType,
    ReactionSeverity,
    Severity,
    VisitStatus,
    VisitType,
)

router = APIRouter(tags=["clinical"])

_view_visits = patient_request(Permission.VIEW_VISITS)
_edit = patient_request(Permission.EDIT_CLINICAL_RECORDS)
_view_history = patient_request(Permission.VIEW_MEDICAL_HISTORY)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VisitIn(_In):
    visit_type: VisitType
    started_at: datetime | None = None
    chief_complaint: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=200)


class VisitOut(BaseModel):
    id: uuid.UUID
    visit_type: VisitType
    status: VisitStatus
    started_at: datetime | None
    ended_at: datetime | None
    chief_complaint: str | None
    location: str | None
    doctor_id: uuid.UUID
    doctor_name: str | None
    recorded_by_me: bool


class NoteIn(_In):
    note_type: NoteType = NoteType.CONSULTATION
    body: str = Field(min_length=1, max_length=20000)


class NoteUpdate(_In):
    body: str = Field(min_length=1, max_length=20000)
    note_type: NoteType | None = None


class AmendmentIn(_In):
    body: str = Field(min_length=1, max_length=20000)
    reason: str = Field(min_length=3, max_length=300)


class NoteOut(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    note_type: NoteType
    status: NoteStatus
    body: str
    author_doctor_id: uuid.UUID
    author_name: str | None
    written_by_me: bool
    created_at: datetime
    signed_at: datetime | None
    supersedes_note_id: uuid.UUID | None
    amendment_reason: str | None


class VisitDetailOut(BaseModel):
    visit: VisitOut
    notes: list[NoteOut]


class ConditionIn(_In):
    name: str = Field(min_length=1, max_length=300)
    icd10_code: str | None = Field(default=None, max_length=10)
    clinical_status: ConditionClinicalStatus = ConditionClinicalStatus.ACTIVE
    verification_status: ConditionVerificationStatus
    severity: Severity | None = None
    onset_date: date | None = None
    visit_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ConditionOut(BaseModel):
    id: uuid.UUID
    name: str
    icd10_code: str | None
    clinical_status: str
    verification_status: str
    severity: str | None
    onset_date: date | None
    source: str
    visit_id: uuid.UUID | None
    notes: str | None
    recorded_at: datetime


class AllergyOut(BaseModel):
    id: uuid.UUID
    substance: str
    category: str
    reaction: str | None
    severity: str | None
    clinical_status: str
    verification_status: str
    source: str


class HistoryOut(BaseModel):
    id: uuid.UUID
    category: str
    title: str
    occurred_on: date | None
    family_relation: str | None
    source: str


class MedicalHistoryOut(BaseModel):
    conditions: list[ConditionOut]
    allergies: list[AllergyOut]
    history: list[HistoryOut]


async def _visit_out(ctx: PatientRequest, visits: list[DoctorVisit]) -> list[VisitOut]:
    names = await care_team.doctor_names(ctx.session, {v.doctor_id for v in visits})
    me = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    return [
        VisitOut(
            id=v.id,
            visit_type=v.visit_type,
            status=v.status,
            started_at=v.started_at,
            ended_at=v.ended_at,
            chief_complaint=v.chief_complaint,
            location=v.location,
            doctor_id=v.doctor_id,
            doctor_name=names.get(v.doctor_id),
            recorded_by_me=v.doctor_id == me,
        )
        for v in visits
    ]


async def _note_out(ctx: PatientRequest, notes: list[ClinicalNote]) -> list[NoteOut]:
    names = await care_team.doctor_names(ctx.session, {n.author_doctor_id for n in notes})
    me = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    return [
        NoteOut(
            id=n.id,
            visit_id=n.visit_id,
            note_type=n.note_type,
            status=n.status,
            body=n.body,
            author_doctor_id=n.author_doctor_id,
            author_name=names.get(n.author_doctor_id),
            written_by_me=n.author_doctor_id == me,
            created_at=n.created_at,
            signed_at=n.signed_at,
            supersedes_note_id=n.supersedes_note_id,
            amendment_reason=n.amendment_reason,
        )
        for n in notes
    ]


# --- visits --------------------------------------------------------------------------


@router.get("/patients/{patient_id}/visits", response_model=list[VisitOut])
async def list_visits(patient_id: uuid.UUID, ctx: PatientRequest = _view_visits) -> list[VisitOut]:
    visits = await service.list_visits(ctx.session, ctx.patient_id)
    await ctx.audit("visit.list", resource_type="doctor_visit")
    await ctx.session.commit()
    return await _visit_out(ctx, visits)


@router.post(
    "/patients/{patient_id}/visits", response_model=VisitOut, status_code=status.HTTP_201_CREATED
)
async def record_visit(
    patient_id: uuid.UUID, body: VisitIn, ctx: PatientRequest = _edit
) -> VisitOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    visit = await service.create_visit(
        ctx.session,
        patient_id=ctx.patient_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        visit_type=body.visit_type,
        started_at=body.started_at,
        chief_complaint=body.chief_complaint,
        location=body.location,
    )
    await ctx.audit("visit.create", resource_type="doctor_visit", resource_id=visit.id)
    await ctx.session.commit()
    return (await _visit_out(ctx, [visit]))[0]


@router.get("/patients/{patient_id}/visits/{visit_id}", response_model=VisitDetailOut)
async def get_visit(
    patient_id: uuid.UUID, visit_id: uuid.UUID, ctx: PatientRequest = _view_visits
) -> VisitDetailOut:
    visit = await service.get_visit(ctx.session, ctx.patient_id, visit_id)
    notes = await service.list_notes(
        ctx.session,
        patient_id=ctx.patient_id,
        viewer_doctor_id=await care_team.doctor_id_for(ctx.session, ctx.actor_id),
        visit_id=visit.id,
    )
    await ctx.audit("visit.view", resource_type="doctor_visit", resource_id=visit.id)
    await ctx.session.commit()
    return VisitDetailOut(
        visit=(await _visit_out(ctx, [visit]))[0], notes=await _note_out(ctx, notes)
    )


@router.post("/patients/{patient_id}/visits/{visit_id}/complete", response_model=VisitOut)
async def complete_visit(
    patient_id: uuid.UUID, visit_id: uuid.UUID, ctx: PatientRequest = _edit
) -> VisitOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    visit = await service.complete_visit(
        ctx.session,
        patient_id=ctx.patient_id,
        visit_id=visit_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
    )
    await ctx.audit(
        "visit.complete",
        resource_type="doctor_visit",
        resource_id=visit.id,
        changed_fields=["status", "ended_at"],
    )
    await ctx.session.commit()
    return (await _visit_out(ctx, [visit]))[0]


# --- notes ---------------------------------------------------------------------------


@router.get("/patients/{patient_id}/notes", response_model=list[NoteOut])
async def list_notes(patient_id: uuid.UUID, ctx: PatientRequest = _view_visits) -> list[NoteOut]:
    notes = await service.list_notes(
        ctx.session,
        patient_id=ctx.patient_id,
        viewer_doctor_id=await care_team.doctor_id_for(ctx.session, ctx.actor_id),
    )
    await ctx.audit("note.list", resource_type="clinical_note")
    await ctx.session.commit()
    return await _note_out(ctx, notes)


@router.post(
    "/patients/{patient_id}/visits/{visit_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_note(
    patient_id: uuid.UUID, visit_id: uuid.UUID, body: NoteIn, ctx: PatientRequest = _edit
) -> NoteOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    note = await service.create_note(
        ctx.session,
        patient_id=ctx.patient_id,
        visit_id=visit_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        note_type=body.note_type,
        body=body.body,
    )
    await ctx.audit("note.create", resource_type="clinical_note", resource_id=note.id)
    await ctx.session.commit()
    return (await _note_out(ctx, [note]))[0]


@router.put("/patients/{patient_id}/notes/{note_id}", response_model=NoteOut)
async def update_note(
    patient_id: uuid.UUID, note_id: uuid.UUID, body: NoteUpdate, ctx: PatientRequest = _edit
) -> NoteOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    note = await service.update_draft(
        ctx.session,
        patient_id=ctx.patient_id,
        note_id=note_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        body=body.body,
        note_type=body.note_type,
    )
    await ctx.audit(
        "note.update_draft",
        resource_type="clinical_note",
        resource_id=note.id,
        changed_fields=["body"],
    )
    await ctx.session.commit()
    return (await _note_out(ctx, [note]))[0]


@router.post("/patients/{patient_id}/notes/{note_id}/sign", response_model=NoteOut)
async def sign_note(
    patient_id: uuid.UUID, note_id: uuid.UUID, ctx: PatientRequest = _edit
) -> NoteOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    note = await service.sign_note(
        ctx.session,
        patient_id=ctx.patient_id,
        note_id=note_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
    )
    await ctx.audit(
        "note.sign",
        resource_type="clinical_note",
        resource_id=note.id,
        changed_fields=["status", "signed_at"],
    )
    await ctx.session.commit()
    return (await _note_out(ctx, [note]))[0]


@router.post(
    "/patients/{patient_id}/notes/{note_id}/amendments",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def amend_note(
    patient_id: uuid.UUID, note_id: uuid.UUID, body: AmendmentIn, ctx: PatientRequest = _edit
) -> NoteOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    note = await service.start_amendment(
        ctx.session,
        patient_id=ctx.patient_id,
        note_id=note_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        body=body.body,
        reason=body.reason,
    )
    await ctx.audit(
        "note.amend",
        resource_type="clinical_note",
        resource_id=note.id,
        context={"supersedes": str(note_id)},
    )
    await ctx.session.commit()
    return (await _note_out(ctx, [note]))[0]


# --- medical history -----------------------------------------------------------------


@router.get("/patients/{patient_id}/medical-history", response_model=MedicalHistoryOut)
async def medical_history(
    patient_id: uuid.UUID, ctx: PatientRequest = _view_history
) -> MedicalHistoryOut:
    conditions = await service.list_conditions(ctx.session, ctx.patient_id)
    allergies = await service.list_allergies(ctx.session, ctx.patient_id)
    history = await service.list_history(ctx.session, ctx.patient_id)
    await ctx.audit("medical_history.view", resource_type="medical_history")
    await ctx.session.commit()
    return MedicalHistoryOut(
        conditions=[_condition_out(c) for c in conditions],
        allergies=[
            AllergyOut(
                id=a.id,
                substance=a.substance,
                category=a.category.value,
                reaction=a.reaction,
                severity=a.severity.value if a.severity else None,
                clinical_status=a.clinical_status.value,
                verification_status=a.verification_status.value,
                source=a.source.value,
            )
            for a in allergies
        ],
        history=[
            HistoryOut(
                id=h.id,
                category=h.category.value,
                title=h.title,
                occurred_on=h.occurred_on,
                family_relation=h.family_relation,
                source=h.source.value,
            )
            for h in history
        ],
    )


def _condition_out(c: MedicalCondition) -> ConditionOut:
    return ConditionOut(
        id=c.id,
        name=c.name,
        icd10_code=c.icd10_code,
        clinical_status=c.clinical_status.value,
        verification_status=c.verification_status.value,
        severity=c.severity.value if c.severity else None,
        onset_date=c.onset_date,
        source=c.source.value,
        visit_id=c.visit_id,
        notes=c.notes,
        recorded_at=c.created_at,
    )


@router.post(
    "/patients/{patient_id}/conditions",
    response_model=ConditionOut,
    status_code=status.HTTP_201_CREATED,
)
async def document_condition(
    patient_id: uuid.UUID, body: ConditionIn, ctx: PatientRequest = _edit
) -> ConditionOut:
    """Record an assessment/diagnosis exactly as the doctor documents it."""
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    condition = await service.record_condition(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        name=body.name,
        icd10_code=body.icd10_code,
        clinical_status=body.clinical_status,
        verification_status=body.verification_status,
        severity=body.severity,
        onset_date=body.onset_date,
        visit_id=body.visit_id,
        notes=body.notes,
    )
    await ctx.audit(
        "condition.document", resource_type="medical_condition", resource_id=condition.id
    )
    await ctx.session.commit()
    return _condition_out(condition)


# --- patient-reported entries ----------------------------------------------------------------

_self_report = patient_request(Permission.REPORT_HEALTH_INFO)


class ReportedAllergyIn(_In):
    substance: str = Field(min_length=1, max_length=200)
    category: AllergenCategory = AllergenCategory.MEDICATION
    reaction: str | None = Field(default=None, max_length=300)
    severity: ReactionSeverity | None = None


class ReportedConditionIn(_In):
    name: str = Field(min_length=1, max_length=300)
    onset_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.post(
    "/patients/{patient_id}/self-reported/allergies",
    response_model=AllergyOut,
    status_code=status.HTTP_201_CREATED,
)
async def report_allergy(
    patient_id: uuid.UUID, body: ReportedAllergyIn, ctx: PatientRequest = _self_report
) -> AllergyOut:
    """Stored as patient-reported and unconfirmed; doctors see it labelled that way."""
    a = await service.report_allergy(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        substance=body.substance,
        category=body.category,
        reaction=body.reaction,
        severity=body.severity,
        source=ctx.reporter_source,
    )
    await ctx.audit("allergy.self_reported", resource_type="allergy", resource_id=a.id)
    await ctx.session.commit()
    return AllergyOut(
        id=a.id,
        substance=a.substance,
        category=a.category.value,
        reaction=a.reaction,
        severity=a.severity.value if a.severity else None,
        clinical_status=a.clinical_status.value,
        verification_status=a.verification_status.value,
        source=a.source.value,
    )


@router.post(
    "/patients/{patient_id}/self-reported/conditions",
    response_model=ConditionOut,
    status_code=status.HTTP_201_CREATED,
)
async def report_condition(
    patient_id: uuid.UUID, body: ReportedConditionIn, ctx: PatientRequest = _self_report
) -> ConditionOut:
    c = await service.report_condition(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        name=body.name,
        onset_date=body.onset_date,
        notes=body.notes,
        source=ctx.reporter_source,
    )
    await ctx.audit("condition.self_reported", resource_type="medical_condition", resource_id=c.id)
    await ctx.session.commit()
    return _condition_out(c)


@router.delete(
    "/patients/{patient_id}/self-reported/{kind}/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_self_reported(
    patient_id: uuid.UUID,
    kind: Literal["allergies", "conditions"],
    entry_id: uuid.UUID,
    ctx: PatientRequest = _self_report,
) -> Response:
    """Only entries the patient reported; a doctor's entries cannot be removed here."""
    await service.remove_self_reported(
        ctx.session, patient_id=ctx.patient_id, kind=kind, entry_id=entry_id, actor=ctx.actor_id
    )
    await ctx.audit(f"{kind}.self_reported_removed", resource_type=kind, resource_id=entry_id)
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
