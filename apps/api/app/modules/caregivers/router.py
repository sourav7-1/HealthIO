"""Caregiver access management.

Managing a patient's caregivers needs MANAGE_CAREGIVERS for that patient (the patient,
or a guardian who was granted it). Accepting an invitation is done by the invited user.
Every change is audited.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import client_info
from app.core.db import get_session
from app.core.enums import Role
from app.core.errors import NotFoundError
from app.modules.access.dependencies import authenticated, require_patient_permission
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.audit.service import record_client_event
from app.modules.caregivers import service
from app.modules.caregivers.models import (
    CaregiverPermissionScope,
    CaregiverRelationshipType,
    CaregiverStatus,
)
from app.modules.identity import service as identity
from app.modules.identity.service import Principal

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
    relationship_type: CaregiverRelationshipType
    is_guardian: bool
    status: CaregiverStatus
    expires_at: datetime | None
    scopes: list[CaregiverPermissionScope]


def _out(link: service.CaregiverLink) -> CaregiverLinkOut:
    return CaregiverLinkOut(
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
    return [_out(link) for link in await service.links_for_patient(session, access.patient_id)]


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


@router.get("/me/caregiving", response_model=list[CaregiverLinkOut])
async def my_caregiving(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> list[CaregiverLinkOut]:
    """Patients I care for, and invitations waiting for me."""
    return [_out(link) for link in await service.links_for_caregiver(session, principal.user_id)]


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
    return _out(next(link for link in links if link.relationship_id == rel.id))
