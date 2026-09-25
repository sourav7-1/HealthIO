"""Tests and reports.

Doctors order tests (with the reason), move orders through their statuses, record or
attach reports, and review reports patients upload. Patients see their orders, upload
report files (PDF, JPEG or PNG), open reports, and share one report with one of their
doctors. Report files go through the documents API (type, size and malware checks);
every read and change is audited. Nothing here interprets a result.
"""

import contextlib
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.labs import service
from app.modules.labs.models import (
    ReportShare,
    ResultFlag,
    TestOrderStatus,
    TestPriority,
    TestReportStatus,
)
from app.modules.records import service as records
from app.modules.records.models import HealthDocument, ScanStatus

router = APIRouter(tags=["tests and reports"])

_view = patient_request(Permission.VIEW_REPORTS)
_order = patient_request(Permission.EDIT_CLINICAL_RECORDS)
_upload = patient_request(Permission.UPLOAD_REPORTS)
_share = patient_request(Permission.MANAGE_CONSENT)
# Report reads: full access with VIEW_REPORTS, or a single report shared with the doctor.
_report_read = patient_request()


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrderIn(_In):
    tests: list[str] = Field(min_length=1, max_length=40)
    priority: TestPriority = TestPriority.ROUTINE
    clinical_indication: str | None = Field(
        default=None, max_length=1000, description="Reason for the test, as the doctor writes it"
    )
    due_by: date | None = None
    visit_id: uuid.UUID | None = None


class CancelIn(_In):
    reason: str = Field(min_length=3, max_length=300)


class OrderStatusIn(_In):
    status: Literal["sample_collected", "partially_resulted", "completed", "entered_in_error"]
    note: str | None = Field(default=None, max_length=300)


class OrderOut(BaseModel):
    id: uuid.UUID
    status: TestOrderStatus
    priority: TestPriority
    clinical_indication: str | None
    ordered_at: datetime
    due_by: date | None
    ordering_doctor_id: uuid.UUID
    ordering_doctor_name: str | None
    visit_id: uuid.UUID | None
    tests: list[str]
    cancel_reason: str | None
    report_ids: list[uuid.UUID]
    next_statuses: list[TestOrderStatus]


class ResultIn(_In):
    analyte_name: str = Field(min_length=1, max_length=200)
    value_numeric: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    value_text: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=32)
    reference_low: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    reference_high: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    reference_text: str | None = Field(default=None, max_length=200)
    flag: ResultFlag = Field(default=ResultFlag.UNKNOWN, description="As printed on the report")


class ReportMetaIn(_In):
    test_name: str | None = Field(default=None, max_length=200)
    report_date: date | None = None
    lab_reference: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ReportIn(ReportMetaIn):
    document_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    lab_name: str | None = Field(default=None, max_length=200)
    collected_at: datetime | None = None
    reported_at: datetime | None = None
    conclusion: str | None = Field(
        default=None, max_length=2000, description="As written on the report"
    )
    results: list[ResultIn] = Field(default_factory=list, max_length=200)


class UploadedReportIn(ReportMetaIn):
    document_id: uuid.UUID
    order_id: uuid.UUID | None = None
    lab_name: str | None = Field(default=None, max_length=200)


class ReportReviewIn(_In):
    decision: Literal["verify", "reject"]
    note: str | None = Field(default=None, max_length=300)


class InErrorIn(_In):
    reason: str = Field(min_length=3, max_length=300)


class ShareIn(_In):
    doctor_id: uuid.UUID
    expires_at: datetime | None = None


class ResultOut(ResultIn):
    model_config = ConfigDict(extra="ignore")
    id: uuid.UUID


class ReportFileOut(BaseModel):
    id: uuid.UUID
    content_type: str
    size_bytes: int | None
    scan_status: ScanStatus
    available: bool


class ShareOut(BaseModel):
    id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str | None
    expires_at: datetime | None
    shared_at: datetime


class ShareTargetOut(BaseModel):
    doctor_id: uuid.UUID
    name: str
    specialty: str | None


class ReportOut(BaseModel):
    id: uuid.UUID
    status: TestReportStatus
    source: str
    test_name: str | None
    report_date: date | None
    lab_name: str | None
    lab_reference: str | None
    notes: str | None
    collected_at: datetime | None
    reported_at: datetime | None
    conclusion: str | None
    document_id: uuid.UUID | None
    file: ReportFileOut | None
    order_id: uuid.UUID | None
    ordering_doctor_name: str | None
    verified_at: datetime | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    results: list[ResultOut]
    # "full": through consent/permissions; "shared": this one report was shared with you.
    access: Literal["full", "shared"]


class ReportDetailOut(ReportOut):
    shares: list[ShareOut] | None  # only for whoever manages the patient's sharing
    share_targets: list[ShareTargetOut] | None


# --- helpers ----------------------------------------------------------------------------


async def _orders_out(ctx: PatientRequest, rows: list[service.OrderWithItems]) -> list[OrderOut]:
    names = await care_team.doctor_names(ctx.session, {r.order.ordering_doctor_id for r in rows})
    reports: dict[uuid.UUID, list[uuid.UUID]] = {}
    for rep in await service.list_reports(ctx.session, ctx.patient_id):
        if rep.report.order_id and rep.report.status != TestReportStatus.ENTERED_IN_ERROR:
            reports.setdefault(rep.report.order_id, []).append(rep.report.id)
    return [
        OrderOut(
            id=r.order.id,
            status=r.order.status,
            priority=r.order.priority,
            clinical_indication=r.order.clinical_indication,
            ordered_at=r.order.ordered_at,
            due_by=r.order.due_by,
            ordering_doctor_id=r.order.ordering_doctor_id,
            ordering_doctor_name=names.get(r.order.ordering_doctor_id),
            visit_id=r.order.visit_id,
            tests=[i.test_name for i in r.items],
            cancel_reason=r.order.cancel_reason,
            report_ids=reports.get(r.order.id, []),
            next_statuses=sorted(service.ORDER_TRANSITIONS.get(r.order.status, set())),
        )
        for r in rows
    ]


async def _reports_out(
    ctx: PatientRequest, rows: list[service.ReportWithResults], shared: set[uuid.UUID]
) -> list[ReportOut]:
    order_ids = {r.report.order_id for r in rows if r.report.order_id}
    orders = {
        o.order.id: o.order.ordering_doctor_id
        for o in await service.list_orders(ctx.session, ctx.patient_id)
        if o.order.id in order_ids
    }
    names = await care_team.doctor_names(ctx.session, set(orders.values()))
    docs: dict[uuid.UUID, HealthDocument] = {}
    for r in rows:
        if r.report.document_id:
            with contextlib.suppress(NotFoundError):
                docs[r.report.document_id] = await records.get_document(
                    ctx.session, ctx.patient_id, r.report.document_id
                )
    full = ctx.allows(Permission.VIEW_REPORTS)
    out: list[ReportOut] = []
    for r in rows:
        rep = r.report
        doc = docs.get(rep.document_id) if rep.document_id else None
        out.append(
            ReportOut(
                id=rep.id,
                status=rep.status,
                source=rep.source.value,
                test_name=rep.test_name,
                report_date=rep.report_date,
                lab_name=rep.lab_name,
                lab_reference=rep.lab_reference,
                notes=rep.notes,
                collected_at=rep.collected_at,
                reported_at=rep.reported_at,
                conclusion=rep.conclusion,
                document_id=rep.document_id,
                file=ReportFileOut(
                    id=doc.id,
                    content_type=doc.content_type,
                    size_bytes=doc.size_bytes,
                    scan_status=doc.scan_status,
                    available=doc.scan_status == ScanStatus.CLEAN,
                )
                if doc
                else None,
                order_id=rep.order_id,
                ordering_doctor_name=names.get(orders[rep.order_id])
                if rep.order_id in orders
                else None,
                verified_at=rep.verified_at,
                reviewed_at=rep.reviewed_at,
                review_note=rep.review_note,
                created_at=rep.created_at,
                results=[ResultOut.model_validate(x, from_attributes=True) for x in r.results],
                access="full" if full else "shared",
            )
        )
    return out


async def _readable(ctx: PatientRequest) -> set[uuid.UUID] | None:
    """None: every report (VIEW_REPORTS). Otherwise the ids shared with this doctor."""
    if ctx.allows(Permission.VIEW_REPORTS):
        return None
    if "doctor" not in ctx.access.via:
        raise ForbiddenError("You do not have permission to do this for this patient.")
    doctor_id = await care_team.doctor_id_for(ctx.session, ctx.actor_id)
    return await service.shared_report_ids(ctx.session, ctx.patient_id, doctor_id)


async def _shares_out(ctx: PatientRequest, shares: list[ReportShare]) -> list[ShareOut]:
    names = await care_team.doctor_names(ctx.session, {s.doctor_id for s in shares})
    return [
        ShareOut(
            id=s.id,
            doctor_id=s.doctor_id,
            doctor_name=names.get(s.doctor_id),
            expires_at=s.expires_at,
            shared_at=s.created_at,
        )
        for s in shares
    ]


async def _detail(ctx: PatientRequest, report_id: uuid.UUID) -> ReportDetailOut:
    readable = await _readable(ctx)
    if readable is not None and report_id not in readable:
        raise NotFoundError()
    row = await service.get_report(ctx.session, ctx.patient_id, report_id)
    (base,) = await _reports_out(ctx, [row], readable or set())
    shares: list[ShareOut] | None = None
    targets: list[ShareTargetOut] | None = None
    if ctx.allows(Permission.MANAGE_CONSENT):
        shares = await _shares_out(
            ctx, await service.list_shares(ctx.session, ctx.patient_id, report_id)
        )
        targets = [
            ShareTargetOut(doctor_id=d.id, name=d.display_name, specialty=d.primary_specialty)
            for d in await care_team.active_doctors(ctx.session, ctx.patient_id)
        ]
    return ReportDetailOut(**base.model_dump(), shares=shares, share_targets=targets)


async def _require_doctor(ctx: PatientRequest) -> None:
    if "doctor" not in ctx.access.via:
        raise ForbiddenError("Only a doctor caring for this patient can do this.")
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)


# --- orders -----------------------------------------------------------------------------


@router.get("/patients/{patient_id}/test-orders", response_model=list[OrderOut])
async def list_orders(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[OrderOut]:
    rows = await service.list_orders(ctx.session, ctx.patient_id)
    await ctx.audit("test_order.list", resource_type="test_order")
    await ctx.session.commit()
    return await _orders_out(ctx, rows)


@router.post(
    "/patients/{patient_id}/test-orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def order_tests(
    patient_id: uuid.UUID, body: OrderIn, ctx: PatientRequest = _order
) -> OrderOut:
    doctor = await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.create_order(
        ctx.session,
        patient_id=ctx.patient_id,
        doctor_id=doctor.id,
        actor=ctx.actor_id,
        test_names=body.tests,
        priority=body.priority,
        clinical_indication=body.clinical_indication,
        due_by=body.due_by,
        visit_id=body.visit_id,
    )
    await ctx.audit("test_order.create", resource_type="test_order", resource_id=row.order.id)
    await ctx.session.commit()
    return (await _orders_out(ctx, [row]))[0]


async def _one_order(ctx: PatientRequest, order_id: uuid.UUID) -> OrderOut:
    rows = [
        r for r in await service.list_orders(ctx.session, ctx.patient_id) if r.order.id == order_id
    ]
    return (await _orders_out(ctx, rows))[0]


@router.post("/patients/{patient_id}/test-orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    patient_id: uuid.UUID, order_id: uuid.UUID, body: CancelIn, ctx: PatientRequest = _order
) -> OrderOut:
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    await service.cancel_order(
        ctx.session,
        patient_id=ctx.patient_id,
        order_id=order_id,
        actor=ctx.actor_id,
        reason=body.reason,
    )
    await ctx.audit(
        "test_order.cancel",
        resource_type="test_order",
        resource_id=order_id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return await _one_order(ctx, order_id)


@router.post("/patients/{patient_id}/test-orders/{order_id}/status", response_model=OrderOut)
async def set_order_status(
    patient_id: uuid.UUID, order_id: uuid.UUID, body: OrderStatusIn, ctx: PatientRequest = _order
) -> OrderOut:
    """Mark sample collected, partly resulted, completed, or entered in error (with a
    note). Each change is kept in the order's history."""
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    order = await service.set_order_status(
        ctx.session,
        patient_id=ctx.patient_id,
        order_id=order_id,
        actor=ctx.actor_id,
        status=TestOrderStatus(body.status),
        note=body.note,
    )
    await ctx.audit(
        "test_order.status",
        resource_type="test_order",
        resource_id=order.id,
        changed_fields=["status"],
        context={"status": order.status.value},
    )
    await ctx.session.commit()
    return await _one_order(ctx, order_id)


# --- reports ----------------------------------------------------------------------------


@router.get("/patients/{patient_id}/reports", response_model=list[ReportOut])
async def list_reports(
    patient_id: uuid.UUID, ctx: PatientRequest = _report_read
) -> list[ReportOut]:
    """All reports with tests-and-reports access; otherwise only reports the patient
    shared with you."""
    readable = await _readable(ctx)
    rows = [
        r
        for r in await service.list_reports(ctx.session, ctx.patient_id)
        if readable is None or r.report.id in readable
    ]
    await ctx.audit(
        "test_report.list",
        resource_type="test_report",
        context={"access": "full" if readable is None else "shared"},
    )
    await ctx.session.commit()
    return await _reports_out(ctx, rows, readable or set())


@router.get("/patients/{patient_id}/reports/{report_id}", response_model=ReportDetailOut)
async def get_report(
    patient_id: uuid.UUID, report_id: uuid.UUID, ctx: PatientRequest = _report_read
) -> ReportDetailOut:
    out = await _detail(ctx, report_id)
    await ctx.audit(
        "test_report.view",
        resource_type="test_report",
        resource_id=report_id,
        context={"access": out.access},
    )
    await ctx.session.commit()
    return out


class FileLinkOut(BaseModel):
    url: str
    expires_in: int


@router.get("/patients/{patient_id}/reports/{report_id}/file", response_model=FileLinkOut)
async def report_file(
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    request: Request,
    ctx: PatientRequest = _report_read,
) -> FileLinkOut:
    """A 60-second link to the report file; works for a report shared with you."""
    readable = await _readable(ctx)
    if readable is not None and report_id not in readable:
        raise NotFoundError()
    report = (await service.get_report(ctx.session, ctx.patient_id, report_id)).report
    if report.document_id is None:
        raise NotFoundError("This report has no file.")
    doc = await records.usable_document(ctx.session, ctx.patient_id, report.document_id)
    url = records.download_url(request.app.state.storage, doc)
    await ctx.audit(
        "test_report.file_download",
        resource_type="test_report",
        resource_id=report_id,
        context={"document_id": str(doc.id), "access": "full" if readable is None else "shared"},
    )
    await ctx.session.commit()
    return FileLinkOut(url=url, expires_in=60)


async def _attachable(ctx: PatientRequest, document_id: uuid.UUID) -> None:
    doc = await records.get_document(ctx.session, ctx.patient_id, document_id)
    if doc.document_type not in records.REPORT_DOCUMENTS:
        raise ValidationFailedError("Attach a file uploaded as a lab or imaging report.")
    if doc.scan_status not in (ScanStatus.CLEAN, ScanStatus.PENDING_SCAN):
        raise ValidationFailedError("That file can't be used. Upload it again.")


@router.post(
    "/patients/{patient_id}/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED
)
async def add_report(
    patient_id: uuid.UUID, body: ReportIn, ctx: PatientRequest = _upload
) -> ReportOut:
    """A doctor records a report (a file they uploaded or referenced, and/or values as
    printed); it is verified by them. Patients and caregivers use /reports/uploaded."""
    await _require_doctor(ctx)
    if body.document_id is not None:
        await _attachable(ctx, body.document_id)
    row = await service.record_doctor_report(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        document_id=body.document_id,
        order_id=body.order_id,
        lab_name=body.lab_name,
        collected_at=body.collected_at,
        reported_at=body.reported_at,
        conclusion=body.conclusion,
        results=[service.ResultInput(**r.model_dump()) for r in body.results],
        meta=service.ReportMeta(
            test_name=body.test_name,
            report_date=body.report_date,
            lab_reference=body.lab_reference,
            notes=body.notes,
        ),
    )
    await ctx.audit("test_report.create", resource_type="test_report", resource_id=row.report.id)
    await ctx.session.commit()
    return (await _reports_out(ctx, [row], set()))[0]


@router.post(
    "/patients/{patient_id}/reports/uploaded",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_uploaded_report(
    patient_id: uuid.UUID, body: UploadedReportIn, ctx: PatientRequest = _upload
) -> ReportOut:
    """The patient or a caregiver adds a report file they uploaded. It is labelled as
    uploaded by them and waits for a doctor's review."""
    if "doctor" in ctx.access.via:
        raise ForbiddenError("Doctors record reports with POST /reports.")
    await _attachable(ctx, body.document_id)
    row = await service.record_uploaded_report(
        ctx.session,
        patient_id=ctx.patient_id,
        actor=ctx.actor_id,
        source=ctx.reporter_source,
        document_id=body.document_id,
        order_id=body.order_id,
        lab_name=body.lab_name,
        meta=service.ReportMeta(
            test_name=body.test_name,
            report_date=body.report_date,
            lab_reference=body.lab_reference,
            notes=body.notes,
        ),
    )
    await ctx.audit("test_report.upload", resource_type="test_report", resource_id=row.report.id)
    await ctx.session.commit()
    return (await _reports_out(ctx, [row], set()))[0]


@router.post("/patients/{patient_id}/reports/{report_id}/review", response_model=ReportOut)
async def review_report(
    patient_id: uuid.UUID, report_id: uuid.UUID, body: ReportReviewIn, ctx: PatientRequest = _upload
) -> ReportOut:
    """A doctor confirms an uploaded report belongs to this patient and its details match
    the file, or rejects it with a reason. This is not an interpretation of the result."""
    await _require_doctor(ctx)
    row = await service.review_report(
        ctx.session,
        patient_id=ctx.patient_id,
        report_id=report_id,
        actor=ctx.actor_id,
        verify=body.decision == "verify",
        note=body.note,
    )
    await ctx.audit(
        "test_report.review",
        resource_type="test_report",
        resource_id=report_id,
        changed_fields=["status"],
        context={"decision": body.decision},
    )
    await ctx.session.commit()
    return (await _reports_out(ctx, [row], set()))[0]


@router.post(
    "/patients/{patient_id}/reports/{report_id}/entered-in-error", response_model=ReportOut
)
async def report_in_error(
    patient_id: uuid.UUID, report_id: uuid.UUID, body: InErrorIn, ctx: PatientRequest = _upload
) -> ReportOut:
    """Withdraw a report added by mistake. It stays in the record, marked with the reason."""
    is_doctor = "doctor" in ctx.access.via
    if is_doctor:
        await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    row = await service.mark_report_in_error(
        ctx.session,
        patient_id=ctx.patient_id,
        report_id=report_id,
        actor=ctx.actor_id,
        actor_is_doctor=is_doctor,
        reason=body.reason,
    )
    await ctx.audit(
        "test_report.entered_in_error",
        resource_type="test_report",
        resource_id=report_id,
        changed_fields=["status"],
    )
    await ctx.session.commit()
    return (await _reports_out(ctx, [row], set()))[0]


# --- sharing ----------------------------------------------------------------------------


@router.post(
    "/patients/{patient_id}/reports/{report_id}/shares",
    response_model=ShareOut,
    status_code=status.HTTP_201_CREATED,
)
async def share_report(
    patient_id: uuid.UUID, report_id: uuid.UUID, body: ShareIn, ctx: PatientRequest = _share
) -> ShareOut:
    """Share this one report with a doctor on the patient's care team, optionally until
    a date. The doctor sees only this report, not other tests and reports."""
    doctors = await care_team.active_doctors(ctx.session, ctx.patient_id)
    share = await service.share_report(
        ctx.session,
        patient_id=ctx.patient_id,
        report_id=report_id,
        doctor_id=body.doctor_id,
        linked_doctor_ids={d.id for d in doctors},
        expires_at=body.expires_at,
        actor=ctx.actor_id,
    )
    await ctx.audit(
        "test_report.share",
        resource_type="report_share",
        resource_id=share.id,
        context={"report_id": str(report_id), "doctor_id": str(body.doctor_id)},
    )
    await ctx.session.commit()
    return (await _shares_out(ctx, [share]))[0]


@router.delete(
    "/patients/{patient_id}/report-shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_share(
    patient_id: uuid.UUID, share_id: uuid.UUID, ctx: PatientRequest = _share
) -> Response:
    share = await service.revoke_share(
        ctx.session, patient_id=ctx.patient_id, share_id=share_id, actor=ctx.actor_id
    )
    await ctx.audit(
        "test_report.share_revoked",
        resource_type="report_share",
        resource_id=share.id,
        context={"report_id": str(share.report_id)},
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
