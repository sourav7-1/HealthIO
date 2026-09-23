"""Care team service: doctor profiles and doctor-patient links."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.care_team.models import (
    DoctorPatientRelationship,
    DoctorProfile,
    DoctorVerificationStatus,
    RelationshipStatus,
)


async def create_doctor_profile(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    display_name: str,
    registration_council: str | None,
    registration_number: str | None,
) -> DoctorProfile:
    has_registration = bool(registration_council and registration_number)
    profile = DoctorProfile(
        user_id=user_id,
        display_name=display_name,
        registration_council=registration_council,
        registration_number=registration_number,
        # Doctors can do nothing clinical until an admin verifies them.
        verification_status=(
            DoctorVerificationStatus.PENDING
            if has_registration
            else DoctorVerificationStatus.UNVERIFIED
        ),
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(profile)
    await session.flush()
    return profile


async def doctor_profile_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> DoctorProfile | None:
    row: DoctorProfile | None = await session.scalar(
        select(DoctorProfile).where(DoctorProfile.user_id == user_id)
    )
    return row


async def has_active_link(
    session: AsyncSession, *, doctor_user_id: uuid.UUID, patient_id: uuid.UUID
) -> bool:
    """True only for a VERIFIED doctor with an ACTIVE relationship to the patient."""
    found = await session.scalar(
        select(DoctorPatientRelationship.id)
        .join(DoctorProfile, DoctorProfile.id == DoctorPatientRelationship.doctor_id)
        .where(
            DoctorProfile.user_id == doctor_user_id,
            DoctorProfile.verification_status == DoctorVerificationStatus.VERIFIED,
            DoctorPatientRelationship.patient_id == patient_id,
            DoctorPatientRelationship.status == RelationshipStatus.ACTIVE,
        )
    )
    return found is not None


@dataclass(frozen=True)
class LinkedPatient:
    patient_id: uuid.UUID
    since: datetime | None
    is_primary_doctor: bool


async def linked_patients(session: AsyncSession, doctor_user_id: uuid.UUID) -> list[LinkedPatient]:
    rows = await session.execute(
        select(
            DoctorPatientRelationship.patient_id,
            DoctorPatientRelationship.started_at,
            DoctorPatientRelationship.is_primary_doctor,
        )
        .join(DoctorProfile, DoctorProfile.id == DoctorPatientRelationship.doctor_id)
        .where(
            DoctorProfile.user_id == doctor_user_id,
            DoctorProfile.verification_status == DoctorVerificationStatus.VERIFIED,
            DoctorPatientRelationship.status == RelationshipStatus.ACTIVE,
        )
        .order_by(DoctorPatientRelationship.started_at.desc())
    )
    return [LinkedPatient(r.patient_id, r.started_at, r.is_primary_doctor) for r in rows]


async def verify_doctor(
    session: AsyncSession,
    *,
    doctor_profile_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    notes: str | None,
) -> DoctorProfile:
    profile = await session.scalar(
        select(DoctorProfile)
        .where(DoctorProfile.id == doctor_profile_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise NotFoundError()
    if profile.verification_status != DoctorVerificationStatus.PENDING:
        raise ConflictError("Only doctors with submitted registration details can be verified.")
    profile.verification_status = DoctorVerificationStatus.VERIFIED
    profile.verified_at = datetime.now(UTC)
    profile.verified_by = admin_user_id
    profile.verification_notes = notes
    profile.updated_by = admin_user_id
    await session.flush()
    return profile


async def require_verified_doctor(session: AsyncSession, user_id: uuid.UUID) -> DoctorProfile:
    profile = await doctor_profile_for_user(session, user_id)
    if profile is None or profile.verification_status != DoctorVerificationStatus.VERIFIED:
        raise ForbiddenError("Your doctor profile must be verified first.")
    return profile


async def linked_patient_ids(session: AsyncSession, doctor_user_id: uuid.UUID) -> list[uuid.UUID]:
    return [p.patient_id for p in await linked_patients(session, doctor_user_id)]


async def open_relationship(
    session: AsyncSession, *, doctor_id: uuid.UUID, patient_id: uuid.UUID
) -> DoctorPatientRelationship | None:
    row: DoctorPatientRelationship | None = await session.scalar(
        select(DoctorPatientRelationship).where(
            DoctorPatientRelationship.doctor_id == doctor_id,
            DoctorPatientRelationship.patient_id == patient_id,
            DoctorPatientRelationship.status.in_(
                [
                    RelationshipStatus.PENDING_PATIENT,
                    RelationshipStatus.PENDING_DOCTOR,
                    RelationshipStatus.ACTIVE,
                ]
            ),
        )
    )
    return row


async def start_active_link(
    session: AsyncSession, *, doctor: DoctorProfile, patient_id: uuid.UUID
) -> DoctorPatientRelationship:
    """Used when the patient is registered in person by this doctor (consent recorded)."""
    rel = DoctorPatientRelationship(
        doctor_id=doctor.id,
        patient_id=patient_id,
        status=RelationshipStatus.ACTIVE,
        is_primary_doctor=False,
        initiated_by=doctor.user_id,
        started_at=datetime.now(UTC),
        created_by=doctor.user_id,
        updated_by=doctor.user_id,
    )
    session.add(rel)
    await session.flush()
    return rel


async def request_connection(
    session: AsyncSession, *, doctor: DoctorProfile, patient_id: uuid.UUID
) -> DoctorPatientRelationship | None:
    """Doctor asks to connect; nothing is shared until the patient accepts and consents.
    Returns None when a pending or active link already exists (idempotent)."""
    if await open_relationship(session, doctor_id=doctor.id, patient_id=patient_id):
        return None
    rel = DoctorPatientRelationship(
        doctor_id=doctor.id,
        patient_id=patient_id,
        status=RelationshipStatus.PENDING_PATIENT,
        initiated_by=doctor.user_id,
        created_by=doctor.user_id,
        updated_by=doctor.user_id,
    )
    session.add(rel)
    await session.flush()
    return rel


@dataclass(frozen=True)
class ConnectionRequest:
    relationship_id: uuid.UUID
    doctor_user_id: uuid.UUID
    doctor_name: str
    primary_specialty: str | None
    requested_at: datetime


async def pending_requests(session: AsyncSession, patient_id: uuid.UUID) -> list[ConnectionRequest]:
    rows = await session.execute(
        select(DoctorPatientRelationship, DoctorProfile)
        .join(DoctorProfile, DoctorProfile.id == DoctorPatientRelationship.doctor_id)
        .where(
            DoctorPatientRelationship.patient_id == patient_id,
            DoctorPatientRelationship.status == RelationshipStatus.PENDING_PATIENT,
        )
        .order_by(DoctorPatientRelationship.created_at.desc())
    )
    return [
        ConnectionRequest(
            rel.id, doc.user_id, doc.display_name, doc.primary_specialty, rel.created_at
        )
        for rel, doc in rows
    ]


async def respond_to_request(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    relationship_id: uuid.UUID,
    accept: bool,
    actor_user_id: uuid.UUID,
) -> tuple[DoctorPatientRelationship, uuid.UUID]:
    """Returns the relationship and the doctor's user id (the consent grantee)."""
    row = await session.execute(
        select(DoctorPatientRelationship, DoctorProfile.user_id)
        .join(DoctorProfile, DoctorProfile.id == DoctorPatientRelationship.doctor_id)
        .where(
            DoctorPatientRelationship.id == relationship_id,
            DoctorPatientRelationship.patient_id == patient_id,
        )
        .with_for_update(of=DoctorPatientRelationship)
        .execution_options(populate_existing=True)
    )
    found = row.first()
    if found is None:
        raise NotFoundError()
    rel, doctor_user_id = found
    if rel.status != RelationshipStatus.PENDING_PATIENT:
        raise ConflictError("This request is no longer open.")
    now = datetime.now(UTC)
    if accept:
        rel.status = RelationshipStatus.ACTIVE
        rel.started_at = now
    else:
        rel.status = RelationshipStatus.DECLINED
        rel.ended_at = now
        rel.ended_by = actor_user_id
    rel.updated_by = actor_user_id
    await session.flush()
    return rel, doctor_user_id


async def doctor_names(session: AsyncSession, doctor_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not doctor_ids:
        return {}
    rows = await session.execute(
        select(DoctorProfile.id, DoctorProfile.display_name).where(DoctorProfile.id.in_(doctor_ids))
    )
    return dict(rows.tuples().all())


async def doctor_id_for(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    profile = await doctor_profile_for_user(session, user_id)
    return profile.id if profile else None
