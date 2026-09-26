"""Medication safety warnings.

Every warning is presented as "Potential issue detected. Please confirm with a
doctor/pharmacist." Doctors see the source's own wording; patients and caregivers see
plain words. Names from sections the viewer may not see (allergies, conditions) are
hidden from that viewer.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError, ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.dependencies import authenticated
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.identity.service import Principal
from app.modules.safety import engine, service
from app.modules.safety.models import DatasetStatus, ReferenceDataset, SafetyWarning

router = APIRouter(tags=["medication safety"])

_view = patient_request(Permission.VIEW_MEDICATIONS)
_prescribe = patient_request(Permission.CHANGE_DOCTOR_PRESCRIPTION)

HIDDEN = (
    "This may involve health information (such as an allergy or condition) that isn't shared "
    "with you."
)


class WarningOut(BaseModel):
    id: uuid.UUID
    headline: str
    kind: str
    severity: Literal["info", "caution", "serious"]
    source_severity: str | None
    title: str
    detail: str
    source_type: str
    source_name: str
    source_version: str | None
    subjects: list[str]
    status: Literal["open", "resolved"]
    detected_at: datetime
    last_checked_at: datetime
    resolved_at: datetime | None
    review_status: Literal["unreviewed", "acknowledged", "reviewed"]
    reviewed_at: datetime | None
    reviewer_role: str | None
    review_note: str | None


class SafetyFindingOut(BaseModel):
    headline: str
    kind: str
    severity: Literal["info", "caution", "serious"]
    source_severity: str | None
    title: str
    detail: str
    source_name: str
    source_version: str | None
    subjects: list[str]


class SafetyCheckOut(BaseModel):
    findings: list[SafetyFindingOut]
    unknown_ingredients: list[str]
    datasets: list[str]


class WarningReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str | None = Field(default=None, max_length=500)


class DatasetOut(BaseModel):
    key: str
    name: str
    version: str
    url: str
    license: str
    reviewed_on: str


def _can_see(ctx: PatientRequest, needs: list[str]) -> bool:
    return "medical_history" not in needs or ctx.allows(Permission.VIEW_MEDICAL_HISTORY)


def _warning_out(ctx: PatientRequest, w: SafetyWarning) -> WarningOut:
    doctor = "doctor" in ctx.access.via
    detail = w.clinician_detail if doctor else w.patient_detail
    if not _can_see(ctx, w.needs):
        detail = HIDDEN + (" Ask the patient or their doctor." if doctor else "")
    return WarningOut(
        id=w.id,
        headline=engine.HEADLINE,
        kind=w.kind.value,
        severity=w.severity.value,
        source_severity=w.source_severity,
        title=w.title,
        detail=detail,
        source_type=w.source_type,
        source_name=w.source_name,
        source_version=w.source_version,
        subjects=[s for s in w.subjects if _can_see(ctx, w.needs) or s.startswith(("med:", "rx"))],
        status=w.status.value,
        detected_at=w.detected_at,
        last_checked_at=w.last_checked_at,
        resolved_at=w.resolved_at,
        review_status=w.review_status.value,
        reviewed_at=w.reviewed_at,
        reviewer_role=w.reviewer_role,
        review_note=w.review_note,
    )


@router.get("/patients/{patient_id}/safety-warnings", response_model=list[WarningOut])
async def list_warnings(
    patient_id: uuid.UUID, include_resolved: bool = False, ctx: PatientRequest = _view
) -> list[WarningOut]:
    rows = await service.list_warnings(
        ctx.session, ctx.patient_id, include_resolved=include_resolved
    )
    await ctx.audit("safety_warning.list", resource_type="safety_warning")
    await ctx.session.commit()
    return [_warning_out(ctx, w) for w in rows]


@router.post("/patients/{patient_id}/safety-warnings/recheck", response_model=list[WarningOut])
async def recheck(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[WarningOut]:
    """Run the checks again now (they also run automatically after changes)."""
    rows = await service.recheck(ctx.session, ctx.patient_id, trigger="manual", actor=ctx.actor_id)
    await ctx.audit(
        "safety_warning.recheck", resource_type="safety_warning", context={"open": str(len(rows))}
    )
    await ctx.session.commit()
    return [_warning_out(ctx, w) for w in rows]


@router.post(
    "/patients/{patient_id}/safety-warnings/{warning_id}/review", response_model=WarningOut
)
async def review_warning(
    patient_id: uuid.UUID, warning_id: uuid.UUID, body: WarningReviewIn, ctx: PatientRequest = _view
) -> WarningOut:
    """Doctor: mark reviewed with a note. Patient or caregiver: acknowledge having seen it.
    Reviewing never changes a medicine."""
    as_doctor = "doctor" in ctx.access.via
    if as_doctor:
        if not ctx.allows(Permission.EDIT_CLINICAL_RECORDS):
            raise ForbiddenError("You do not have permission to do this for this patient.")
        await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
        if not body.note:
            raise ValidationFailedError("Add a short review note.")
    row = await service.review(
        ctx.session,
        patient_id=ctx.patient_id,
        warning_id=warning_id,
        actor=ctx.actor_id,
        as_doctor=as_doctor,
        note=body.note,
    )
    await ctx.audit(
        "safety_warning.review" if as_doctor else "safety_warning.acknowledge",
        resource_type="safety_warning",
        resource_id=row.id,
        changed_fields=["review_status"],
    )
    await ctx.session.commit()
    return _warning_out(ctx, row)


@router.post(
    "/patients/{patient_id}/prescriptions/{prescription_id}/safety-check",
    response_model=SafetyCheckOut,
)
async def check_prescription(
    patient_id: uuid.UUID, prescription_id: uuid.UUID, ctx: PatientRequest = _prescribe
) -> SafetyCheckOut:
    """Potential issues with a (draft) prescription against current medicines, allergies
    and conditions, before it is issued. Nothing is stored."""
    report = await service.preview_prescription(ctx.session, ctx.patient_id, prescription_id)
    await ctx.audit(
        "safety_warning.preview",
        resource_type="prescription",
        resource_id=prescription_id,
        context={"findings": str(len(report.findings))},
    )
    await ctx.session.commit()
    return SafetyCheckOut(
        findings=[
            SafetyFindingOut(
                headline=engine.HEADLINE,
                kind=f.kind.value,
                severity=f.severity.value,
                source_severity=f.source_severity,
                title=f.title,
                detail=f.clinician_detail if _can_see(ctx, list(f.needs)) else HIDDEN,
                source_name=f.source.name,
                source_version=f.source.version,
                subjects=list(f.subjects),
            )
            for f in report.findings
        ],
        unknown_ingredients=report.unresolved,
        datasets=report.datasets,
    )


@router.get("/safety/reference-datasets", response_model=list[DatasetOut])
async def reference_datasets(
    principal: Principal = Depends(authenticated()), session: AsyncSession = Depends(get_session)
) -> list[DatasetOut]:
    """Which trusted datasets the checks currently use (empty: only record comparison and
    prescription consistency checks run)."""
    rows = (
        await session.scalars(
            select(ReferenceDataset)
            .where(ReferenceDataset.status == DatasetStatus.ACTIVE)
            .order_by(ReferenceDataset.name)
        )
    ).all()
    return [
        DatasetOut(
            key=r.key,
            name=r.name,
            version=r.version,
            url=r.url,
            license=r.license,
            reviewed_on=r.reviewed_on.isoformat(),
        )
        for r in rows
    ]
