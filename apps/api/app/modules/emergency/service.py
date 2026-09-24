"""Emergency profile and contacts (patient-controlled)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.emergency.models import EmergencyContact, EmergencyProfile, OrganDonorStatus

MAX_CONTACTS = 10


@dataclass(frozen=True)
class ProfileInput:
    critical_information: str | None
    advance_directive: str | None
    organ_donor: OrganDonorStatus | None
    show_blood_group: bool
    show_allergies: bool
    show_conditions: bool
    show_medications: bool


async def get_profile(session: AsyncSession, patient_id: uuid.UUID) -> EmergencyProfile | None:
    row: EmergencyProfile | None = await session.scalar(
        select(EmergencyProfile).where(EmergencyProfile.patient_id == patient_id)
    )
    return row


async def save_profile(
    session: AsyncSession, patient_id: uuid.UUID, data: ProfileInput, actor: uuid.UUID
) -> EmergencyProfile:
    row = await session.scalar(
        select(EmergencyProfile)
        .where(EmergencyProfile.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = EmergencyProfile(patient_id=patient_id, created_by=actor)
        session.add(row)
    for field, value in data.__dict__.items():
        setattr(row, field, value)
    row.last_reviewed_at = datetime.now(UTC)
    row.updated_by = actor
    await session.flush()
    return row


async def list_contacts(session: AsyncSession, patient_id: uuid.UUID) -> list[EmergencyContact]:
    rows = await session.scalars(
        select(EmergencyContact)
        .where(EmergencyContact.patient_id == patient_id, EmergencyContact.deleted_at.is_(None))
        .order_by(EmergencyContact.priority)
    )
    return list(rows.all())


async def add_contact(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    phone: str,
    relationship_label: str | None,
    notify_on_sos: bool,
) -> EmergencyContact:
    taken = {c.priority for c in await list_contacts(session, patient_id)}
    free = [p for p in range(1, MAX_CONTACTS + 1) if p not in taken]
    if not free:
        raise ConflictError(f"You can have at most {MAX_CONTACTS} emergency contacts.")
    contact = EmergencyContact(
        patient_id=patient_id,
        name=name.strip(),
        phone=phone.strip(),
        relationship_label=relationship_label,
        priority=free[0],
        notify_on_sos=notify_on_sos,
        created_by=actor,
        updated_by=actor,
    )
    session.add(contact)
    await session.flush()
    return contact


async def remove_contact(
    session: AsyncSession, *, patient_id: uuid.UUID, contact_id: uuid.UUID, actor: uuid.UUID
) -> None:
    row = await session.scalar(
        select(EmergencyContact).where(
            EmergencyContact.id == contact_id,
            EmergencyContact.patient_id == patient_id,
            EmergencyContact.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFoundError()
    row.deleted_at = datetime.now(UTC)
    row.deleted_by = actor
    row.updated_by = actor
    await session.flush()
