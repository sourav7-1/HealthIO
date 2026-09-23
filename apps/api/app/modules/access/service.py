"""Authorization decisions for patient data.

Effective permissions of a caller for one patient are the union of what each
relationship gives them:

  self       the caller is the patient                      → SELF_PERMISSIONS
  doctor     verified doctor with an ACTIVE relationship     → DOCTOR_PERMISSIONS, narrowed to
                                                               the data categories the patient
                                                               consented to share
  caregiver  ACTIVE, unexpired caregiver link + CAREGIVER role → exactly the granted scopes,
                                                               never NEVER_FOR_CAREGIVERS,
                                                               guardian-only scopes only for
                                                               guardians

Admins get nothing from their role. No relationship at all means no access.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.access.permissions import (
    CAREGIVER_GRANTABLE,
    DOCTOR_PERMISSION_CATEGORY,
    DOCTOR_PERMISSIONS,
    GUARDIAN_ONLY,
    NEVER_FOR_CAREGIVERS,
    ROLE_PERMISSIONS,
    SELF_PERMISSIONS,
    Permission,
)
from app.modules.care_team import service as care_team
from app.modules.caregivers import service as caregivers
from app.modules.consent import service as consent
from app.modules.consent.models import ConsentPurpose
from app.modules.identity.service import Principal
from app.modules.patients import service as patients


@dataclass(frozen=True)
class PatientAccess:
    patient_id: uuid.UUID
    permissions: frozenset[Permission]
    via: frozenset[str]  # "self", "doctor", "caregiver"
    patient_exists: bool = True
    actor_user_id: uuid.UUID | None = None

    @property
    def has_relationship(self) -> bool:
        return bool(self.via)

    def allows(self, permission: Permission) -> bool:
        return permission in self.permissions


def platform_permissions(principal: Principal) -> frozenset[Permission]:
    perms: set[Permission] = set()
    for role in principal.roles:
        perms |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(perms)


async def resolve_patient_access(
    session: AsyncSession,
    principal: Principal,
    patient_id: uuid.UUID,
    now: datetime | None = None,
) -> PatientAccess:
    now = now or datetime.now(UTC)
    profile = await patients.get_live_profile(session, patient_id)
    if profile is None:
        return PatientAccess(
            patient_id,
            frozenset(),
            frozenset(),
            patient_exists=False,
            actor_user_id=principal.user_id,
        )

    perms: set[Permission] = set()
    via: set[str] = set()

    if profile.user_id == principal.user_id and principal.has_role(Role.PATIENT):
        perms |= SELF_PERMISSIONS
        via.add("self")

    if principal.has_role(Role.DOCTOR) and await care_team.has_active_link(
        session, doctor_user_id=principal.user_id, patient_id=patient_id
    ):
        via.add("doctor")
        shared = await consent.shared_categories(
            session,
            patient_id=patient_id,
            grantee_user_id=principal.user_id,
            purpose=ConsentPurpose.CARE_DELIVERY,
            now=now,
        )
        perms |= {p for p in DOCTOR_PERMISSIONS if DOCTOR_PERMISSION_CATEGORY[p] in shared}

    if principal.has_role(Role.CAREGIVER):
        grant = await caregivers.active_grant(
            session, caregiver_user_id=principal.user_id, patient_id=patient_id, now=now
        )
        if grant is not None:
            via.add("caregiver")
            granted = {Permission(s.value) for s in grant.scopes} & CAREGIVER_GRANTABLE
            granted -= NEVER_FOR_CAREGIVERS
            if not grant.is_guardian:
                granted -= GUARDIAN_ONLY
            perms |= granted

    return PatientAccess(
        patient_id, frozenset(perms), frozenset(via), actor_user_id=principal.user_id
    )


def require_actor(access: PatientAccess) -> uuid.UUID:
    assert access.actor_user_id is not None
    return access.actor_user_id
