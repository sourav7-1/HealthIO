"""Caregivers service: permission-based access for family members and carers.

A patient (or a guardian holding MANAGE_CAREGIVERS) invites an existing user with an
explicit list of scopes. The invitation grants nothing until the caregiver accepts.
Scopes are stored one row per scope; changes revoke rows instead of deleting them, so
the history of who could see what is kept.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import func, or_, select, update
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
OPEN_STATUSES = (CaregiverStatus.INVITED, CaregiverStatus.ACTIVE)
MAX_DEPENDANTS_PER_CAREGIVER = 20
ADULT_AGE = 18


class DependantBasis(StrEnum):
    """Why someone may manage another person's health record (their declaration).

    Minors: a parent or legal guardian consents for them (DPDP Act s.9).
    Adults: a lawful guardian or attorney, or the person's own agreement to be helped.
    """

    PARENT_OF_MINOR = "parent_of_minor"
    LEGAL_GUARDIAN_OF_MINOR = "legal_guardian_of_minor"
    COURT_APPOINTED_GUARDIAN = "court_appointed_guardian"
    POWER_OF_ATTORNEY = "power_of_attorney"
    ADULT_CONSENTED = "adult_consented"


MINOR_BASES = frozenset({DependantBasis.PARENT_OF_MINOR, DependantBasis.LEGAL_GUARDIAN_OF_MINOR})


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


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def check_dependant_basis(basis: DependantBasis, date_of_birth: date, today: date) -> None:
    if date_of_birth > today:
        raise ValidationFailedError("Date of birth cannot be in the future.")
    minor = age_on(date_of_birth, today) < ADULT_AGE
    if minor and basis not in MINOR_BASES:
        raise ValidationFailedError(
            "For a child under 18, you must be their parent or legal guardian."
        )
    if not minor and basis in MINOR_BASES:
        raise ValidationFailedError(
            "This person is an adult. Choose how you are authorised to manage their health "
            "record, or ask them to invite you from their own account."
        )


async def create_guardianship(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    guardian_user_id: uuid.UUID,
    relationship_type: CaregiverRelationshipType,
    basis: DependantBasis,
) -> CaregiverRelationship:
    """The creator of a dependant profile becomes its first guardian, active at once, with
    every grantable scope. Clinical writes are not scopes, so they can never be held."""
    count = await session.scalar(
        select(func.count())
        .select_from(CaregiverRelationship)
        .where(
            CaregiverRelationship.caregiver_user_id == guardian_user_id,
            CaregiverRelationship.is_guardian.is_(True),
            CaregiverRelationship.status.in_(OPEN_STATUSES),
        )
    )
    if (count or 0) >= MAX_DEPENDANTS_PER_CAREGIVER:
        raise ConflictError("You already manage the maximum number of people.")
    now = datetime.now(UTC)
    rel = CaregiverRelationship(
        patient_id=patient_id,
        caregiver_user_id=guardian_user_id,
        relationship_type=relationship_type,
        is_guardian=True,
        guardian_basis=basis.value,
        status=CaregiverStatus.ACTIVE,
        invited_by=guardian_user_id,
        accepted_at=now,
        created_by=guardian_user_id,
        updated_by=guardian_user_id,
    )
    session.add(rel)
    await session.flush()
    for scope in sorted(CaregiverPermissionScope):
        session.add(
            CaregiverPermission(
                patient_id=patient_id,
                relationship_id=rel.id,
                scope=scope,
                granted_by=guardian_user_id,
                created_by=guardian_user_id,
                updated_by=guardian_user_id,
            )
        )
    await session.flush()
    return rel


async def _other_active_guardians(session: AsyncSession, rel: CaregiverRelationship) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(CaregiverRelationship)
        .where(
            CaregiverRelationship.patient_id == rel.patient_id,
            CaregiverRelationship.id != rel.id,
            CaregiverRelationship.is_guardian.is_(True),
            CaregiverRelationship.status == CaregiverStatus.ACTIVE,
        )
    )
    return count or 0


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


async def decline(
    session: AsyncSession, *, relationship_id: uuid.UUID, caregiver_user_id: uuid.UUID
) -> CaregiverRelationship:
    rel = await _relationship_for_update(session, relationship_id)
    if rel is None or rel.caregiver_user_id != caregiver_user_id:
        raise NotFoundError()
    if rel.status != CaregiverStatus.INVITED:
        raise ConflictError("This invitation is no longer open.")
    rel.status = CaregiverStatus.DECLINED
    rel.updated_by = caregiver_user_id
    await _end_permissions(session, rel.id, caregiver_user_id, datetime.now(UTC))
    await session.flush()
    return rel


async def leave(
    session: AsyncSession,
    *,
    relationship_id: uuid.UUID,
    caregiver_user_id: uuid.UUID,
    patient_has_login: bool,
) -> CaregiverRelationship:
    """A caregiver stops helping. The last guardian of a dependant cannot leave, or nobody
    could look after that person's record."""
    rel = await _relationship_for_update(session, relationship_id)
    if rel is None or rel.caregiver_user_id != caregiver_user_id:
        raise NotFoundError()
    if rel.status != CaregiverStatus.ACTIVE:
        raise ConflictError("This caregiver link is not active.")
    if (
        rel.is_guardian
        and not patient_has_login
        and not await _other_active_guardians(session, rel)
    ):
        raise ConflictError(
            "You are the only guardian for this person. Add another guardian before leaving."
        )
    await _close(session, rel, caregiver_user_id, "caregiver left")
    return rel


async def _end_permissions(
    session: AsyncSession, relationship_id: uuid.UUID, actor: uuid.UUID, now: datetime
) -> None:
    await session.execute(
        update(CaregiverPermission)
        .where(
            CaregiverPermission.relationship_id == relationship_id,
            CaregiverPermission.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=actor, updated_by=actor)
        .execution_options(synchronize_session=False)
    )


async def _close(
    session: AsyncSession, rel: CaregiverRelationship, actor: uuid.UUID, reason: str | None
) -> None:
    now = datetime.now(UTC)
    rel.status = CaregiverStatus.REVOKED
    rel.revoked_at = now
    rel.revoked_by = actor
    rel.revoke_reason = reason
    rel.updated_by = actor
    await _end_permissions(session, rel.id, actor, now)
    await session.flush()


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
    if rel.status not in OPEN_STATUSES:
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
    if rel.status not in OPEN_STATUSES:
        raise ConflictError("This caregiver link is already closed.")
    if rel.caregiver_user_id == revoked_by:
        raise ForbiddenError("To stop being a caregiver, leave from your caregiver dashboard.")
    await _close(session, rel, revoked_by, reason)
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
    guardian_basis: str | None = None


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
            r.guardian_basis,
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


async def caregiver_user_ids(session: AsyncSession, patient_id: uuid.UUID) -> set[uuid.UUID]:
    """Everyone who has ever been linked as this patient's caregiver (for the activity log)."""
    rows = await session.scalars(
        select(CaregiverRelationship.caregiver_user_id).where(
            CaregiverRelationship.patient_id == patient_id
        )
    )
    return set(rows.all())
