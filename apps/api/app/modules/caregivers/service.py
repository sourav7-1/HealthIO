"""Caregivers service: permission-based access for family members and carers.

A patient (or a guardian holding MANAGE_CAREGIVERS) invites an existing user with an
explicit list of scopes. The invitation grants nothing until the caregiver accepts.
Scopes are stored one row per scope; changes revoke rows instead of deleting them, so
the history of who could see what is kept.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.caregivers.models import (
    CaregiverPermission,
    CaregiverPermissionScope,
    CaregiverRelationship,
    CaregiverRelationshipType,
    CaregiverStatus,
)

GUARDIAN_ONLY_SCOPES = frozenset({CaregiverPermissionScope.MANAGE_CAREGIVERS})


@dataclass(frozen=True)
class ActiveGrant:
    relationship_id: uuid.UUID
    is_guardian: bool
    scopes: frozenset[CaregiverPermissionScope]


async def active_grant(
    session: AsyncSession, *, caregiver_user_id: uuid.UUID, patient_id: uuid.UUID, now: datetime
) -> ActiveGrant | None:
    rel = await session.scalar(
        select(CaregiverRelationship).where(
            CaregiverRelationship.caregiver_user_id == caregiver_user_id,
            CaregiverRelationship.patient_id == patient_id,
            CaregiverRelationship.status == CaregiverStatus.ACTIVE,
            or_(
                CaregiverRelationship.expires_at.is_(None),
                CaregiverRelationship.expires_at > now,
            ),
        )
    )
    if rel is None:
        return None
    scopes = await session.scalars(
        select(CaregiverPermission.scope).where(
            CaregiverPermission.relationship_id == rel.id,
            CaregiverPermission.revoked_at.is_(None),
        )
    )
    return ActiveGrant(rel.id, rel.is_guardian, frozenset(scopes.all()))


def _validate_scopes(scopes: set[CaregiverPermissionScope], *, is_guardian: bool) -> None:
    if not scopes:
        raise ValidationFailedError("Grant at least one permission.")
    if not is_guardian and scopes & GUARDIAN_ONLY_SCOPES:
        raise ValidationFailedError("Only a guardian can manage other caregivers.")


@dataclass(frozen=True)
class GrantRequest:
    caregiver_user_id: uuid.UUID
    relationship_type: CaregiverRelationshipType
    scopes: set[CaregiverPermissionScope]
    is_guardian: bool = False
    guardian_basis: str | None = None
    expires_at: datetime | None = None


async def invite(
    session: AsyncSession, *, patient_id: uuid.UUID, granted_by: uuid.UUID, req: GrantRequest
) -> CaregiverRelationship:
    if req.caregiver_user_id == granted_by:
        raise ValidationFailedError("You cannot add yourself as a caregiver.")
    if req.is_guardian and not req.guardian_basis:
        raise ValidationFailedError("A guardian needs a stated basis, e.g. 'parent of minor'.")
    if req.expires_at is not None and req.expires_at <= datetime.now(UTC):
        raise ValidationFailedError("Expiry must be in the future.")
    _validate_scopes(req.scopes, is_guardian=req.is_guardian)

    existing = await session.scalar(
        select(CaregiverRelationship.id).where(
            CaregiverRelationship.caregiver_user_id == req.caregiver_user_id,
            CaregiverRelationship.patient_id == patient_id,
            CaregiverRelationship.status.in_([CaregiverStatus.INVITED, CaregiverStatus.ACTIVE]),
        )
    )
    if existing is not None:
        raise ConflictError("This person already has a pending or active caregiver link.")

    rel = CaregiverRelationship(
        patient_id=patient_id,
        caregiver_user_id=req.caregiver_user_id,
        relationship_type=req.relationship_type,
        is_guardian=req.is_guardian,
        guardian_basis=req.guardian_basis,
        status=CaregiverStatus.INVITED,
        invited_by=granted_by,
        expires_at=req.expires_at,
        created_by=granted_by,
        updated_by=granted_by,
    )
    session.add(rel)
    await session.flush()
    for scope in sorted(req.scopes):
        session.add(
            CaregiverPermission(
                patient_id=patient_id,
                relationship_id=rel.id,
                scope=scope,
                granted_by=granted_by,
                created_by=granted_by,
                updated_by=granted_by,
            )
        )
    await session.flush()
    return rel


async def _relationship_for_update(
    session: AsyncSession, relationship_id: uuid.UUID
) -> CaregiverRelationship | None:
    row: CaregiverRelationship | None = await session.scalar(
        select(CaregiverRelationship)
        .where(CaregiverRelationship.id == relationship_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return row


async def accept(
    session: AsyncSession, *, relationship_id: uuid.UUID, caregiver_user_id: uuid.UUID
) -> CaregiverRelationship:
    rel = await _relationship_for_update(session, relationship_id)
    # Someone else's invitation looks exactly like a missing one.
    if rel is None or rel.caregiver_user_id != caregiver_user_id:
        raise NotFoundError()
    if rel.status != CaregiverStatus.INVITED:
        raise ConflictError("This invitation is no longer open.")
    now = datetime.now(UTC)
    if rel.expires_at is not None and rel.expires_at <= now:
        raise ConflictError("This invitation has expired.")
    rel.status = CaregiverStatus.ACTIVE
    rel.accepted_at = now
    rel.updated_by = caregiver_user_id
    await session.flush()
    return rel


async def get_relationship(
    session: AsyncSession, *, patient_id: uuid.UUID, relationship_id: uuid.UUID
) -> CaregiverRelationship:
    rel = await session.scalar(
        select(CaregiverRelationship).where(
            CaregiverRelationship.id == relationship_id,
            CaregiverRelationship.patient_id == patient_id,
        )
    )
    if rel is None:
        raise NotFoundError()
    return rel


async def set_scopes(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    relationship_id: uuid.UUID,
    scopes: set[CaregiverPermissionScope],
    changed_by: uuid.UUID,
) -> tuple[set[CaregiverPermissionScope], set[CaregiverPermissionScope]]:
    """Replace the active scope set. Returns (added, removed)."""
    rel = await get_relationship(session, patient_id=patient_id, relationship_id=relationship_id)
    if rel.status not in (CaregiverStatus.INVITED, CaregiverStatus.ACTIVE):
        raise ConflictError("This caregiver link is no longer active.")
    if rel.caregiver_user_id == changed_by:
        raise ForbiddenError("Caregivers cannot change their own permissions.")
    _validate_scopes(scopes, is_guardian=rel.is_guardian)

    current = set(
        (
            await session.scalars(
                select(CaregiverPermission.scope).where(
                    CaregiverPermission.relationship_id == rel.id,
                    CaregiverPermission.revoked_at.is_(None),
                )
            )
        ).all()
    )
    added, removed = scopes - current, current - scopes
    now = datetime.now(UTC)
    if removed:
        await session.execute(
            update(CaregiverPermission)
            .where(
                CaregiverPermission.relationship_id == rel.id,
                CaregiverPermission.scope.in_(removed),
                CaregiverPermission.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_by=changed_by, updated_by=changed_by)
            .execution_options(synchronize_session=False)
        )
    for scope in sorted(added):
        session.add(
            CaregiverPermission(
                patient_id=patient_id,
                relationship_id=rel.id,
                scope=scope,
                granted_by=changed_by,
                created_by=changed_by,
                updated_by=changed_by,
            )
        )
    await session.flush()
    return added, removed


async def revoke(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    relationship_id: uuid.UUID,
    revoked_by: uuid.UUID,
    reason: str | None,
) -> CaregiverRelationship:
    rel = await get_relationship(session, patient_id=patient_id, relationship_id=relationship_id)
    if rel.status not in (CaregiverStatus.INVITED, CaregiverStatus.ACTIVE):
        raise ConflictError("This caregiver link is already closed.")
    now = datetime.now(UTC)
    rel.status = CaregiverStatus.REVOKED
    rel.revoked_at = now
    rel.revoked_by = revoked_by
    rel.revoke_reason = reason
    rel.updated_by = revoked_by
    await session.execute(
        update(CaregiverPermission)
        .where(
            CaregiverPermission.relationship_id == rel.id,
            CaregiverPermission.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=revoked_by, updated_by=revoked_by)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return rel


@dataclass(frozen=True)
class CaregiverLink:
    relationship_id: uuid.UUID
    patient_id: uuid.UUID
    caregiver_user_id: uuid.UUID
    relationship_type: CaregiverRelationshipType
    is_guardian: bool
    status: CaregiverStatus
    expires_at: datetime | None
    scopes: frozenset[CaregiverPermissionScope]


async def _with_scopes(
    session: AsyncSession, rels: list[CaregiverRelationship]
) -> list[CaregiverLink]:
    if not rels:
        return []
    rows = await session.execute(
        select(CaregiverPermission.relationship_id, CaregiverPermission.scope).where(
            CaregiverPermission.relationship_id.in_([r.id for r in rels]),
            CaregiverPermission.revoked_at.is_(None),
        )
    )
    scopes: dict[uuid.UUID, set[CaregiverPermissionScope]] = {}
    for rid, scope in rows:
        scopes.setdefault(rid, set()).add(scope)
    return [
        CaregiverLink(
            r.id,
            r.patient_id,
            r.caregiver_user_id,
            r.relationship_type,
            r.is_guardian,
            r.status,
            r.expires_at,
            frozenset(scopes.get(r.id, set())),
        )
        for r in rels
    ]


async def links_for_patient(session: AsyncSession, patient_id: uuid.UUID) -> list[CaregiverLink]:
    rels = await session.scalars(
        select(CaregiverRelationship)
        .where(
            CaregiverRelationship.patient_id == patient_id,
            CaregiverRelationship.status.in_([CaregiverStatus.INVITED, CaregiverStatus.ACTIVE]),
        )
        .order_by(CaregiverRelationship.created_at)
    )
    return await _with_scopes(session, list(rels.all()))


async def links_for_caregiver(
    session: AsyncSession, caregiver_user_id: uuid.UUID
) -> list[CaregiverLink]:
    rels = await session.scalars(
        select(CaregiverRelationship)
        .where(
            CaregiverRelationship.caregiver_user_id == caregiver_user_id,
            CaregiverRelationship.status.in_([CaregiverStatus.INVITED, CaregiverStatus.ACTIVE]),
        )
        .order_by(CaregiverRelationship.created_at)
    )
    return await _with_scopes(session, list(rels.all()))
