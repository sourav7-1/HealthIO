"""Caregiver dashboard: one view across everyone the caller looks after.

Each person's sections are decided by the same access rules as the per-patient endpoints
(resolve_patient_access): a section the caregiver has no scope for comes back as `null`
("not shared"), never as an empty list, so the UI can say so honestly. Every section
returned is a read of that patient's data and is audited against that patient.
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import client_info
from app.core.db import get_session
from app.modules.access.dependencies import authenticated
from app.modules.access.permissions import Permission
from app.modules.access.service import resolve_patient_access
from app.modules.appointments import service as appointments
from app.modules.appointments.models import AppointmentStatus, FollowUpStatus
from app.modules.appointments.router import (
    AppointmentOut,
    FollowUpOut,
    appointments_out,
    follow_ups_out,
)
from app.modules.audit.service import record_patient_event
from app.modules.caregivers import service
from app.modules.caregivers.models import CaregiverRelationshipType, CaregiverStatus
from app.modules.caregivers.router import patient_label
from app.modules.identity.service import Principal
from app.modules.labs import service as labs
from app.modules.medications import doses
from app.modules.medications.models import DoseStatus
from app.modules.medications.router import DoseOut, dose_out
from app.modules.medications.schedules import local_day_bounds
from app.modules.patients import service as patients
from app.modules.reminders import service as reminders

router = APIRouter(tags=["caregivers"])

MISSED_LOOKBACK = timedelta(days=7)
RECENT_REPORTS = 5


class ReportSummary(BaseModel):
    id: uuid.UUID
    lab_name: str | None
    source: str
    collected_at: datetime | None
    created_at: datetime
    result_count: int


class DependantSummary(BaseModel):
    patient_id: uuid.UUID
    relationship_id: uuid.UUID
    name: str
    relationship_type: CaregiverRelationshipType
    is_guardian: bool
    is_dependant: bool
    permissions: list[str]
    # null = not shared with this caregiver (no scope); [] = shared, nothing to show.
    today_doses: list[DoseOut] | None
    missed_doses: list[DoseOut] | None
    appointments: list[AppointmentOut] | None
    follow_ups: list[FollowUpOut] | None
    recent_reports: list[ReportSummary] | None


class CaregiverDashboard(BaseModel):
    people: list[DependantSummary]


@router.get("/me/caregiving/dashboard", response_model=CaregiverDashboard)
async def caregiver_dashboard(
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> CaregiverDashboard:
    now = datetime.now(UTC)
    links = [
        lk
        for lk in await service.links_for_caregiver(session, principal.user_id)
        if lk.status == CaregiverStatus.ACTIVE
    ]
    profiles = await patients.get_profiles(session, [lk.patient_id for lk in links])
    people: list[DependantSummary] = []
    for link in links:
        profile = profiles.get(link.patient_id)
        if profile is None:
            continue
        access = await resolve_patient_access(session, principal, link.patient_id, now)
        if "caregiver" not in access.via:
            continue  # expired, or the caregiver role is missing
        sections: list[str] = []

        today: list[DoseOut] | None = None
        missed: list[DoseOut] | None = None
        if access.allows(Permission.VIEW_MEDICATIONS):
            prefs = await reminders.get(session, link.patient_id)
            await doses.materialize(session, link.patient_id, now=now)
            await doses.mark_missed(session, link.patient_id, now=now, after=prefs.missed_after)
            start, end = local_day_bounds(
                now.astimezone(ZoneInfo(profile.timezone)).date(), profile.timezone
            )
            today = [
                dose_out(r.dose, r.medication, r.meal_relation)
                for r in await doses.list_doses(session, link.patient_id, start, end)
            ]
            missed = [
                dose_out(r.dose, r.medication, r.meal_relation)
                for r in await doses.list_doses(
                    session, link.patient_id, now - MISSED_LOOKBACK, now
                )
                if r.dose.status == DoseStatus.MISSED
            ]
            sections.append("medications")

        appts: list[AppointmentOut] | None = None
        follow: list[FollowUpOut] | None = None
        if access.allows(Permission.VIEW_APPOINTMENTS):
            upcoming = [
                a
                for a in await appointments.list_for_patient(session, link.patient_id)
                if a.starts_at >= now
                and a.status
                in (
                    AppointmentStatus.REQUESTED,
                    AppointmentStatus.SCHEDULED,
                    AppointmentStatus.CONFIRMED,
                )
            ]
            appts = await appointments_out(session, sorted(upcoming, key=lambda a: a.starts_at))
            open_follow_ups = [
                f
                for f in await appointments.list_follow_ups(session, link.patient_id)
                if f.status in (FollowUpStatus.OPEN, FollowUpStatus.BOOKED)
            ]
            follow = await follow_ups_out(session, open_follow_ups)
            sections.append("appointments")

        reports: list[ReportSummary] | None = None
        if access.allows(Permission.VIEW_REPORTS):
            rows = await labs.list_reports(session, link.patient_id)
            reports = [
                ReportSummary(
                    id=r.report.id,
                    lab_name=r.report.lab_name,
                    source=r.report.source.value,
                    collected_at=r.report.collected_at,
                    created_at=r.report.created_at,
                    result_count=len(r.results),
                )
                for r in sorted(rows, key=lambda r: r.report.created_at, reverse=True)[
                    :RECENT_REPORTS
                ]
            ]
            sections.append("reports")

        await record_patient_event(
            session,
            client_info(request),
            actor_user_id=principal.user_id,
            patient_id=link.patient_id,
            action="caregiver.dashboard_view",
            context={"via": "caregiver", "sections": ",".join(sections) or "none"},
        )
        people.append(
            DependantSummary(
                patient_id=link.patient_id,
                relationship_id=link.relationship_id,
                name=patient_label(profile),
                relationship_type=link.relationship_type,
                is_guardian=link.is_guardian,
                is_dependant=profile.user_id is None,
                permissions=sorted(p.value for p in access.permissions),
                today_doses=today,
                missed_doses=missed,
                appointments=appts,
                follow_ups=follow,
                recent_reports=reports,
            )
        )
    await session.commit()
    return CaregiverDashboard(people=people)
