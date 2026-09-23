import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.core.enums import Role
from app.modules.identity.models import UserStatus
from app.modules.identity.service import RegistrationRole


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_Strict):
    email: EmailStr
    # SecretStr keeps the value out of reprs, logs and validation error output.
    password: SecretStr = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    role: RegistrationRole
    # Doctors: registration with the NMC or a State Medical Council (verified by an admin).
    registration_council: str | None = Field(default=None, max_length=120)
    registration_number: str | None = Field(default=None, max_length=64)


class LoginRequest(_Strict):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=256)


class EmailRequest(_Strict):
    email: EmailStr


class TokenRequest(_Strict):
    token: str = Field(min_length=20, max_length=200)


class PasswordResetConfirm(_Strict):
    token: str = Field(min_length=20, max_length=200)
    new_password: SecretStr = Field(min_length=1, max_length=256)


class Accepted(BaseModel):
    detail: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (OAuth token type)
    expires_in: int = Field(description="Access token lifetime in seconds")


class MeResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str | None
    status: UserStatus
    email_verified: bool
    roles: list[Role]
    platform_permissions: list[str]
    patient_profile_id: uuid.UUID | None
    doctor_profile_id: uuid.UUID | None
    doctor_verification_status: str | None


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool
