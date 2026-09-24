"""Authentication endpoints. See app/modules/identity/service.py for the security model.

The refresh token never appears in a response body: it lives in an httpOnly, SameSite=Strict
cookie scoped to /api/v1/auth, so page scripts cannot read it. Because the browser sends
that cookie automatically, the refresh endpoint also requires a custom header and a
trusted Origin (CSRF defence).
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.config import Settings
from app.core.errors import ForbiddenError, InvalidTokenError
from app.core.policies import public
from app.core.rate_limit import RateLimit
from app.modules.access.dependencies import authenticated, get_auth_service
from app.modules.access.service import platform_permissions
from app.modules.care_team import service as care_team
from app.modules.identity.schemas import (
    Accepted,
    AccountUpdate,
    EmailRequest,
    LoginRequest,
    MeResponse,
    PasswordChange,
    PasswordChanged,
    PasswordResetConfirm,
    RegisterRequest,
    SessionOut,
    TokenRequest,
    TokenResponse,
)
from app.modules.identity.service import AuthService, IssuedTokens, Principal, get_account
from app.modules.patients import service as patients

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "healthio"

_login_limit = Depends(RateLimit("login", per_minute="login_rate_limit_per_minute"))
_auth_form_limit = Depends(RateLimit("auth-form", per_minute=20))
_refresh_limit = Depends(RateLimit("refresh", per_minute=30))


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_prefix}/auth"


def _set_refresh_cookie(response: Response, settings: Settings, tokens: IssuedTokens) -> None:
    max_age = int((tokens.refresh_expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        settings.refresh_cookie_name,
        tokens.refresh_token,
        max_age=max(max_age, 0),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=_cookie_path(settings),
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _check_csrf(request: Request, settings: Settings) -> None:
    if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
        raise ForbiddenError("Missing anti-CSRF header.")
    origin = request.headers.get("origin")
    trusted = {*settings.cors_origins, settings.public_web_url}
    if origin is not None and origin not in trusted:
        raise ForbiddenError("Untrusted origin.")


def _token_response(response: Response, settings: Settings, tokens: IssuedTokens) -> TokenResponse:
    _set_refresh_cookie(response, settings, tokens)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
    dependencies=[public, _auth_form_limit],
    summary="Create an account (always answers the same way; see the email)",
)
async def register(
    body: RegisterRequest, auth: AuthService = Depends(get_auth_service)
) -> Accepted:
    await auth.register(
        email=body.email,
        password=body.password.get_secret_value(),
        display_name=body.display_name,
        role=body.role,
        registration_council=body.registration_council,
        registration_number=body.registration_number,
    )
    return Accepted(detail="If this email can be registered, we have sent a confirmation link.")


@router.post("/login", response_model=TokenResponse, dependencies=[public, _login_limit])
async def login(
    body: LoginRequest,
    response: Response,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    tokens = await auth.login(email=body.email, password=body.password.get_secret_value())
    return _token_response(response, _settings(request), tokens)


@router.post("/refresh", response_model=TokenResponse, dependencies=[public, _refresh_limit])
async def refresh(
    request: Request, response: Response, auth: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    settings = _settings(request)
    _check_csrf(request, settings)
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise InvalidTokenError()
    try:
        tokens = await auth.refresh(token)
    except InvalidTokenError:
        _clear_refresh_cookie(response, settings)
        raise
    return _token_response(response, settings, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    await auth.logout(principal)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, _settings(request))
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    await auth.logout(principal, everywhere=True)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, _settings(request))
    return response


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> list[SessionOut]:
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.absolute_expires_at,
            current=s.id == principal.session_id,
        )
        for s in await auth.list_sessions(principal)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    await auth.revoke_session(principal, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/email/verification-request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
)
async def request_email_verification(
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> Accepted:
    await auth.request_email_verification(principal)
    return Accepted(detail="If your email is not yet verified, we have sent a new link.")


@router.post(
    "/email/verify",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[public, _auth_form_limit],
)
async def verify_email(
    body: TokenRequest, auth: AuthService = Depends(get_auth_service)
) -> Response:
    await auth.confirm_email(body.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/reset-request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
    dependencies=[public, _auth_form_limit],
)
async def request_password_reset(
    body: EmailRequest, auth: AuthService = Depends(get_auth_service)
) -> Accepted:
    await auth.request_password_reset(body.email)
    return Accepted(detail="If an account exists for this email, we have sent a reset link.")


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[public, _auth_form_limit],
)
async def reset_password(
    body: PasswordResetConfirm, auth: AuthService = Depends(get_auth_service)
) -> Response:
    await auth.confirm_password_reset(body.token, body.new_password.get_secret_value())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/change",
    response_model=PasswordChanged,
    dependencies=[_auth_form_limit],
)
async def change_password(
    body: PasswordChange,
    principal: Principal = Depends(authenticated()),
    auth: AuthService = Depends(get_auth_service),
) -> PasswordChanged:
    """Needs the current password. Every other signed-in device is signed out."""
    count = await auth.change_password(
        principal, body.current_password.get_secret_value(), body.new_password.get_secret_value()
    )
    return PasswordChanged(other_sessions_signed_out=count)


@me_router.patch("/me", response_model=MeResponse)
async def update_me(
    body: AccountUpdate,
    principal: Principal = Depends(authenticated()),
    auth: AuthService = Depends(get_auth_service),
) -> MeResponse:
    await auth.update_account(
        principal,
        display_name=body.display_name,
        timezone=body.timezone,
        preferred_language=body.preferred_language,
    )
    return await me(principal, auth)


@me_router.get("/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(authenticated(allow_unverified=True)),
    auth: AuthService = Depends(get_auth_service),
) -> MeResponse:
    user = await get_account(auth.db, principal.user_id)
    if user is None:
        raise InvalidTokenError()
    doctor = await care_team.doctor_profile_for_user(auth.db, principal.user_id)
    return MeResponse(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        email_verified=principal.email_verified,
        roles=sorted(principal.roles),
        platform_permissions=sorted(p.value for p in platform_permissions(principal)),
        patient_profile_id=await patients.self_profile_id(auth.db, principal.user_id),
        doctor_profile_id=doctor.id if doctor else None,
        doctor_verification_status=doctor.verification_status.value if doctor else None,
        timezone=user.timezone,
        preferred_language=user.preferred_language,
    )
