"""Doctor workspace (dashboard, patient list, adding/connecting patients), the patient's
side of connection requests, and admin verification of doctors.

Everything a doctor sees here is limited to patients with an ACTIVE relationship, and
each item is narrowed to the data categories that patient consented to share.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import client_info
from app.core.db import get_session
from app.core.enums import Role
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailedError
from app.core.mail import Mailer, OutgoingEmail
from app.modules.access.dependencies import authenticated, require_permission, require_roles
from app.modules.access.permissions import Permission
from app.modules.appointments import service as appointments
from app.modules.audit.service import record_client_event, record_patient_event
from app.modules.care_team import service
from app.modules.chart.router import ProfileOut, display_name, profile_out
from app.modules.clinical import service as clinical
from app.modules.consent import service as consent
from app.modules.consent.models import ConsentPurpose, DataCategory, GrantorCapacity
from app.modules.identity import service as identity
from app.modules.identity.service import Principal
from app.modules.medications import service as medications
from app.modules.patients import service as patients
from app.modules.patients.models import PatientProfile, SexAtBirth
from app.modules.prescriptions import service as prescriptions

router = APIRouter(tags=["care team"])

CURRENT_NOTICE_VERSION = "2026-09-en"
_doctor = require_permission(Permission.LIST_OWN_PATIENTS)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- scope helper --------------------------------------------------------------------


class _Scope:
    """The doctor's patients and, per patient, the consented data categories."""

    def __init__(
        self,
        cats: dict[uuid.UUID, frozenset[DataCategory]],
        profiles: dict[uuid.UUID, PatientProfile],
    ):
        self.cats = cats
        self.profiles = profiles

    def with_category(self, category: DataCategory) -> list[uuid.UUID]:
        return [pid for pid, c in self.cats.items() if category in c]

    def name(self, pid: uuid.UUID) -> str | None:
        """Only when the patient shares demographics with this doctor."""
        p = self.profiles.get(pid)
        return (
            display_name(p) if p and DataCategory.DEMOGRAPHICS in self.cats.get(pid, ()) else None
        )


async def _scope(session: AsyncSession, doctor_user_id: uuid.UUID) -> _Scope:
    ids = await service.linked_patient_ids(session, doctor_user_id)
    cats = await consent.categories_by_patient(
        session,
        grantee_user_id=doctor_user_id,
        patient_ids=ids,
        purpose=ConsentPurpose.CARE_DELIVERY,
        now=datetime.now(UTC),
    )
    return _Scope(cats, await patients.get_profiles(session, ids))


# --- dashboard -----------------------------------------------------------------------


class DashboardAppointment(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str | None
    starts_at: datetime
    ends_at: datetime
    status: str
    mode: str
    reason: str | None


class DashboardFollowUp(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str | None
    due_date: date
    reason: str | None
    status: str
    overdue: bool


class RecentPatient(BaseModel):
    patient_id: uuid.UUID
    patient_name: str | None
    last_visit_at: datetime | None


class DashboardOut(BaseModel):
    doctor_name: str | None
    verification_status: str | None
    timezone: str
    today: date
    total_patients: int
    today_appointments: list[DashboardAppointment]
    upcoming_follow_ups: list[DashboardFollowUp]
    active_treatments: int
    recent_patients: list[RecentPatient]


@router.get("/doctor/dashboard", response_model=DashboardOut)
async def dashboard(
    request: Request,
    principal: Principal = Depends(_doctor),
    session: AsyncSession = Depends(get_session),
) -> DashboardOut:
    account = await identity.get_account(session, principal.user_id)
    tz_name = account.timezone if account else "Asia/Kolkata"
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    doctor = await service.doctor_profile_for_user(session, principal.user_id)
    empty = DashboardOut(
        doctor_name=doctor.display_name if doctor else None,
        verification_status=doctor.verification_status.value if doctor else None,
        timezone=tz_name,
        today=today,
        total_patients=0,
        today_appointments=[],
        upcoming_follow_ups=[],
        active_treatments=0,
        recent_patients=[],
    )
    if doctor is None or doctor.verification_status.value != "verified":
        return empty

    scope = await _scope(session, principal.user_id)
    start = datetime.combine(today, time.min, tzinfo=tz)
    appts = await appointments.for_doctor_between(
        session,
        doctor.id,
        scope.with_category(DataCategory.APPOINTMENTS),
        start,
        start + timedelta(days=1),
    )
    follow_ups = await appointments.upcoming_for_doctor(
        session,
        doctor.id,
        scope.with_category(DataCategory.APPOINTMENTS),
        today + timedelta(days=14),
    )
    active = await medications.count_active_for_items(
        session,
        await prescriptions.active_item_ids_by_prescriber(session, doctor.id),
        scope.with_category(DataCategory.MEDICATIONS),
    )
    recent = await clinical.recent_visits_by_doctor(
        session, doctor.id, scope.with_category(DataCategory.VISITS_AND_NOTES)
    )
    await record_client_event(
        session,
        client_info(request),
        action="doctor.dashboard_view",
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return empty.model_copy(
        update={
            "total_patients": len(scope.cats),
            "today_appointments": [
                DashboardAppointment(
                    id=a.id,
                    patient_id=a.patient_id,
                    patient_name=scope.name(a.patient_id),
                    starts_at=a.starts_at,
                    ends_at=a.ends_at,
                    status=a.status.value,
                    mode=a.mode.value,
                    reason=a.reason,
                )
                for a in appts
            ],
            "upcoming_follow_ups": [
                DashboardFollowUp(
                    id=f.id,
                    patient_id=f.patient_id,
                    patient_name=scope.name(f.patient_id),
                    due_date=f.due_date,
                    reason=f.reason,
                    status=f.status.value,
                    overdue=f.due_date < today,
                )
                for f in follow_ups
            ],
            "active_treatments": active,
            "recent_patients": [
                RecentPatient(patient_id=pid, patient_name=scope.name(pid), last_visit_at=when)
                for pid, when in recent
            ],
        }
    )


# --- patient list and search ---------------------------------------------------------


class PatientListItem(BaseModel):
    patient_id: uuid.UUID
    display_name: str | None = Field(
        description="Null when the patient has not shared demographics"
    )
    date_of_birth: date | None
    sex_at_birth: str | None
    since: datetime | None
    is_primary_doctor: bool
    shared_categories: list[str]


@router.get("/doctor/patients", response_model=list[PatientListItem])
async def my_patients(
    request: Request,
    q: str | None = None,
    principal: Principal = Depends(_doctor),
    session: AsyncSession = Depends(get_session),
) -> list[PatientListItem]:
    """Only patients with an ACTIVE relationship to this (verified) doctor. Name search
    only covers patients who share demographics."""
    links = {p.patient_id: p for p in await service.linked_patients(session, principal.user_id)}
    scope = await _scope(session, principal.user_id)
    ids = list(links)
    if q and q.strip():
        ids = await patients.search_within(
            session, scope.with_category(DataCategory.DEMOGRAPHICS), q[:100]
        )
    await record_client_event(
        session,
        client_info(request),
        action="doctor.patient_list" if not q else "doctor.patient_search",
        actor_user_id=principal.user_id,
    )
    await session.commit()
    out = []
    for pid in ids:
        shares_demo = DataCategory.DEMOGRAPHICS in scope.cats.get(pid, ())
        p = scope.profiles.get(pid)
        out.append(
            PatientListItem(
                patient_id=pid,
                display_name=scope.name(pid),
                date_of_birth=p.date_of_birth if p and shares_demo else None,
                sex_at_birth=p.sex_at_birth.value if p and shares_demo else None,
                since=links[pid].since,
                is_primary_doctor=links[pid].is_primary_doctor,
                shared_categories=sorted(c.value for c in scope.cats.get(pid, ())),
            )
        )
    out.sort(key=lambda i: (i.display_name or "~").lower())
    return out


# --- add a new patient (registered in person) ------------------------------------------


class InPersonConsent(_In):
    confirmed: Literal[True] = Field(
        description="The patient (or guardian) agreed in person after seeing the privacy notice"
    )
    data_categories: set[DataCategory] = Field(min_length=1)
    notice_version: str = Field(default=CURRENT_NOTICE_VERSION, max_length=32)


class NewPatientIn(_In):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = None
    sex_at_birth: SexAtBirth = SexAtBirth.UNKNOWN
    consent: InPersonConsent


@router.post("/doctor/patients", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def add_patient(
    body: NewPatientIn,
    request: Request,
    principal: Principal = Depends(_doctor),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    """Register a patient who is with the doctor in person. The consent they gave is
    recorded as clinician-recorded; they can review or withdraw it once they have an
    account."""
    doctor = await service.require_verified_doctor(session, principal.user_id)
    if body.date_of_birth and body.date_of_birth > datetime.now(UTC).date():
        raise ValidationFailedError("Date of birth cannot be in the future.")
    profile = await patients.create_clinic_patient(
        session,
        given_name=body.given_name,
        family_name=body.family_name,
        date_of_birth=body.date_of_birth,
        sex_at_birth=body.sex_at_birth,
        created_by=principal.user_id,
    )
    await service.start_active_link(session, doctor=doctor, patient_id=profile.id)
    record = await consent.record_care_consent(
        session,
        patient_id=profile.id,
        granted_by=principal.user_id,
        capacity=GrantorCapacity.CLINICIAN_RECORDED,
        doctor_user_id=principal.user_id,
        categories=set(body.consent.data_categories),
        notice_version=body.consent.notice_version,
    )
    info = client_info(request)
    await record_patient_event(
        session,
        info,
        actor_user_id=principal.user_id,
        patient_id=profile.id,
        action="patient.register_in_person",
        resource_type="patient_profile",
        resource_id=profile.id,
    )
    await record_patient_event(
        session,
        info,
        actor_user_id=principal.user_id,
        patient_id=profile.id,
        action="consent.recorded_by_clinician",
        resource_type="consent_record",
        resource_id=record.id,
        context={"categories": ",".join(sorted(c.value for c in body.consent.data_categories))},
    )
    await session.commit()
    return profile_out(profile)


# --- connect an existing patient -------------------------------------------------------


class ConnectIn(_In):
    patient_email: EmailStr


class Accepted(BaseModel):
    detail: str


@router.post(
    "/doctor/patients/connect", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED
)
async def connect_patient(
    body: ConnectIn,
    request: Request,
    principal: Principal = Depends(_doctor),
    session: AsyncSession = Depends(get_session),
) -> Accepted:
    """Ask an existing patient to connect. The answer is the same whether or not the email
    has an account, and nothing is shared until the patient accepts and chooses what to
    share."""
    doctor = await service.require_verified_doctor(session, principal.user_id)
    user_id = await identity.find_user_id_by_email(session, body.patient_email)
    patient_id = await patients.self_profile_id(session, user_id) if user_id else None
    rel = (
        await service.request_connection(session, doctor=doctor, patient_id=patient_id)
        if patient_id
        else None
    )
    await record_client_event(
        session,
        client_info(request),
        action="doctor.connect_requested",
        actor_user_id=principal.user_id,
        patient_id=patient_id if rel else None,
        resource_type="doctor_patient_relationship",
        resource_id=rel.id if rel else None,
        reason_code=None if rel else "no_request_created",
    )
    await session.commit()
    if rel is not None:
        mailer: Mailer = request.app.state.mailer
        account = await identity.get_account(session, user_id) if user_id else None
        if account and account.email:
            await mailer.send(
                OutgoingEmail(
                    to=account.email,
                    subject="A doctor asked to connect with you on Health Io",
                    text=(
                        f"{doctor.display_name} asked to connect with you. Nothing is shared "
                        "until you accept and choose what to share. Open Health Io to review "
                        "the request."
                    ),
                    meta={"purpose": "doctor_connect_request"},
                )
            )
    return Accepted(detail="If this email belongs to a patient, they will receive your request.")


# --- the patient's side of connection requests ----------------------------------------


class ConnectionRequestOut(BaseModel):
    relationship_id: uuid.UUID
    doctor_name: str
    primary_specialty: str | None
    requested_at: datetime


class RespondIn(_In):
    data_categories: set[DataCategory] = Field(default_factory=set)


async def _own_profile(session: AsyncSession, principal: Principal) -> uuid.UUID:
    if not principal.has_role(Role.PATIENT):
        raise ForbiddenError()
    pid = await patients.self_profile_id(session, principal.user_id)
    if pid is None:
        raise NotFoundError()
    return pid


@router.get("/me/doctor-requests", response_model=list[ConnectionRequestOut])
async def my_doctor_requests(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> list[ConnectionRequestOut]:
    pid = await _own_profile(session, principal)
    return [
        ConnectionRequestOut(
            relationship_id=r.relationship_id,
            doctor_name=r.doctor_name,
            primary_specialty=r.primary_specialty,
            requested_at=r.requested_at,
        )
        for r in await service.pending_requests(session, pid)
    ]


@router.post(
    "/me/doctor-requests/{relationship_id}/{decision}", status_code=status.HTTP_204_NO_CONTENT
)
async def respond_to_doctor_request(
    relationship_id: uuid.UUID,
    decision: Literal["accept", "decline"],
    body: RespondIn,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> None:
    pid = await _own_profile(session, principal)
    accept = decision == "accept"
    if accept and not body.data_categories:
        raise ValidationFailedError("Choose at least one type of information to share.")
    rel, doctor_user_id = await service.respond_to_request(
        session,
        patient_id=pid,
        relationship_id=relationship_id,
        accept=accept,
        actor_user_id=principal.user_id,
    )
    info = client_info(request)
    if accept:
        record = await consent.record_care_consent(
            session,
            patient_id=pid,
            granted_by=principal.user_id,
            capacity=GrantorCapacity.SELF,
            doctor_user_id=doctor_user_id,
            categories=set(body.data_categories),
            notice_version=CURRENT_NOTICE_VERSION,
        )
        await record_patient_event(
            session,
            info,
            actor_user_id=principal.user_id,
            patient_id=pid,
            action="consent.granted",
            resource_type="consent_record",
            resource_id=record.id,
            context={"categories": ",".join(sorted(c.value for c in body.data_categories))},
        )
    await record_patient_event(
        session,
        info,
        actor_user_id=principal.user_id,
        patient_id=pid,
        action=f"doctor_request.{decision}",
        resource_type="doctor_patient_relationship",
        resource_id=rel.id,
    )
    await session.commit()


# --- admin -----------------------------------------------------------------------------


class VerifyDoctor(_In):
    notes: str | None = Field(default=None, max_length=1000)


class DoctorVerificationOut(BaseModel):
    doctor_profile_id: uuid.UUID
    verification_status: str
    verified_at: datetime | None


@router.post("/admin/doctors/{doctor_profile_id}/verify", response_model=DoctorVerificationOut)
async def verify_doctor(
    doctor_profile_id: uuid.UUID,
    body: VerifyDoctor,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> DoctorVerificationOut:
    profile = await service.verify_doctor(
        session,
        doctor_profile_id=doctor_profile_id,
        admin_user_id=principal.user_id,
        notes=body.notes,
    )
    await record_client_event(
        session,
        client_info(request),
        action="doctor.verified",
        actor_user_id=principal.user_id,
        resource_type="doctor_profile",
        resource_id=profile.id,
    )
    await session.commit()
    return DoctorVerificationOut(
        doctor_profile_id=profile.id,
        verification_status=profile.verification_status.value,
        verified_at=profile.verified_at,
    )
