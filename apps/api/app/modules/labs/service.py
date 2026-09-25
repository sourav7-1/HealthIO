"""Labs service: test orders and reports.

Tests are ordered by name (and optionally a catalogue entry) with the reason for the
test. Reports recorded by a doctor are verified by that doctor; values are entered
exactly as printed on the report. Reports uploaded by a patient or caregiver stay
PENDING_REVIEW until a doctor verifies (or rejects) them. Verifying confirms the file is
this patient's report and its details match it; it never interprets the result.
Results are never interpreted or flagged automatically, by code or by AI.

A patient can share one report with one of their doctors (`ReportShare`) when that
doctor's consent does not cover tests and reports.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordSource
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.labs.models import (
    ReportShare,
    ResultFlag,
    TestOrder,
    TestOrderItem,
    TestOrderStatus,
    TestPriority,
    TestReport,
    TestReportStatus,
    TestResult,
)
from app.modules.timeline.versions import change_reason


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
    async with change_reason(session, reason):
        order.status = TestOrderStatus.CANCELLED
        order.cancelled_at = datetime.now(UTC)
        order.cancelled_by = actor
        order.cancel_reason = reason
        order.updated_by = actor
    return order


# --- order status -------------------------------------------------------------------------

ORDER_TRANSITIONS: dict[TestOrderStatus, set[TestOrderStatus]] = {
    TestOrderStatus.ORDERED: {
        TestOrderStatus.SAMPLE_COLLECTED,
        TestOrderStatus.PARTIALLY_RESULTED,
        TestOrderStatus.COMPLETED,
        TestOrderStatus.ENTERED_IN_ERROR,
    },
    TestOrderStatus.SAMPLE_COLLECTED: {
        TestOrderStatus.PARTIALLY_RESULTED,
        TestOrderStatus.COMPLETED,
        TestOrderStatus.ENTERED_IN_ERROR,
    },
    TestOrderStatus.PARTIALLY_RESULTED: {
        TestOrderStatus.COMPLETED,
        TestOrderStatus.ENTERED_IN_ERROR,
    },
    TestOrderStatus.COMPLETED: {TestOrderStatus.ENTERED_IN_ERROR},
}


def _words(status: TestOrderStatus) -> str:
    return status.value.replace("_", " ")


async def set_order_status(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    order_id: uuid.UUID,
    actor: uuid.UUID,
    status: TestOrderStatus,
    note: str | None,
) -> TestOrder:
    """Move an order along ordered → sample collected → partly resulted → completed.
    Cancelling has its own endpoint; "entered in error" needs a note. Every change is
    versioned by the database with the note as its reason."""
    order: TestOrder | None = await session.scalar(
        select(TestOrder)
        .where(TestOrder.id == order_id, TestOrder.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError()
    if status == order.status:
        return order
    if status not in ORDER_TRANSITIONS.get(order.status, set()):
        raise ConflictError(
            f"An order that is {_words(order.status)} cannot be marked {_words(status)}."
        )
    if status == TestOrderStatus.ENTERED_IN_ERROR and not (note and note.strip()):
        raise ValidationFailedError("Say why this order was entered in error.")
    async with change_reason(session, note or f"marked {_words(status)}"):
        order.status = status
        order.updated_by = actor
    return order


# --- reports ------------------------------------------------------------------------------


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
class ReportMeta:
    test_name: str | None = None
    report_date: date | None = None
    lab_reference: str | None = None
    notes: str | None = None


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
                .order_by(
                    TestReport.report_date.desc().nulls_last(),
                    TestReport.collected_at.desc().nulls_last(),
                    TestReport.created_at.desc(),
                )
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


async def get_report(
    session: AsyncSession,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ReportWithResults:
    stmt = select(TestReport).where(TestReport.id == report_id, TestReport.patient_id == patient_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    report: TestReport | None = await session.scalar(stmt)
    if report is None:
        raise NotFoundError()
    results = (
        await session.scalars(
            select(TestResult)
            .where(TestResult.report_id == report.id)
            .order_by(TestResult.sequence)
        )
    ).all()
    return ReportWithResults(report, list(results))


async def _order_test_names(
    session: AsyncSession, patient_id: uuid.UUID, order_id: uuid.UUID
) -> str:
    order = await session.scalar(
        select(TestOrder).where(TestOrder.id == order_id, TestOrder.patient_id == patient_id)
    )
    if order is None:
        raise NotFoundError("Order not found for this patient.")
    if order.status in (TestOrderStatus.CANCELLED, TestOrderStatus.ENTERED_IN_ERROR):
        raise ConflictError("That order was cancelled; attach the report without it.")
    names = (
        await session.scalars(
            select(TestOrderItem.test_name)
            .where(TestOrderItem.order_id == order_id)
            .order_by(TestOrderItem.test_name)
        )
    ).all()
    return ", ".join(names)[:200]


def _check_report_date(d: date | None) -> None:
    if d is not None and d > datetime.now(UTC).date():
        raise ValidationFailedError("The report date cannot be in the future.")


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
    meta: ReportMeta | None = None,
) -> ReportWithResults:
    meta = meta or ReportMeta()
    if document_id is None and not results:
        raise ValidationFailedError("Attach the report file or enter at least one result.")
    for r in results:
        if not r.analyte_name.strip() or (r.value_numeric is None and not r.value_text):
            raise ValidationFailedError("Each result needs a test name and a value.")
    _check_report_date(meta.report_date)
    test_name = meta.test_name
    if order_id is not None:
        test_name = test_name or await _order_test_names(session, patient_id, order_id) or None
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
        test_name=test_name,
        report_date=meta.report_date,
        lab_reference=meta.lab_reference,
        notes=meta.notes,
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
    now = datetime.now(UTC)
    report.status = TestReportStatus.VERIFIED
    report.verified_at = now
    report.verified_by = actor
    report.reviewed_at = now
    report.reviewed_by = actor
    await session.flush()
    return ReportWithResults(report, rows)


async def record_uploaded_report(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    source: RecordSource,
    document_id: uuid.UUID,
    order_id: uuid.UUID | None,
    lab_name: str | None,
    meta: ReportMeta,
) -> ReportWithResults:
    """A report file the patient or a caregiver added. It is labelled as uploaded and
    stays PENDING_REVIEW until a doctor checks it; nothing reads values out of it."""
    _check_report_date(meta.report_date)
    test_name = meta.test_name
    if order_id is not None:
        test_name = test_name or await _order_test_names(session, patient_id, order_id) or None
    if not test_name:
        raise ValidationFailedError("Add the name of the test.")
    already = await session.scalar(
        select(TestReport.id).where(
            TestReport.patient_id == patient_id,
            TestReport.document_id == document_id,
            TestReport.status.in_([TestReportStatus.PENDING_REVIEW, TestReportStatus.VERIFIED]),
        )
    )
    if already is not None:
        raise ConflictError("This file is already attached to a report.")
    report = TestReport(
        patient_id=patient_id,
        order_id=order_id,
        document_id=document_id,
        source=source,
        status=TestReportStatus.PENDING_REVIEW,
        lab_name=lab_name,
        test_name=test_name,
        report_date=meta.report_date,
        lab_reference=meta.lab_reference,
        notes=meta.notes,
        created_by=actor,
        updated_by=actor,
    )
    session.add(report)
    await session.flush()
    return ReportWithResults(report, [])


async def review_report(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    actor: uuid.UUID,
    verify: bool,
    note: str | None,
) -> ReportWithResults:
    """A doctor confirms an uploaded report is this patient's and its details match the
    file (verified, then frozen), or rejects it with a reason."""
    row = await get_report(session, patient_id, report_id, for_update=True)
    report = row.report
    if report.status != TestReportStatus.PENDING_REVIEW:
        raise ConflictError("Only a report waiting for review can be reviewed.")
    if not verify and not (note and note.strip()):
        raise ValidationFailedError("Say why the report is being rejected.")
    now = datetime.now(UTC)
    report.reviewed_at = now
    report.reviewed_by = actor
    report.review_note = note.strip() if note and note.strip() else None
    report.updated_by = actor
    if verify:
        report.status = TestReportStatus.VERIFIED
        report.verified_at = now
        report.verified_by = actor
    else:
        report.status = TestReportStatus.REJECTED
    await session.flush()
    return row


async def mark_report_in_error(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    actor: uuid.UUID,
    actor_is_doctor: bool,
    reason: str,
) -> ReportWithResults:
    """Withdraw a report added by mistake. The row and its file are kept (history).
    Patients and caregivers can withdraw what they uploaded while it awaits review;
    doctors can mark any open report entered in error."""
    row = await get_report(session, patient_id, report_id, for_update=True)
    report = row.report
    patient_side = report.source in (RecordSource.PATIENT, RecordSource.CAREGIVER)
    if not actor_is_doctor and not (
        patient_side and report.status == TestReportStatus.PENDING_REVIEW
    ):
        raise ForbiddenError(
            "You can withdraw only reports you uploaded that are still waiting for review."
        )
    if report.status not in (TestReportStatus.PENDING_REVIEW, TestReportStatus.VERIFIED):
        raise ConflictError("This report is already closed.")
    report.status = TestReportStatus.ENTERED_IN_ERROR
    report.review_note = reason.strip()
    report.reviewed_at = datetime.now(UTC)
    report.reviewed_by = actor
    report.updated_by = actor
    await session.flush()
    return row


# --- sharing one report with a doctor ------------------------------------------------------


def _live_share(now: datetime) -> list[Any]:
    return [
        ReportShare.revoked_at.is_(None),
        or_(ReportShare.expires_at.is_(None), ReportShare.expires_at > now),
    ]


async def list_shares(
    session: AsyncSession, patient_id: uuid.UUID, report_id: uuid.UUID | None = None
) -> list[ReportShare]:
    stmt = select(ReportShare).where(
        ReportShare.patient_id == patient_id, *_live_share(datetime.now(UTC))
    )
    if report_id is not None:
        stmt = stmt.where(ReportShare.report_id == report_id)
    return list((await session.scalars(stmt.order_by(ReportShare.created_at))).all())


async def shared_report_ids(
    session: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """Reports this doctor may see through a live share."""
    if doctor_id is None:
        return set()
    rows = await session.scalars(
        select(ReportShare.report_id).where(
            ReportShare.patient_id == patient_id,
            ReportShare.doctor_id == doctor_id,
            *_live_share(datetime.now(UTC)),
        )
    )
    return set(rows.all())


async def share_report(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    doctor_id: uuid.UUID,
    linked_doctor_ids: set[uuid.UUID],
    expires_at: datetime | None,
    actor: uuid.UUID,
) -> ReportShare:
    if doctor_id not in linked_doctor_ids:
        raise ValidationFailedError("You can share reports only with doctors on your care team.")
    now = datetime.now(UTC)
    if expires_at is not None and expires_at <= now:
        raise ValidationFailedError("The end date must be in the future.")
    report = (await get_report(session, patient_id, report_id)).report
    if report.status in (TestReportStatus.REJECTED, TestReportStatus.ENTERED_IN_ERROR):
        raise ConflictError("A withdrawn or rejected report cannot be shared.")
    existing: ReportShare | None = await session.scalar(
        select(ReportShare)
        .where(
            ReportShare.report_id == report_id,
            ReportShare.doctor_id == doctor_id,
            ReportShare.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.expires_at is None or existing.expires_at > now:
            existing.expires_at = expires_at  # already shared: update the end date
            existing.updated_by = actor
            await session.flush()
            return existing
        existing.revoked_at = now  # expired: close it and start a new share
        existing.revoked_by = actor
        existing.updated_by = actor
        await session.flush()
    share = ReportShare(
        patient_id=patient_id,
        report_id=report_id,
        doctor_id=doctor_id,
        expires_at=expires_at,
        created_by=actor,
        updated_by=actor,
    )
    session.add(share)
    await session.flush()
    return share


async def revoke_share(
    session: AsyncSession, *, patient_id: uuid.UUID, share_id: uuid.UUID, actor: uuid.UUID
) -> ReportShare:
    share: ReportShare | None = await session.scalar(
        select(ReportShare)
        .where(ReportShare.id == share_id, ReportShare.patient_id == patient_id)
        .with_for_update()
    )
    if share is None or share.revoked_at is not None:
        raise NotFoundError()
    share.revoked_at = datetime.now(UTC)
    share.revoked_by = actor
    share.updated_by = actor
    await session.flush()
    return share
