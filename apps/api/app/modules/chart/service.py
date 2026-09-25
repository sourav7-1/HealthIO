"""Patient chart overview. Each section is included only if the caller holds the
permission for it. The timeline is in app.modules.timeline."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.appointments import service as appointments
from app.modules.appointments.models import FollowUpStatus
from app.modules.clinical import service as clinical
from app.modules.clinical.models import ConditionClinicalStatus
from app.modules.labs import service as labs
from app.modules.labs.models import TestOrderStatus
from app.modules.medications import service as medications


def _label(value: str) -> str:
    return value.replace("_", " ")


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
