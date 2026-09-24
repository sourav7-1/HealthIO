"""Caregiver access management.

Managing a patient's caregivers needs MANAGE_CAREGIVERS for that patient (the patient,
or a guardian who was granted it). Accepting an invitation is done by the invited user.
Every change is audited.
"""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import client_info
from app.core.db import get_session
from app.core.enums import Role
from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.access.dependencies import authenticated, require_patient_permission
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.audit.models import AuditLog
from app.modules.audit.service import record_client_event
from app.modules.caregivers import service
from app.modules.caregivers.models import (
    CaregiverPermissionScope,
    CaregiverRelationshipType,
    CaregiverStatus,
)
from app.modules.identity import service as identity
from app.modules.identity.service import Principal
from app.modules.patients import service as patients
from app.modules.patients.models import PatientProfile, SexAtBirth

router = APIRouter(tags=["caregivers"])

_manage = require_patient_permission(Permission.MANAGE_CAREGIVERS)


class InviteCaregiver(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caregiver_email: EmailStr
    relationship_type: CaregiverRelationshipType
    scopes: set[CaregiverPermissionScope] = Field(min_length=1)
    is_guardian: bool = False
    guardian_basis: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None


class SetScopes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: set[CaregiverPermissionScope] = Field(min_length=1)


class CaregiverLinkOut(BaseModel):
    relationship_id: uuid.UUID
    patient_id: uuid.UUID
    caregiver_user_id: uuid.UUID
    caregiver_name: str | None = None
    # Shown to the caregiver so they know whom they help (the patient chose to link them).
    patient_name: str | None = None
    # The patient has no login of their own (a child, or an adult the guardian represents).
    is_dependant: bool = False
    guardian_basis: str | None = None
    relationship_type: CaregiverRelationshipType
    is_guardian: bool
    status: CaregiverStatus
    expires_at: datetime | None
    scopes: list[CaregiverPermissionScope]


def patient_label(p: PatientProfile) -> str:
    return " ".join(x for x in (p.given_name, p.family_name) if x)


def _out(
    link: service.CaregiverLink,
    names: dict[uuid.UUID, str] | None = None,
    profiles: dict[uuid.UUID, PatientProfile] | None = None,
) -> CaregiverLinkOut:
    profile = (profiles or {}).get(link.patient_id)
    return CaregiverLinkOut(
        caregiver_name=(names or {}).get(link.caregiver_user_id),
        patient_name=patient_label(profile) if profile else None,
        is_dependant=profile is not None and profile.user_id is None,
        guardian_basis=link.guardian_basis,
        relationship_id=link.relationship_id,
        patient_id=link.patient_id,
        caregiver_user_id=link.caregiver_user_id,
        relationship_type=link.relationship_type,
        is_guardian=link.is_guardian,
        status=link.status,
        expires_at=link.expires_at,
        scopes=sorted(link.scopes),
    )


@router.get("/patients/{patient_id}/caregivers", response_model=list[CaregiverLinkOut])
async def list_caregivers(
    patient_id: uuid.UUID,
    access: PatientAccess = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> list[CaregiverLinkOut]:
    links = await service.links_for_patient(session, access.patient_id)
    names = await identity.display_names(session, {link.caregiver_user_id for link in links})
    return [_out(link, names) for link in links]


@router.post(
    "/patients/{patient_id}/caregivers",
    response_model=CaregiverLinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_caregiver(
    patient_id: uuid.UUID,
    body: InviteCaregiver,
    request: Request,
    access: PatientAccess = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> CaregiverLinkOut:
    actor = uuid.UUID(request.state.subject_id)
    caregiver_id = await identity.find_user_id_by_email(session, body.caregiver_email)
    if caregiver_id is None:
        raise NotFoundError("No active account uses this email. Ask them to register first.")
    rel = await service.invite(
        session,
        patient_id=access.patient_id,
        granted_by=actor,
        req=service.GrantRequest(
            caregiver_user_id=caregiver_id,
            relationship_type=body.relationship_type,
            scopes=set(body.scopes),
            is_guardian=body.is_guardian,
            guardian_basis=body.guardian_basis,
            expires_at=body.expires_at,
        ),
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.invited",
        actor_user_id=actor,
        patient_id=access.patient_id,
        resource_type="caregiver_relationship",
        resource_id=rel.id,
        context={"scopes": ",".join(sorted(body.scopes)), "guardian": str(body.is_guardian)},
    )
    await session.commit()
    links = await service.links_for_patient(session, access.patient_id)
    return _out(next(link for link in links if link.relationship_id == rel.id))


@router.put(
    "/patients/{patient_id}/caregivers/{relationship_id}/scopes",
    response_model=CaregiverLinkOut,
)
async def set_caregiver_scopes(
    patient_id: uuid.UUID,
    relationship_id: uuid.UUID,
    body: SetScopes,
    request: Request,
    access: PatientAccess = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> CaregiverLinkOut:
    actor = uuid.UUID(request.state.subject_id)
    added, removed = await service.set_scopes(
        session,
        patient_id=access.patient_id,
        relationship_id=relationship_id,
        scopes=set(body.scopes),
        changed_by=actor,
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.scopes_changed",
        actor_user_id=actor,
        patient_id=access.patient_id,
        resource_type="caregiver_relationship",
        resource_id=relationship_id,
        context={"added": ",".join(sorted(added)), "removed": ",".join(sorted(removed))},
    )
    await session.commit()
    links = await service.links_for_patient(session, access.patient_id)
    return _out(next(link for link in links if link.relationship_id == relationship_id))


@router.delete(
    "/patients/{patient_id}/caregivers/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_caregiver(
    patient_id: uuid.UUID,
    relationship_id: uuid.UUID,
    request: Request,
    access: PatientAccess = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> Response:
    actor = uuid.UUID(request.state.subject_id)
    await service.revoke(
        session,
        patient_id=access.patient_id,
        relationship_id=relationship_id,
        revoked_by=actor,
        reason=None,
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.revoked",
        actor_user_id=actor,
        patient_id=access.patient_id,
        resource_type="caregiver_relationship",
        resource_id=relationship_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _caregiver_view(
    session: AsyncSession, links: list[service.CaregiverLink]
) -> list[CaregiverLinkOut]:
    profiles = await patients.get_profiles(session, [link.patient_id for link in links])
    return [_out(link, None, profiles) for link in links]


@router.get("/me/caregiving", response_model=list[CaregiverLinkOut])
async def my_caregiving(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> list[CaregiverLinkOut]:
    """People I care for, and invitations waiting for me."""
    return await _caregiver_view(
        session, await service.links_for_caregiver(session, principal.user_id)
    )


class DependantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    given_name: str = Field(min_length=1, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date
    sex_at_birth: SexAtBirth
    relationship_type: CaregiverRelationshipType
    basis: service.DependantBasis
    timezone: str = "Asia/Kolkata"
    # The user must actively declare they are authorised; recorded in the audit log.
    declaration_accepted: bool

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        if v not in available_timezones():
            raise ValueError("Unknown time zone")
        return v


DEPENDANT_DECLARATION_VERSION = "2026-09-dependant-en"


@router.post("/me/dependants", response_model=CaregiverLinkOut, status_code=status.HTTP_201_CREATED)
async def create_dependant(
    body: DependantIn,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> CaregiverLinkOut:
    """Add someone you look after who does not use Health Io themselves: your child, or an
    adult you are authorised to represent. You become their guardian on the platform."""
    if not body.declaration_accepted:
        raise ValidationFailedError("Confirm that you are authorised to manage their record.")
    service.check_dependant_basis(body.basis, body.date_of_birth, datetime.now(UTC).date())
    profile = await patients.create_dependant_profile(
        session,
        given_name=body.given_name,
        family_name=body.family_name,
        date_of_birth=body.date_of_birth,
        sex_at_birth=body.sex_at_birth,
        timezone=body.timezone,
        created_by=principal.user_id,
    )
    rel = await service.create_guardianship(
        session,
        patient_id=profile.id,
        guardian_user_id=principal.user_id,
        relationship_type=body.relationship_type,
        basis=body.basis,
    )
    await identity.grant_role(
        session, user_id=principal.user_id, role=Role.CAREGIVER, granted_by=principal.user_id
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.dependant_created",
        actor_user_id=principal.user_id,
        patient_id=profile.id,
        resource_type="caregiver_relationship",
        resource_id=rel.id,
        context={"basis": body.basis.value, "declaration": DEPENDANT_DECLARATION_VERSION},
    )
    await session.commit()
    links = await service.links_for_caregiver(session, principal.user_id)
    return (await _caregiver_view(session, [lk for lk in links if lk.relationship_id == rel.id]))[0]


@router.post(
    "/caregiver-invitations/{relationship_id}/decline", status_code=status.HTTP_204_NO_CONTENT
)
async def decline_invitation(
    relationship_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    rel = await service.decline(
        session, relationship_id=relationship_id, caregiver_user_id=principal.user_id
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.declined",
        actor_user_id=principal.user_id,
        patient_id=rel.patient_id,
        resource_type="caregiver_relationship",
        resource_id=rel.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/caregiving/{relationship_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_caregiving(
    relationship_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stop being someone's caregiver. Your access ends immediately."""
    links = {
        lk.relationship_id: lk
        for lk in await service.links_for_caregiver(session, principal.user_id)
    }
    link = links.get(relationship_id)
    if link is None:
        raise NotFoundError()
    profile = await patients.get_live_profile(session, link.patient_id)
    rel = await service.leave(
        session,
        relationship_id=relationship_id,
        caregiver_user_id=principal.user_id,
        patient_has_login=profile is not None and profile.user_id is not None,
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.left",
        actor_user_id=principal.user_id,
        patient_id=rel.patient_id,
        resource_type="caregiver_relationship",
        resource_id=rel.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/caregiver-invitations/{relationship_id}/accept",
    response_model=CaregiverLinkOut,
)
async def accept_invitation(
    relationship_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> CaregiverLinkOut:
    rel = await service.accept(
        session, relationship_id=relationship_id, caregiver_user_id=principal.user_id
    )
    await identity.grant_role(
        session, user_id=principal.user_id, role=Role.CAREGIVER, granted_by=principal.user_id
    )
    await record_client_event(
        session,
        client_info(request),
        action="caregiver.accepted",
        actor_user_id=principal.user_id,
        patient_id=rel.patient_id,
        resource_type="caregiver_relationship",
        resource_id=rel.id,
    )
    await session.commit()
    links = await service.links_for_caregiver(session, principal.user_id)
    return (await _caregiver_view(session, [lk for lk in links if lk.relationship_id == rel.id]))[0]


class CaregiverActivityOut(BaseModel):
    occurred_at: datetime
    caregiver_user_id: uuid.UUID
    caregiver_name: str | None
    action: str
    outcome: str
    resource_type: str | None


ACTIVITY_LIMIT = 100


@router.get(
    "/patients/{patient_id}/caregivers/activity",
    response_model=list[CaregiverActivityOut],
    summary="What caregivers did with this patient's record (from the audit log)",
)
async def caregiver_activity(
    patient_id: uuid.UUID,
    access: PatientAccess = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> list[CaregiverActivityOut]:
    carers = await service.caregiver_user_ids(session, access.patient_id)
    if not carers:
        return []
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.patient_id == access.patient_id, AuditLog.actor_user_id.in_(carers))
            .order_by(AuditLog.seq.desc())
            .limit(ACTIVITY_LIMIT)
        )
    ).all()
    names = await identity.display_names(session, carers)
    return [
        CaregiverActivityOut(
            occurred_at=r.occurred_at,
            caregiver_user_id=r.actor_user_id,
            caregiver_name=names.get(r.actor_user_id),
            action=r.action,
            outcome=r.outcome.value,
            resource_type=r.resource_type,
        )
        for r in rows
        if r.actor_user_id is not None
    ]
