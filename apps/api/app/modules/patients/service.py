"""Patients service: the public interface other modules use for patient profiles."""

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.models import PatientProfile, PatientStatus, SexAtBirth


async def create_self_profile(
    session: AsyncSession, *, user_id: uuid.UUID, display_name: str
) -> PatientProfile:
    """Profile for a patient who signs in themselves (created at registration)."""
    profile = PatientProfile(
        user_id=user_id, given_name=display_name, created_by=user_id, updated_by=user_id
    )
    session.add(profile)
    await session.flush()
    return profile


async def get_live_profile(session: AsyncSession, patient_id: uuid.UUID) -> PatientProfile | None:
    row: PatientProfile | None = await session.scalar(
        select(PatientProfile).where(
            PatientProfile.id == patient_id, PatientProfile.deleted_at.is_(None)
        )
    )
    return row


async def self_profile_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    row: uuid.UUID | None = await session.scalar(
        select(PatientProfile.id).where(
            PatientProfile.user_id == user_id,
            PatientProfile.deleted_at.is_(None),
            PatientProfile.status == PatientStatus.ACTIVE,
        )
    )
    return row


async def create_clinic_patient(
    session: AsyncSession,
    *,
    given_name: str,
    family_name: str | None,
    date_of_birth: date | None,
    sex_at_birth: SexAtBirth,
    created_by: uuid.UUID,
) -> PatientProfile:
    """A patient registered by a clinician; they have no login until they claim the record."""
    profile = PatientProfile(
        user_id=None,
        given_name=given_name.strip(),
        family_name=(family_name or "").strip() or None,
        date_of_birth=date_of_birth,
        sex_at_birth=sex_at_birth,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(profile)
    await session.flush()
    return profile


async def get_profiles(
    session: AsyncSession, patient_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PatientProfile]:
    if not patient_ids:
        return {}
    rows = await session.scalars(
        select(PatientProfile).where(
            PatientProfile.id.in_(patient_ids), PatientProfile.deleted_at.is_(None)
        )
    )
    return {p.id: p for p in rows.all()}


async def search_within(
    session: AsyncSession, patient_ids: list[uuid.UUID], query: str
) -> list[uuid.UUID]:
    """Name search restricted to an allowed set of patients (never the whole table)."""
    if not patient_ids:
        return []
    terms = [t for t in query.strip().split() if t][:4]
    stmt = select(PatientProfile.id).where(
        PatientProfile.id.in_(patient_ids), PatientProfile.deleted_at.is_(None)
    )
    for term in terms:
        like = f"%{term.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(
            or_(PatientProfile.given_name.ilike(like), PatientProfile.family_name.ilike(like))
        )
    rows = await session.scalars(stmt.order_by(PatientProfile.given_name).limit(100))
    return list(rows.all())
