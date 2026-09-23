"""Patient chart composition: overview and timeline.

Each section is included only if the caller holds the permission for it, so a doctor
whose consent covers medications but not visits sees a timeline without visits. Event
titles describe what was recorded; they never summarise or interpret clinical content.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.appointments import service as appointments
from app.modules.appointments.models import FollowUpStatus
from app.modules.clinical import service as clinical
from app.modules.clinical.models import ConditionClinicalStatus, NoteStatus
from app.modules.labs import service as labs
from app.modules.labs.models import TestOrderStatus
from app.modules.medications import service as medications
from app.modules.prescriptions import service as prescriptions
from app.modules.prescriptions.models import PrescriptionStatus


def _label(value: str) -> str:
    return value.replace("_", " ")


@dataclass(frozen=True)
class TimelineEvent:
    at: datetime
    # visit | note | diagnosis | prescription | medication | test_order | report
    # | appointment | follow_up
    kind: str
    title: str
    detail: str | None = None
    status: str | None = None
    resource_id: uuid.UUID | None = None


def _day(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=UTC)


async def timeline(
    session: AsyncSession,
    access: PatientAccess,
    viewer_doctor_id: uuid.UUID | None,
    limit: int = 200,
) -> list[TimelineEvent]:
    pid = access.patient_id
    events: list[TimelineEvent] = []

    if access.allows(Permission.VIEW_VISITS):
        for v in await clinical.list_visits(session, pid):
            events.append(
                TimelineEvent(
                    at=v.started_at or v.created_at,
                    kind="visit",
                    title=f"Visit ({_label(v.visit_type.value)})",
                    detail=v.chief_complaint,
                    status=v.status.value,
                    resource_id=v.id,
                )
            )
        for n in await clinical.list_notes(
            session, patient_id=pid, viewer_doctor_id=viewer_doctor_id
        ):
            if n.status == NoteStatus.DRAFT:
                continue
            events.append(
                TimelineEvent(
                    at=n.signed_at or n.created_at,
                    kind="note",
                    title=(
                        f"Amended {_label(n.note_type.value)} note signed"
                        if n.supersedes_note_id
                        else f"{_label(n.note_type.value).capitalize()} note signed"
                    ),
                    status=n.status.value,
                    resource_id=n.visit_id,
                )
            )

    if access.allows(Permission.VIEW_MEDICAL_HISTORY):
        for c in await clinical.list_conditions(session, pid):
            events.append(
                TimelineEvent(
                    at=c.created_at,
                    kind="diagnosis",
                    title=f"Condition recorded: {c.name}",
                    detail=(
                        f"{_label(c.verification_status.value)}, "
                        f"{_label(c.clinical_status.value)}"
                        + (f" (ICD-10 {c.icd10_code})" if c.icd10_code else "")
                    ),
                    status=c.verification_status.value,
                    resource_id=c.id,
                )
            )

    if access.allows(Permission.VIEW_PRESCRIPTIONS):
        for rx in await prescriptions.list_for_patient(
            session, pid, viewer_doctor_id=viewer_doctor_id
        ):
            p = rx.prescription
            if p.status == PrescriptionStatus.DRAFT:
                continue
            count = len(rx.items)
            events.append(
                TimelineEvent(
                    at=p.issued_at or p.created_at,
                    kind="prescription",
                    title=(
                        f"Prescription {_label(p.status.value)} "
                        f"({count} medicine{'s' if count != 1 else ''})"
                    ),
                    detail=", ".join(i.drug_name for i in rx.items[:5])
                    + ("…" if count > 5 else ""),
                    status=p.status.value,
                    resource_id=p.id,
                )
            )

    if access.allows(Permission.VIEW_MEDICATIONS):
        for m in await medications.list_for_patient(session, pid):
            if m.prescription_item_id is not None:
                continue  # already shown with its prescription
            events.append(
                TimelineEvent(
                    at=m.created_at,
                    kind="medication",
                    title=f"Medicine recorded: {m.name}",
                    detail=_label(m.source.value),
                    status=m.status.value,
                    resource_id=m.id,
                )
            )

    if access.allows(Permission.VIEW_REPORTS):
        for o in await labs.list_orders(session, pid):
            events.append(
                TimelineEvent(
                    at=o.order.ordered_at,
                    kind="test_order",
                    title=f"Tests ordered ({len(o.items)})",
                    detail=", ".join(i.test_name for i in o.items[:6])
                    + ("…" if len(o.items) > 6 else ""),
                    status=o.order.status.value,
                    resource_id=o.order.id,
                )
            )
        for r in await labs.list_reports(session, pid):
            events.append(
                TimelineEvent(
                    at=r.report.collected_at or r.report.created_at,
                    kind="report",
                    title="Report recorded"
                    + (f": {r.report.lab_name}" if r.report.lab_name else ""),
                    detail=f"{len(r.results)} value{'s' if len(r.results) != 1 else ''} entered"
                    if r.results
                    else "File attached",
                    status=r.report.status.value,
                    resource_id=r.report.id,
                )
            )

    if access.allows(Permission.VIEW_APPOINTMENTS):
        for a in await appointments.list_for_patient(session, pid):
            events.append(
                TimelineEvent(
                    at=a.starts_at,
                    kind="appointment",
                    title=f"Appointment ({_label(a.mode.value)})",
                    detail=a.reason,
                    status=a.status.value,
                    resource_id=a.id,
                )
            )
        for f in await appointments.list_follow_ups(session, pid):
            events.append(
                TimelineEvent(
                    at=_day(f.due_date),
                    kind="follow_up",
                    title="Follow-up due",
                    detail=f.reason,
                    status=f.status.value,
                    resource_id=f.id,
                )
            )

    events.sort(key=lambda e: e.at, reverse=True)
    return events[:limit]


@dataclass
class Overview:
    active_conditions: list[str] | None = None
    allergies: list[str] | None = None
    active_medications: int | None = None
    open_test_orders: int | None = None
    next_appointment: datetime | None = None
    next_follow_up: date | None = None
    last_visit: datetime | None = None
    sections: list[str] = field(default_factory=list)


async def overview(session: AsyncSession, access: PatientAccess) -> Overview:
    pid = access.patient_id
    out = Overview()
    now = datetime.now(UTC)
    if access.allows(Permission.VIEW_MEDICAL_HISTORY):
        out.sections.append("medical_history")
        out.active_conditions = [
            c.name
            for c in await clinical.list_conditions(session, pid)
            if c.clinical_status
            in (
                ConditionClinicalStatus.ACTIVE,
                ConditionClinicalStatus.RECURRENCE,
                ConditionClinicalStatus.RELAPSE,
            )
            and c.verification_status.value not in ("refuted", "entered_in_error")
        ]
        out.allergies = [a.substance for a in await clinical.list_allergies(session, pid)]
    if access.allows(Permission.VIEW_MEDICATIONS):
        out.sections.append("medications")
        out.active_medications = await medications.count_active_for_patient(session, pid)
    if access.allows(Permission.VIEW_REPORTS):
        out.sections.append("reports")
        out.open_test_orders = sum(
            1
            for o in await labs.list_orders(session, pid)
            if o.order.status
            in (
                TestOrderStatus.ORDERED,
                TestOrderStatus.SAMPLE_COLLECTED,
                TestOrderStatus.PARTIALLY_RESULTED,
            )
        )
    if access.allows(Permission.VIEW_APPOINTMENTS):
        out.sections.append("appointments")
        upcoming = [
            a.starts_at
            for a in await appointments.list_for_patient(session, pid)
            if a.starts_at >= now and a.status in appointments.LIVE
        ]
        out.next_appointment = min(upcoming) if upcoming else None
        due = [
            f.due_date
            for f in await appointments.list_follow_ups(session, pid)
            if f.status in (FollowUpStatus.OPEN, FollowUpStatus.BOOKED)
        ]
        out.next_follow_up = min(due) if due else None
    if access.allows(Permission.VIEW_VISITS):
        out.sections.append("visits")
        visits = [v.started_at for v in await clinical.list_visits(session, pid) if v.started_at]
        out.last_visit = max(visits) if visits else None
    return out
