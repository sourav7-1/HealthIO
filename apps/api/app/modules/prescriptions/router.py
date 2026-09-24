"""Prescriptions: doctor e-prescriptions with immutable versions.

Every read is audited; so are exports. Writes need CHANGE_DOCTOR_PRESCRIPTION (only a
linked, verified doctor with prescription consent holds it) and the caller must be the
prescriber. Patients and permitted caregivers can read and export, never write.
"""

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MealRelation, VerificationStatus
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.appointments import service as appointments
from app.modules.care_team import service as care_team
from app.modules.medications import doses
from app.modules.medications import service as medications
from app.modules.medications.models import ActorRole, MedicationOrigin
from app.modules.patients import service as patients
from app.modules.prescriptions import service
from app.modules.prescriptions.document import (
    DocumentItem,
    PatientInfo,
    PrescriberInfo,
    PrescriptionDocument,
    VersionRef,
    provenance_note,
)
from app.modules.prescriptions.models import Prescription, PrescriptionSource, PrescriptionStatus
from app.modules.prescriptions.pdf import RENDERERS

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


class DetailsIn(_In):
    items: list[ItemIn] = Field(min_length=1, max_length=30)
    valid_until: date | None = None
    diagnosis_as_written: str | None = Field(default=None, max_length=1000)
    advice: str | None = Field(default=None, max_length=2000)  # notes and advice
    follow_up_on: date | None = None
    follow_up_instructions: str | None = Field(default=None, max_length=500)

    def details(self) -> service.Details:
        return service.Details(
            valid_until=self.valid_until,
            diagnosis_as_written=self.diagnosis_as_written,
            advice=self.advice,
            follow_up_on=self.follow_up_on,
            follow_up_instructions=self.follow_up_instructions,
        )


class PrescriptionIn(DetailsIn):
    visit_id: uuid.UUID | None = None
    prescribed_on: date | None = None


class PrescriptionUpdate(DetailsIn):
    revision_reason: str | None = Field(default=None, max_length=300)


class RevisionIn(DetailsIn):
    reason: str = Field(min_length=5, max_length=300)


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
    follow_up_on: date | None
    follow_up_instructions: str | None
    revision: int
    revision_reason: str | None
    supersedes_prescription_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    content_sha256: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    items: list[ItemOut]


def _to_input(item: ItemIn) -> service.ItemInput:
    return service.ItemInput(**item.model_dump())


async def _successors(
    ctx: PatientRequest, universe: list[service.PrescriptionWithItems] | None
) -> dict[uuid.UUID, uuid.UUID]:
    """superseded id -> id of the version that replaced it (others' drafts excluded)."""
    if universe is None:
        universe = await service.list_for_patient(
            ctx.session, ctx.patient_id, viewer_doctor_id=await _viewer_doctor(ctx)
        )
    return {
        r.prescription.supersedes_prescription_id: r.prescription.id
        for r in universe
        if r.prescription.supersedes_prescription_id is not None
    }


async def _viewer_doctor(ctx: PatientRequest) -> uuid.UUID | None:
    return await care_team.doctor_id_for(ctx.session, ctx.actor_id)


async def _out(
    ctx: PatientRequest,
    rows: list[service.PrescriptionWithItems],
    universe: list[service.PrescriptionWithItems] | None = None,
) -> list[PrescriptionOut]:
    doctors = {
        r.prescription.prescriber_doctor_id for r in rows if r.prescription.prescriber_doctor_id
    }
    names = await care_team.doctor_names(ctx.session, doctors)
    me = await _viewer_doctor(ctx)
    successors = await _successors(ctx, universe)
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
                follow_up_on=p.follow_up_on,
                follow_up_instructions=p.follow_up_instructions,
                revision=p.revision,
                revision_reason=p.revision_reason,
                supersedes_prescription_id=p.supersedes_prescription_id,
                superseded_by_id=successors.get(p.id),
                content_sha256=p.content_sha256,
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
    """All versions, newest first. Superseded versions carry `superseded_by_id`."""
    rows = await service.list_for_patient(
        ctx.session, ctx.patient_id, viewer_doctor_id=await _viewer_doctor(ctx)
    )
    await ctx.audit("prescription.list", resource_type="prescription")
    await ctx.session.commit()
    return await _out(ctx, rows, universe=rows)


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
        details=body.details(),
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
    """Edit a draft. Issued prescriptions are immutable: use /revisions to correct one."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.replace_draft(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        items=[_to_input(i) for i in body.items],
        details=body.details(),
        revision_reason=body.revision_reason,
    )
    await ctx.audit(
        "prescription.update_draft",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=[
            "items",
            "valid_until",
            "diagnosis_as_written",
            "advice",
            "follow_up_on",
            "follow_up_instructions",
        ],
    )
    await ctx.session.commit()
    return (await _out(ctx, [row]))[0]


@router.post(
    "/patients/{patient_id}/prescriptions/{prescription_id}/revisions",
    response_model=PrescriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_revision(
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    body: RevisionIn,
    ctx: PatientRequest = _write,
) -> PrescriptionOut:
    """Correct an issued prescription. Creates a new draft version (the issued one is not
    touched); issuing that draft supersedes the previous version."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.start_revision(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        reason=body.reason,
        items=[_to_input(i) for i in body.items],
        details=body.details(),
    )
    await ctx.audit(
        "prescription.revision_started",
        resource_type="prescription",
        resource_id=row.prescription.id,
        context={"supersedes": str(prescription_id), "revision": str(row.prescription.revision)},
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
    patient's confirmation; no reminder starts until they confirm the schedule. Issuing a
    correction supersedes the previous version and stops the medicines it started."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    result = await service.issue(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
    )
    row = result.row
    rx = row.prescription
    if result.superseded is not None:
        old = result.superseded.prescription
        stopped = await doses.stop_for_prescription_items(
            ctx.session,
            patient_id=ctx.patient_id,
            item_ids=[i.id for i in result.superseded.items],
            actor=ctx.actor_id,
            reason=f"Replaced by corrected prescription (version {rx.revision})",
        )
        await appointments.cancel_for_prescription(
            ctx.session, patient_id=ctx.patient_id, prescription_id=old.id, actor=ctx.actor_id
        )
        await ctx.audit(
            "prescription.superseded",
            resource_type="prescription",
            resource_id=old.id,
            changed_fields=["status"],
            context={"superseded_by": str(rx.id), "medicines_stopped": str(stopped)},
        )
    await medications.create_from_prescription(
        ctx.session,
        patient_id=ctx.patient_id,
        start_date=rx.prescribed_on or date.today(),
        actor=ctx.actor_id,
        origin=MedicationOrigin.DOCTOR_PRESCRIPTION,
        actor_role=ActorRole.DOCTOR,
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
    if rx.follow_up_on is not None:
        await appointments.set_follow_up(
            ctx.session,
            patient_id=ctx.patient_id,
            doctor_id=doctor.id,
            actor=ctx.actor_id,
            due_date=rx.follow_up_on,
            reason=rx.follow_up_instructions,
            source_visit_id=rx.visit_id,
            source_prescription_id=rx.id,
        )
    await ctx.audit(
        "prescription.issue",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=["status", "issued_at", "content_sha256"],
        context={"items": str(len(row.items)), "revision": str(rx.revision)},
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
    """Cancel an issued prescription (kept as history; its medicines stop), or discard an
    unissued draft."""
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    items = (
        await service.get_one(
            ctx.session, ctx.patient_id, prescription_id, viewer_doctor_id=doctor.id
        )
    ).items
    rx = await service.cancel(
        ctx.session,
        patient_id=ctx.patient_id,
        prescription_id=prescription_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        reason=body.reason,
    )
    if rx.status == PrescriptionStatus.CANCELLED:
        await doses.stop_for_prescription_items(
            ctx.session,
            patient_id=ctx.patient_id,
            item_ids=[i.id for i in items],
            actor=ctx.actor_id,
            reason=f"Prescription cancelled by the doctor: {body.reason}",
        )
        await appointments.cancel_for_prescription(
            ctx.session, patient_id=ctx.patient_id, prescription_id=rx.id, actor=ctx.actor_id
        )
    await ctx.audit(
        "prescription.cancel"
        if rx.status == PrescriptionStatus.CANCELLED
        else "prescription.discard_draft",
        resource_type="prescription",
        resource_id=prescription_id,
        changed_fields=["status", "cancelled_at", "cancel_reason"],
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the prescription document (web view and exports) -----------------------------------------


class PrescriberOut(BaseModel):
    name: str
    qualifications: list[str]
    specialty: str | None
    registration_council: str | None
    registration_number: str | None
    practice_name: str | None
    practice_address: str | None


class PatientInfoOut(BaseModel):
    name: str
    age_years: int | None
    sex: str | None


class DocumentItemOut(BaseModel):
    sequence: int
    medicine: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    route: str | None
    dose: str | None
    frequency: str | None
    meal_relation: str | None
    duration_days: int | None
    is_prn: bool
    prn_reason: str | None
    instructions: str | None


class VersionOut(BaseModel):
    id: uuid.UUID
    revision: int
    status: str
    issued_at: datetime | None
    revision_reason: str | None


class PrescriptionDocumentOut(BaseModel):
    id: uuid.UUID
    revision: int
    status: str
    prescribed_on: date | None
    issued_at: datetime | None
    valid_until: date | None
    diagnosis_as_written: str | None
    advice: str | None
    follow_up_on: date | None
    follow_up_instructions: str | None
    revision_reason: str | None
    supersedes_prescription_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    content_sha256: str | None
    prescriber: PrescriberOut | None
    patient: PatientInfoOut | None
    items: list[DocumentItemOut]
    versions: list[VersionOut]
    source: str
    verification_status: str
    provenance: str | None


def _dose_text(amount: Decimal | None, unit: str | None) -> str | None:
    if amount is None:
        return unit
    number = format(amount.normalize(), "f")
    return f"{number} {unit}".strip() if unit else number


async def _build_document(
    ctx: PatientRequest, row: service.PrescriptionWithItems
) -> PrescriptionDocument:
    rx: Prescription = row.prescription
    viewer = await _viewer_doctor(ctx)
    prescriber = None
    if rx.prescriber_doctor_id is not None:
        d = await care_team.doctor_profile(ctx.session, rx.prescriber_doctor_id)
        if d is not None:
            prescriber = PrescriberInfo(
                name=d.display_name,
                qualifications=list(d.qualifications or []),
                specialty=d.primary_specialty,
                registration_council=d.registration_council,
                registration_number=d.registration_number,
                practice_name=d.practice_name,
                practice_address=d.practice_address,
            )
    elif rx.external_prescriber_name:
        prescriber = PrescriberInfo(
            name=rx.external_prescriber_name,
            qualifications=[],
            specialty=None,
            registration_council=None,
            registration_number=rx.external_prescriber_registration,
            practice_name=rx.external_facility_name,
            practice_address=None,
        )
    patient = None
    if ctx.allows(Permission.VIEW_PROFILE):
        profile = await patients.get_live_profile(ctx.session, ctx.patient_id)
        if profile is not None:
            dob = profile.date_of_birth
            today = datetime.now(UTC).date()
            patient = PatientInfo(
                name=" ".join(x for x in (profile.given_name, profile.family_name) if x),
                age_years=None
                if dob is None
                else today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)),
                sex=profile.sex_at_birth.value if profile.sex_at_birth else None,
            )
    chain = await service.version_chain(ctx.session, ctx.patient_id, rx.id, viewer_doctor_id=viewer)
    successor = next((v.id for v in chain if v.supersedes_prescription_id == rx.id), None)
    return PrescriptionDocument(
        id=rx.id,
        revision=rx.revision,
        status=rx.status.value,
        prescribed_on=rx.prescribed_on,
        issued_at=rx.issued_at,
        valid_until=rx.valid_until,
        diagnosis_as_written=rx.diagnosis_as_written,
        advice=rx.advice,
        follow_up_on=rx.follow_up_on,
        follow_up_instructions=rx.follow_up_instructions,
        revision_reason=rx.revision_reason,
        supersedes_prescription_id=rx.supersedes_prescription_id,
        superseded_by_id=successor,
        cancelled_at=rx.cancelled_at,
        cancel_reason=rx.cancel_reason,
        content_sha256=rx.content_sha256,
        prescriber=prescriber,
        patient=patient,
        items=[
            DocumentItem(
                sequence=i.sequence,
                medicine=i.drug_name,
                generic_name=i.generic_name,
                strength=i.strength,
                dosage_form=i.dosage_form,
                route=i.route,
                dose=_dose_text(i.dose_amount, i.dose_unit),
                frequency=i.frequency_text,
                meal_relation=i.meal_relation.value if i.meal_relation else None,
                duration_days=i.duration_days,
                is_prn=i.is_prn,
                prn_reason=i.prn_reason,
                instructions=i.instructions,
            )
            for i in row.items
        ],
        versions=[
            VersionRef(
                id=v.id,
                revision=v.revision,
                status=v.status.value,
                issued_at=v.issued_at,
                revision_reason=v.revision_reason,
            )
            for v in chain
        ],
        source=rx.source.value,
        verification_status=rx.verification_status.value,
    )


@router.get(
    "/patients/{patient_id}/prescriptions/{prescription_id}",
    response_model=PrescriptionDocumentOut,
)
async def get_prescription_document(
    patient_id: uuid.UUID, prescription_id: uuid.UUID, ctx: PatientRequest = _view
) -> PrescriptionDocumentOut:
    """The prescription as a document: prescriber and patient details, items as written,
    and every version of it (for the history view)."""
    row = await service.get_one(
        ctx.session, ctx.patient_id, prescription_id, viewer_doctor_id=await _viewer_doctor(ctx)
    )
    document = await _build_document(ctx, row)
    await ctx.audit("prescription.read", resource_type="prescription", resource_id=prescription_id)
    await ctx.session.commit()
    out = PrescriptionDocumentOut.model_validate(
        {**asdict(document), "provenance": provenance_note(document)}
    )
    return out


@router.get(
    "/patients/{patient_id}/prescriptions/{prescription_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export_prescription_pdf(
    patient_id: uuid.UUID, prescription_id: uuid.UUID, ctx: PatientRequest = _view
) -> Response:
    """Download the prescription as PDF. Non-current versions are clearly marked."""
    row = await service.get_one(
        ctx.session, ctx.patient_id, prescription_id, viewer_doctor_id=await _viewer_doctor(ctx)
    )
    document = await _build_document(ctx, row)
    renderer = RENDERERS["pdf"]
    content = renderer.render(document)
    await ctx.audit(
        "prescription.export",
        resource_type="prescription",
        resource_id=prescription_id,
        context={"format": "pdf", "revision": str(document.revision), "status": document.status},
    )
    await ctx.session.commit()
    stamp = document.prescribed_on.isoformat() if document.prescribed_on else "undated"
    filename = f"prescription-{stamp}-v{document.revision}.{renderer.file_extension}"
    return Response(
        content=content,
        media_type=renderer.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
