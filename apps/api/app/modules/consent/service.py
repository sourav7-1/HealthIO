"""Consent service: evaluates what a patient has agreed to share, with whom."""

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consent.models import (
    ConsentPurpose,
    ConsentRecord,
    ConsentStatus,
    DataCategory,
    GranteeType,
    GrantorCapacity,
)


async def shared_categories(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    grantee_user_id: uuid.UUID,
    purpose: ConsentPurpose,
    now: datetime,
) -> frozenset[DataCategory]:
    """Union of data categories in the grantee's active, unexpired consents."""
    rows = await session.scalars(
        select(ConsentRecord.data_categories).where(
            ConsentRecord.patient_id == patient_id,
            ConsentRecord.grantee_user_id == grantee_user_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.status == ConsentStatus.ACTIVE,
            ConsentRecord.valid_from <= now,
            or_(ConsentRecord.valid_until.is_(None), ConsentRecord.valid_until > now),
        )
    )
    return frozenset(DataCategory(c) for cats in rows.all() for c in cats)


async def categories_by_patient(
    session: AsyncSession,
    *,
    grantee_user_id: uuid.UUID,
    patient_ids: list[uuid.UUID],
    purpose: ConsentPurpose,
    now: datetime,
) -> dict[uuid.UUID, frozenset[DataCategory]]:
    """shared_categories() for many patients in one query."""
    if not patient_ids:
        return {}
    rows = await session.execute(
        select(ConsentRecord.patient_id, ConsentRecord.data_categories).where(
            ConsentRecord.patient_id.in_(patient_ids),
            ConsentRecord.grantee_user_id == grantee_user_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.status == ConsentStatus.ACTIVE,
            ConsentRecord.valid_from <= now,
            or_(ConsentRecord.valid_until.is_(None), ConsentRecord.valid_until > now),
        )
    )
    found: dict[uuid.UUID, set[DataCategory]] = {pid: set() for pid in patient_ids}
    for pid, cats in rows:
        found[pid] |= {DataCategory(c) for c in cats}
    return {pid: frozenset(c) for pid, c in found.items()}


async def record_care_consent(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    granted_by: uuid.UUID,
    capacity: GrantorCapacity,
    doctor_user_id: uuid.UUID,
    categories: set[DataCategory],
    notice_version: str,
) -> ConsentRecord:
    record = ConsentRecord(
        patient_id=patient_id,
        granted_by=granted_by,
        grantor_capacity=capacity,
        grantee_type=GranteeType.DOCTOR,
        grantee_user_id=doctor_user_id,
        purpose=ConsentPurpose.CARE_DELIVERY,
        data_categories=sorted(c.value for c in categories),
        notice_version=notice_version,
        created_by=granted_by,
        updated_by=granted_by,
    )
    session.add(record)
    await session.flush()
    return record
