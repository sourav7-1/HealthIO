"""`patient_request(Permission.X)`: one dependency for patient-scoped endpoints.

It applies the route policy (require_patient_permission) and hands the handler the DB
session, the caller's access and an `audit()` helper, so every read and write of
clinical data is audited the same way:

    @router.get("/patients/{patient_id}/visits")
    async def list_visits(
        patient_id: uuid.UUID, ctx: PatientRequest = patient_request(VIEW_VISITS)
    ):
        rows = await clinical.list_visits(ctx.session, ctx.patient_id)
        await ctx.audit("visit.list")
        await ctx.session.commit()
"""

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import ClientInfo, client_info
from app.core.db import get_session
from app.modules.access.dependencies import RequirePatientPermission
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess
from app.modules.audit.service import record_patient_event


@dataclass(frozen=True)
class PatientRequest:
    access: PatientAccess
    session: AsyncSession
    client: ClientInfo

    @property
    def patient_id(self) -> uuid.UUID:
        return self.access.patient_id

    @property
    def actor_id(self) -> uuid.UUID:
        assert self.access.actor_user_id is not None
        return self.access.actor_user_id

    def allows(self, permission: Permission) -> bool:
        return self.access.allows(permission)

    async def audit(
        self,
        action: str,
        *,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        changed_fields: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        await record_patient_event(
            self.session,
            self.client,
            actor_user_id=self.actor_id,
            patient_id=self.patient_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            changed_fields=changed_fields,
            context={"via": ",".join(sorted(self.access.via)), **(context or {})},
        )


def patient_request(*permissions: Permission) -> Any:
    policy = RequirePatientPermission(*permissions)

    async def dependency(
        request: Request,
        access: PatientAccess = Depends(policy),
        session: AsyncSession = Depends(get_session),
    ) -> PatientRequest:
        return PatientRequest(access=access, session=session, client=client_info(request))

    return Depends(dependency)
