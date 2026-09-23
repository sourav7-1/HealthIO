import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ForbiddenError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.permissions import Permission
from app.modules.care_team import service as care_team
from app.modules.labs import service
from app.modules.labs.models import ResultFlag, TestOrderStatus, TestPriority, TestReportStatus
from app.modules.records import service as records

router = APIRouter(tags=["tests and reports"])

_view = patient_request(Permission.VIEW_REPORTS)
_order = patient_request(Permission.EDIT_CLINICAL_RECORDS)
_upload = patient_request(Permission.UPLOAD_REPORTS)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrderIn(_In):
    tests: list[str] = Field(min_length=1, max_length=40)
    priority: TestPriority = TestPriority.ROUTINE
    clinical_indication: str | None = Field(default=None, max_length=1000)
    due_by: date | None = None
    visit_id: uuid.UUID | None = None


class CancelIn(_In):
    reason: str = Field(min_length=3, max_length=300)


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


class ResultIn(_In):
    analyte_name: str = Field(min_length=1, max_length=200)
    value_numeric: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    value_text: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=32)
    reference_low: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    reference_high: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    reference_text: str | None = Field(default=None, max_length=200)
    flag: ResultFlag = Field(default=ResultFlag.UNKNOWN, description="As printed on the report")


class ReportIn(_In):
    document_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    lab_name: str | None = Field(default=None, max_length=200)
    collected_at: datetime | None = None
    reported_at: datetime | None = None
    conclusion: str | None = Field(
        default=None, max_length=2000, description="As written on the report"
    )
    results: list[ResultIn] = Field(default_factory=list, max_length=200)


class ResultOut(ResultIn):
    model_config = ConfigDict(extra="ignore")
    id: uuid.UUID


class ReportOut(BaseModel):
    id: uuid.UUID
    status: TestReportStatus
    source: str
    lab_name: str | None
    collected_at: datetime | None
    reported_at: datetime | None
    conclusion: str | None
    document_id: uuid.UUID | None
    order_id: uuid.UUID | None
    verified_at: datetime | None
    created_at: datetime
    results: list[ResultOut]


async def _orders_out(ctx: PatientRequest, rows: list[service.OrderWithItems]) -> list[OrderOut]:
    names = await care_team.doctor_names(ctx.session, {r.order.ordering_doctor_id for r in rows})
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
        )
        for r in rows
    ]


def _report_out(r: service.ReportWithResults) -> ReportOut:
    rep = r.report
    return ReportOut(
        id=rep.id,
        status=rep.status,
        source=rep.source.value,
        lab_name=rep.lab_name,
        collected_at=rep.collected_at,
        reported_at=rep.reported_at,
        conclusion=rep.conclusion,
        document_id=rep.document_id,
        order_id=rep.order_id,
        verified_at=rep.verified_at,
        created_at=rep.created_at,
        results=[ResultOut.model_validate(x, from_attributes=True) for x in r.results],
    )


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
    rows = [
        r for r in await service.list_orders(ctx.session, ctx.patient_id) if r.order.id == order_id
    ]
    return (await _orders_out(ctx, rows))[0]


@router.get("/patients/{patient_id}/reports", response_model=list[ReportOut])
async def list_reports(patient_id: uuid.UUID, ctx: PatientRequest = _view) -> list[ReportOut]:
    rows = await service.list_reports(ctx.session, ctx.patient_id)
    await ctx.audit("test_report.list", resource_type="test_report")
    await ctx.session.commit()
    return [_report_out(r) for r in rows]


@router.post(
    "/patients/{patient_id}/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED
)
async def add_report(
    patient_id: uuid.UUID, body: ReportIn, ctx: PatientRequest = _upload
) -> ReportOut:
    """A doctor records a report (file and/or values as printed); it is verified by them.
    Patient and caregiver uploads go through a separate review flow (Phase 12)."""
    if "doctor" not in ctx.access.via:
        raise ForbiddenError("Only the treating doctor can record a verified report here.")
    await care_team.require_verified_doctor(ctx.session, ctx.actor_id)
    if body.document_id is not None:
        await records.usable_document(ctx.session, ctx.patient_id, body.document_id)
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
    )
    await ctx.audit("test_report.create", resource_type="test_report", resource_id=row.report.id)
    await ctx.session.commit()
    return _report_out(row)
