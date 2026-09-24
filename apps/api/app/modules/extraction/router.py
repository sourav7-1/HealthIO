"""Prescription scans API: read an uploaded prescription photo with AI (or type it in),
review every field, then save it as a patient- or doctor-verified prescription.

Policies: viewing needs VIEW_REPORTS; starting a scan needs UPLOAD_REPORTS; reviewing,
confirming and rejecting need UPLOAD_REPORTS *and* being able to vouch for the content:
the patient, a caregiver with REPORT_HEALTH_INFO, or a linked doctor who may edit the
chart. AI reading also needs the patient's `ai_processing` consent.
"""

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai.ocr import build_ocr
from app.ai.providers import VisionExtractor, build_extractor
from app.ai.schemas import CRITICAL_FIELDS, HEADER_FIELDS, ITEM_FIELDS
from app.core.errors import ConflictError, ForbiddenError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.consent import service as consent
from app.modules.consent.models import ConsentPurpose, DataCategory, GrantorCapacity
from app.modules.extraction import normalize, service
from app.modules.extraction.models import (
    PrescriptionScan,
    PrescriptionScanMode,
    PrescriptionScanStatus,
    VerifierRole,
)
from app.modules.medications import service as medications

router = APIRouter(tags=["prescription scans"])

_view = patient_request(Permission.VIEW_REPORTS)
_upload = patient_request(Permission.UPLOAD_REPORTS)
_consent = patient_request(Permission.MANAGE_CONSENT)

AI_NOTICE = (
    "With your permission, photos of prescriptions you upload are read by an AI model "
    "(Anthropic Claude) to fill in the medicines for you. Only the photo is sent, not your "
    "name or other records. The AI can make mistakes: nothing is saved until you check "
    "every field. You can turn this off at any time and type prescriptions in yourself."
)


def extractor_for(request: Request) -> VisionExtractor | None:
    state = request.app.state
    if not hasattr(state, "ai_extractor"):
        state.ai_extractor = build_extractor(state.settings)
    extractor: VisionExtractor | None = state.ai_extractor
    return extractor


def ocr_for(request: Request) -> Any:
    state = request.app.state
    if not hasattr(state, "ocr_engine"):
        state.ocr_engine = build_ocr(state.settings.ai_ocr_engine)
    return state.ocr_engine


# --- AI consent -------------------------------------------------------------------------------


class AiConsentOut(BaseModel):
    granted: bool
    granted_at: datetime | None
    notice_version: str
    notice: str
    ai_available: bool


class AiConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    granted: bool


async def _consent_out(ctx: PatientRequest, request: Request) -> AiConsentOut:
    record = await consent.active_platform_consent(
        ctx.session,
        patient_id=ctx.patient_id,
        purpose=ConsentPurpose.AI_PROCESSING,
        now=datetime.now(UTC),
    )
    return AiConsentOut(
        granted=record is not None,
        granted_at=record.valid_from if record else None,
        notice_version=service.AI_NOTICE_VERSION,
        notice=AI_NOTICE,
        ai_available=extractor_for(request) is not None,
    )


@router.get("/patients/{patient_id}/ai-consent", response_model=AiConsentOut)
async def get_ai_consent(
    patient_id: uuid.UUID, request: Request, ctx: PatientRequest = _upload
) -> AiConsentOut:
    return await _consent_out(ctx, request)


@router.put("/patients/{patient_id}/ai-consent", response_model=AiConsentOut)
async def set_ai_consent(
    patient_id: uuid.UUID, body: AiConsentIn, request: Request, ctx: PatientRequest = _consent
) -> AiConsentOut:
    """The patient (or a dependant's guardian) allows or stops AI reading of documents."""
    now = datetime.now(UTC)
    if body.granted:
        await consent.grant_platform_consent(
            ctx.session,
            patient_id=ctx.patient_id,
            purpose=ConsentPurpose.AI_PROCESSING,
            categories={DataCategory.PRESCRIPTIONS, DataCategory.DOCUMENTS},
            granted_by=ctx.actor_id,
            capacity=GrantorCapacity.SELF if "self" in ctx.access.via else GrantorCapacity.GUARDIAN,
            notice_version=service.AI_NOTICE_VERSION,
            now=now,
        )
        await ctx.audit("consent.ai_processing_granted", resource_type="consent_record")
    else:
        withdrawn = await consent.withdraw_platform_consent(
            ctx.session,
            patient_id=ctx.patient_id,
            purpose=ConsentPurpose.AI_PROCESSING,
            withdrawn_by=ctx.actor_id,
            now=now,
        )
        if withdrawn:
            await ctx.audit("consent.ai_processing_withdrawn", resource_type="consent_record")
    await ctx.session.commit()
    return await _consent_out(ctx, request)


# --- scans ------------------------------------------------------------------------------------


class AiReadingOut(BaseModel):
    value: str | None
    confidence: float
    model_confidence: float
    band: Literal["high", "medium", "low", "absent"]
    legibility: str
    evidence: str | None
    region: dict[str, float] | None
    region_source: str | None
    flags: list[str]
    message: str | None


class ReviewStateOut(BaseModel):
    status: Literal["unverified", "confirmed", "corrected", "not_on_prescription"]
    value: str | None
    at: datetime | None


class ScanFieldOut(BaseModel):
    ai: AiReadingOut | None  # None: typed in by a person (manual entry or an added line)
    review: ReviewStateOut
    critical: bool
    requires_confirmation: bool
    # Dictionary-based meaning of the current text, e.g. "1 in the morning, 1 at night".
    interpretation: str | None


class ScanItemOut(BaseModel):
    key: str
    from_ai: bool
    removed: bool
    fields: dict[str, ScanFieldOut]


class ScanIssueOut(BaseModel):
    path: str
    problem: str


class ScanSummaryOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    mode: PrescriptionScanMode
    status: PrescriptionScanStatus
    created_at: datetime
    prescription_id: uuid.UUID | None
    error_message: str | None


class ScanOut(ScanSummaryOut):
    error_code: str | None
    is_prescription: bool | None
    handwritten: bool | None
    reading_notes: list[str]
    discarded_keys: list[str]
    model_metadata: dict[str, Any]
    header: dict[str, ScanFieldOut]
    items: list[ScanItemOut]
    issues: list[ScanIssueOut]
    verifier_role: VerifierRole | None
    verification_level: str | None
    verified_at: datetime | None
    rejected_reason: str | None


def _interpret(field: str, text: str | None) -> str | None:
    if not text:
        return None
    if field == "frequency":
        f = normalize.frequency(text)
        return f.meaning if f else None
    if field == "meal_relation":
        m = normalize.meal(text)
        return m.meaning if m else None
    if field == "duration":
        d = normalize.duration(text)
        return d.meaning if d else None
    if field == "dose":
        ds = normalize.dose(text)
        return ds.meaning if ds else None
    if field == "prescription_date":
        parsed = normalize.prescription_date(text, datetime.now(UTC).date())
        return parsed.strftime("%d %B %Y") if parsed else None
    return None


def _field_out(name: str, state: dict[str, Any], ai: dict[str, Any] | None) -> ScanFieldOut:
    critical = name in CRITICAL_FIELDS
    current = state["value"] if state["status"] != "not_on_prescription" else None
    return ScanFieldOut(
        ai=AiReadingOut.model_validate(ai) if ai else None,
        review=ReviewStateOut(status=state["status"], value=state["value"], at=state["at"]),
        critical=critical,
        requires_confirmation=critical or (ai is not None and ai["band"] in ("medium", "low")),
        interpretation=_interpret(name, current),
    )


def _scan_out(scan: PrescriptionScan) -> ScanOut:
    review, assessment = service.load(scan)
    result = json.loads(scan.result) if scan.result else {}
    items = [
        ScanItemOut(
            key=item["key"],
            from_ai=item.get("source_index") is not None,
            removed=item["removed"],
            fields={
                f: _field_out(f, item["fields"][f], service.assessed_field(assessment, item, f))
                for f in ITEM_FIELDS
            },
        )
        for item in review["items"]
    ]
    header = {
        f: _field_out(f, review["header"][f], service.assessed_field(assessment, None, f))
        for f in HEADER_FIELDS
    }
    issues = (
        service.blocking_issues(review, assessment)
        if scan.status == PrescriptionScanStatus.NEEDS_REVIEW
        else []
    )
    return ScanOut(
        id=scan.id,
        document_id=scan.document_id,
        mode=scan.mode,
        status=scan.status,
        created_at=scan.created_at,
        prescription_id=scan.prescription_id,
        error_message=scan.error_message,
        error_code=scan.error_code,
        is_prescription=assessment["is_prescription"] if assessment else None,
        handwritten=assessment["handwritten"] if assessment else None,
        reading_notes=list(assessment.get("reading_notes", [])) if assessment else [],
        discarded_keys=list(result.get("discarded_keys", [])),
        model_metadata=scan.metadata_dict(),
        header=header,
        items=items,
        issues=[ScanIssueOut(**i) for i in issues],
        verifier_role=scan.verifier_role,
        verification_level=scan.verification_level.value if scan.verification_level else None,
        verified_at=scan.verified_at,
        rejected_reason=scan.rejected_reason,
    )


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    mode: PrescriptionScanMode = PrescriptionScanMode.AI


def _band_counts(scan: PrescriptionScan) -> dict[str, str]:
    _, assessment = service.load(scan)
    counts = {"high": 0, "medium": 0, "low": 0, "absent": 0}
    if assessment:
        fields = [f for item in assessment["items"] for f in item.values()]
        fields += list(assessment["header"].values())
        for f in fields:
            counts[f["band"]] += 1
    return {f"fields_{k}": str(v) for k, v in counts.items()}


@router.post(
    "/patients/{patient_id}/prescription-scans",
    response_model=ScanOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_scan(
    patient_id: uuid.UUID, body: ScanIn, request: Request, ctx: PatientRequest = _upload
) -> ScanOut:
    """Start reading an uploaded prescription photo. `mode=manual` opens the same review
    screen empty, to type it in next to the photo (no AI involved)."""
    extractor = extractor_for(request)
    if body.mode == PrescriptionScanMode.AI:
        if extractor is None:
            raise ConflictError("AI reading is not available on this server. Type it in instead.")
        granted = await consent.active_platform_consent(
            ctx.session,
            patient_id=ctx.patient_id,
            purpose=ConsentPurpose.AI_PROCESSING,
            now=datetime.now(UTC),
        )
        if granted is None:
            raise ForbiddenError(
                "AI reading needs the patient's permission first. You can type it in instead."
            )
    scan = await service.create(
        ctx.session,
        patient_id=ctx.patient_id,
        document_id=body.document_id,
        actor=ctx.actor_id,
        mode=body.mode,
    )
    await ctx.audit(
        "prescription_scan.created",
        resource_type="prescription_scan",
        resource_id=scan.id,
        context={"mode": body.mode.value, "document_id": str(body.document_id)},
    )
    await ctx.session.commit()

    if body.mode == PrescriptionScanMode.AI:
        settings = request.app.state.settings
        if settings.ai_jobs_inline:
            await service.mark_running(ctx.session, scan)
            await service.process(
                ctx.session,
                scan,
                storage=request.app.state.storage,
                settings=settings,
                extractor=extractor,
                ocr_engine=ocr_for(request),
            )
            await ctx.audit(
                "prescription_scan.read_by_ai"
                if scan.status == PrescriptionScanStatus.NEEDS_REVIEW
                else "prescription_scan.failed",
                resource_type="prescription_scan",
                resource_id=scan.id,
                context={
                    "provider": scan.provider or "",
                    "model": scan.model or "",
                    "error_code": scan.error_code or "",
                    **_band_counts(scan),
                },
            )
            await ctx.session.commit()
        else:
            from app.workers.tasks import process_prescription_scan

            process_prescription_scan.delay(str(scan.id), str(ctx.patient_id))
    return _scan_out(scan)


@router.get("/patients/{patient_id}/prescription-scans", response_model=list[ScanSummaryOut])
async def list_scans(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[ScanSummaryOut]:
    rows = await service.list_for_patient(ctx.session, ctx.patient_id)
    await ctx.audit("prescription_scan.list", resource_type="prescription_scan")
    await ctx.session.commit()
    return [ScanSummaryOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/patients/{patient_id}/prescription-scans/{scan_id}", response_model=ScanOut)
async def get_scan(
    patient_id: uuid.UUID, scan_id: uuid.UUID, ctx: PatientRequest = _view
) -> ScanOut:
    scan = await service.get(ctx.session, ctx.patient_id, scan_id)
    await ctx.audit(
        "prescription_scan.read", resource_type="prescription_scan", resource_id=scan.id
    )
    await ctx.session.commit()
    return _scan_out(scan)


def _verifier(ctx: PatientRequest) -> VerifierRole:
    """Who may vouch for the content: the patient, a caregiver allowed to report health
    information, or a linked doctor who may edit the chart."""
    if "self" in ctx.access.via:
        return VerifierRole.PATIENT
    if "doctor" in ctx.access.via and ctx.allows(Permission.EDIT_CLINICAL_RECORDS):
        return VerifierRole.DOCTOR
    if "caregiver" in ctx.access.via and ctx.allows(Permission.REPORT_HEALTH_INFO):
        return VerifierRole.CAREGIVER
    raise ForbiddenError("You can view this scan, but not check or save it.")


class ReviewOpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["set_field", "add_item", "remove_item", "restore_item"]
    item_key: str | None = Field(default=None, max_length=10)
    field: str | None = Field(default=None, max_length=40)
    action: Literal["set", "confirm", "not_on_prescription", "reset"] | None = None
    value: str | None = Field(default=None, max_length=300)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ops: list[ReviewOpIn] = Field(min_length=1, max_length=50)


@router.post("/patients/{patient_id}/prescription-scans/{scan_id}/review", response_model=ScanOut)
async def review_scan(
    patient_id: uuid.UUID, scan_id: uuid.UUID, body: ReviewIn, ctx: PatientRequest = _upload
) -> ScanOut:
    role = _verifier(ctx)
    scan, changed = await service.apply_review(
        ctx.session,
        patient_id=ctx.patient_id,
        scan_id=scan_id,
        ops=[service.Op(**o.model_dump()) for o in body.ops],
        actor=ctx.actor_id,
    )
    # Paths and resulting statuses only; values stay in the encrypted review.
    await ctx.audit(
        "prescription_scan.reviewed",
        resource_type="prescription_scan",
        resource_id=scan.id,
        changed_fields=changed,
        context={"role": role.value},
    )
    await ctx.session.commit()
    return _scan_out(scan)


class ConfirmOut(BaseModel):
    scan: ScanOut
    prescription_id: uuid.UUID


@router.post(
    "/patients/{patient_id}/prescription-scans/{scan_id}/confirm", response_model=ConfirmOut
)
async def confirm_scan(
    patient_id: uuid.UUID, scan_id: uuid.UUID, ctx: PatientRequest = _upload
) -> ConfirmOut:
    """Save the reviewed prescription. It is labelled patient-verified (or doctor-verified),
    and its medicines wait for the patient to confirm reminder times."""
    role = _verifier(ctx)
    done = await service.confirm(
        ctx.session,
        patient_id=ctx.patient_id,
        scan_id=scan_id,
        actor=ctx.actor_id,
        role=role,
        today=datetime.now(UTC).date(),
    )
    await medications.create_from_prescription(
        ctx.session,
        patient_id=ctx.patient_id,
        start_date=done.prescription.prescribed_on or date.today(),
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
            for i in done.items
        ],
    )
    await ctx.audit(
        "prescription_scan.verified",
        resource_type="prescription_scan",
        resource_id=done.scan.id,
        changed_fields=["status", "verified_by", "verified_at", "prescription_id"],
        context={
            "role": role.value,
            "items": str(len(done.items)),
            "corrected_fields": str(done.corrected_fields),
        },
    )
    await ctx.audit(
        "prescription.recorded_from_scan",
        resource_type="prescription",
        resource_id=done.prescription.id,
        context={
            "scan_id": str(done.scan.id),
            "verification": done.prescription.verification_status.value,
        },
    )
    await ctx.session.commit()
    return ConfirmOut(scan=_scan_out(done.scan), prescription_id=done.prescription.id)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)


@router.post("/patients/{patient_id}/prescription-scans/{scan_id}/reject", response_model=ScanOut)
async def reject_scan(
    patient_id: uuid.UUID, scan_id: uuid.UUID, body: RejectIn, ctx: PatientRequest = _upload
) -> ScanOut:
    """Discard the reading (for example, the wrong photo). Nothing is saved to the record."""
    role = _verifier(ctx)
    scan = await service.reject(
        ctx.session,
        patient_id=ctx.patient_id,
        scan_id=scan_id,
        actor=ctx.actor_id,
        reason=body.reason,
    )
    await ctx.audit(
        "prescription_scan.rejected",
        resource_type="prescription_scan",
        resource_id=scan.id,
        changed_fields=["status", "rejected_reason"],
        context={"role": role.value},
    )
    await ctx.session.commit()
    return _scan_out(scan)
