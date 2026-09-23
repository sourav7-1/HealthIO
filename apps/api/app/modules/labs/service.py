"""Labs service: test orders and reports.

Tests are ordered by name (and optionally a catalogue entry). Reports uploaded by a
doctor are recorded as verified by that doctor; values are entered exactly as printed
on the report. Results are never interpreted or flagged automatically.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordSource
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.modules.labs.models import (
    ResultFlag,
    TestOrder,
    TestOrderItem,
    TestOrderStatus,
    TestPriority,
    TestReport,
    TestReportStatus,
    TestResult,
)


@dataclass(frozen=True)
class OrderWithItems:
    order: TestOrder
    items: list[TestOrderItem]


async def list_orders(session: AsyncSession, patient_id: uuid.UUID) -> list[OrderWithItems]:
    orders = list(
        (
            await session.scalars(
                select(TestOrder)
                .where(TestOrder.patient_id == patient_id)
                .order_by(TestOrder.ordered_at.desc())
            )
        ).all()
    )
    if not orders:
        return []
    items = (
        await session.scalars(
            select(TestOrderItem)
            .where(TestOrderItem.order_id.in_([o.id for o in orders]))
            .order_by(TestOrderItem.test_name)
        )
    ).all()
    grouped: dict[uuid.UUID, list[TestOrderItem]] = {o.id: [] for o in orders}
    for item in items:
        grouped[item.order_id].append(item)
    return [OrderWithItems(o, grouped[o.id]) for o in orders]


async def create_order(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    test_names: list[str],
    priority: TestPriority,
    clinical_indication: str | None,
    due_by: date | None,
    visit_id: uuid.UUID | None,
) -> OrderWithItems:
    names = sorted({n.strip() for n in test_names if n.strip()}, key=str.lower)
    if not names:
        raise ValidationFailedError("Add at least one test.")
    if len(names) > 40:
        raise ValidationFailedError("An order can have at most 40 tests.")
    order = TestOrder(
        patient_id=patient_id,
        ordering_doctor_id=doctor_id,
        visit_id=visit_id,
        priority=priority,
        clinical_indication=clinical_indication,
        due_by=due_by,
        ordered_at=datetime.now(UTC),
        created_by=actor,
        updated_by=actor,
    )
    session.add(order)
    await session.flush()
    items = [
        TestOrderItem(
            patient_id=patient_id,
            order_id=order.id,
            test_name=name,
            created_by=actor,
            updated_by=actor,
        )
        for name in names
    ]
    session.add_all(items)
    await session.flush()
    return OrderWithItems(order, items)


async def cancel_order(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    order_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str,
) -> TestOrder:
    order: TestOrder | None = await session.scalar(
        select(TestOrder)
        .where(TestOrder.id == order_id, TestOrder.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError()
    if order.status not in (TestOrderStatus.ORDERED, TestOrderStatus.SAMPLE_COLLECTED):
        raise ConflictError("Only an open order can be cancelled.")
    order.status = TestOrderStatus.CANCELLED
    order.cancelled_at = datetime.now(UTC)
    order.cancelled_by = actor
    order.cancel_reason = reason
    order.updated_by = actor
    await session.flush()
    return order


@dataclass(frozen=True)
class ResultInput:
    analyte_name: str
    value_numeric: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    reference_low: Decimal | None = None
    reference_high: Decimal | None = None
    reference_text: str | None = None
    flag: ResultFlag = ResultFlag.UNKNOWN  # as printed on the report, never computed


@dataclass(frozen=True)
class ReportWithResults:
    report: TestReport
    results: list[TestResult]


async def list_reports(session: AsyncSession, patient_id: uuid.UUID) -> list[ReportWithResults]:
    reports = list(
        (
            await session.scalars(
                select(TestReport)
                .where(TestReport.patient_id == patient_id)
                .order_by(TestReport.collected_at.desc().nulls_last(), TestReport.created_at.desc())
            )
        ).all()
    )
    if not reports:
        return []
    results = (
        await session.scalars(
            select(TestResult)
            .where(TestResult.report_id.in_([r.id for r in reports]))
            .order_by(TestResult.sequence)
        )
    ).all()
    grouped: dict[uuid.UUID, list[TestResult]] = {r.id: [] for r in reports}
    for res in results:
        grouped[res.report_id].append(res)
    return [ReportWithResults(r, grouped[r.id]) for r in reports]


async def record_doctor_report(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    document_id: uuid.UUID | None,
    order_id: uuid.UUID | None,
    lab_name: str | None,
    collected_at: datetime | None,
    reported_at: datetime | None,
    conclusion: str | None,
    results: list[ResultInput],
) -> ReportWithResults:
    if document_id is None and not results:
        raise ValidationFailedError("Attach the report file or enter at least one result.")
    for r in results:
        if not r.analyte_name.strip() or (r.value_numeric is None and not r.value_text):
            raise ValidationFailedError("Each result needs a test name and a value.")
    if order_id is not None:
        exists = await session.scalar(
            select(TestOrder.id).where(TestOrder.id == order_id, TestOrder.patient_id == patient_id)
        )
        if exists is None:
            raise NotFoundError("Order not found for this patient.")
    report = TestReport(
        patient_id=patient_id,
        order_id=order_id,
        document_id=document_id,
        source=RecordSource.DOCTOR,
        status=TestReportStatus.PENDING_REVIEW,
        lab_name=lab_name,
        collected_at=collected_at,
        reported_at=reported_at,
        conclusion=conclusion,
        created_by=actor,
        updated_by=actor,
    )
    session.add(report)
    await session.flush()
    rows = [
        TestResult(
            patient_id=patient_id,
            report_id=report.id,
            sequence=i,
            analyte_name=r.analyte_name.strip(),
            value_numeric=r.value_numeric,
            value_text=r.value_text,
            unit=r.unit,
            reference_low=r.reference_low,
            reference_high=r.reference_high,
            reference_text=r.reference_text,
            flag=r.flag,
            created_by=actor,
            updated_by=actor,
        )
        for i, r in enumerate(results, start=1)
    ]
    session.add_all(rows)
    await session.flush()
    # Entered by the treating doctor from the source document: verified by them.
    report.status = TestReportStatus.VERIFIED
    report.verified_at = datetime.now(UTC)
    report.verified_by = actor
    await session.flush()
    return ReportWithResults(report, rows)
