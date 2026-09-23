"""Reusable authorization dependencies (route policies).

Every route declares exactly one policy (tests/test_route_policies.py):

    public                                   no authentication (core/policies.py)
    authenticated(allow_unverified=False)    any signed-in, active account
    require_roles(Role.ADMIN, ...)           caller holds at least one of the roles
    require_permission(Permission.X)         platform permission derived from roles
    require_patient_permission(Permission.X) caller may do X for the patient in the path

Use them as a parameter to also receive the result:

    @router.get("/patients/{patient_id}/medications")
    async def list_medications(
        access: PatientAccess = Depends(require_patient_permission(Permission.VIEW_MEDICATIONS)),
    ): ...

Denials are written to the audit log. For patient data, a caller with no relationship to
the patient gets 404 (the patient's existence is not revealed); a caller with a
relationship but without the permission gets 403.
"""

import uuid
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import client_info
from app.core.db import get_session
from app.core.enums import Role
from app.core.errors import (
    AppError,
    EmailUnverifiedError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.policies import mark_policy
from app.modules.access.permissions import Permission
from app.modules.access.service import PatientAccess, platform_permissions, resolve_patient_access
from app.modules.audit.models import AuditOutcome
from app.modules.audit.service import AuditEvent, record_event
from app.modules.identity.service import AuthService, Principal

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


def get_auth_service(request: Request, session: AsyncSession = Depends(get_session)) -> AuthService:
    state = request.app.state
    return AuthService(
        session=session,
        settings=state.settings,
        passwords=state.passwords,
        signer=state.token_signer,
        mailer=state.mailer,
        redis=state.redis,
        client=client_info(request),
    )


async def current_principal(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None:
        raise UnauthorizedError(headers={"WWW-Authenticate": "Bearer"})
    principal = await auth.authenticate(credentials.credentials)
    request.state.subject_id = str(principal.user_id)  # per-user rate limiting
    return principal


async def _deny(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    error: AppError,
    *,
    reason: str,
    patient_id: uuid.UUID | None = None,
    requested_id: uuid.UUID | None = None,
) -> AppError:
    info = client_info(request)
    route = request.scope.get("route")
    context = {"method": request.method, "route": getattr(route, "path", request.url.path)}
    if requested_id is not None:  # not a known patient: keep it out of the FK column
        context["requested_patient_id"] = str(requested_id)
    await record_event(
        session,
        AuditEvent(
            action="access.denied",
            outcome=AuditOutcome.DENIED,
            actor_user_id=principal.user_id,
            patient_id=patient_id,
            reason_code=reason,
            request_id=info.request_id,
            ip_address=info.ip,
            user_agent=info.user_agent,
            context=context,
        ),
    )
    await session.commit()
    return error


class Authenticated:
    def __init__(self, *, allow_unverified: bool = False) -> None:
        self.allow_unverified = allow_unverified
        mark_policy(self, "authenticated")

    async def __call__(self, principal: Principal = Depends(current_principal)) -> Principal:
        if not self.allow_unverified and not principal.email_verified:
            raise EmailUnverifiedError()
        return principal


class RequireRoles:
    def __init__(self, *roles: Role) -> None:
        if not roles:
            raise ValueError("require_roles needs at least one role")
        self.roles = frozenset(roles)
        mark_policy(self, "roles:" + ",".join(sorted(r.value for r in roles)))

    async def __call__(
        self,
        request: Request,
        session: AsyncSession = Depends(get_session),
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        if not principal.email_verified:
            raise EmailUnverifiedError()
        if not principal.roles & self.roles:
            raise await _deny(request, session, principal, ForbiddenError(), reason="missing_role")
        return principal


class RequirePermission:
    def __init__(self, permission: Permission) -> None:
        self.permission = permission
        mark_policy(self, f"permission:{permission.value}")

    async def __call__(
        self,
        request: Request,
        session: AsyncSession = Depends(get_session),
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        if not principal.email_verified:
            raise EmailUnverifiedError()
        if self.permission not in platform_permissions(principal):
            raise await _deny(
                request,
                session,
                principal,
                ForbiddenError(),
                reason=f"missing_permission:{self.permission.value}",
            )
        return principal


class RequirePatientPermission:
    """With no permissions listed, any relationship with the patient is enough."""

    def __init__(self, *permissions: Permission, param: str = "patient_id") -> None:
        self.permissions = frozenset(permissions)
        self.param = param
        label = ",".join(sorted(p.value for p in permissions)) or "any_relationship"
        mark_policy(self, "patient:" + label)

    async def __call__(
        self,
        request: Request,
        session: AsyncSession = Depends(get_session),
        principal: Principal = Depends(current_principal),
    ) -> PatientAccess:
        if not principal.email_verified:
            raise EmailUnverifiedError()
        try:
            patient_id = uuid.UUID(str(request.path_params[self.param]))
        except (KeyError, ValueError):
            raise NotFoundError() from None

        access = await resolve_patient_access(session, principal, patient_id)
        if not access.has_relationship:
            raise await _deny(
                request,
                session,
                principal,
                NotFoundError(),
                reason="no_relationship" if access.patient_exists else "unknown_patient",
                patient_id=patient_id if access.patient_exists else None,
                requested_id=None if access.patient_exists else patient_id,
            )
        missing = self.permissions - access.permissions
        if missing:
            raise await _deny(
                request,
                session,
                principal,
                ForbiddenError("You do not have permission to do this for this patient."),
                reason="missing_permission:" + ",".join(sorted(p.value for p in missing)),
                patient_id=patient_id,
            )
        return access


def authenticated(*, allow_unverified: bool = False) -> Any:
    return Authenticated(allow_unverified=allow_unverified)


def require_roles(*roles: Role) -> Any:
    return RequireRoles(*roles)


def require_permission(permission: Permission) -> Any:
    return RequirePermission(permission)


def require_patient_permission(*permissions: Permission, param: str = "patient_id") -> Any:
    if not permissions:
        raise ValueError("require_patient_permission needs at least one permission")
    return RequirePatientPermission(*permissions, param=param)


def require_patient_relationship(*, param: str = "patient_id") -> Any:
    """Any relationship with the patient (self, linked doctor or caregiver)."""
    return RequirePatientPermission(param=param)
