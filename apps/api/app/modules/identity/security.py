"""Security primitives: password hashing, opaque tokens and signed access tokens.

- Passwords: Argon2id (argon2-cffi). Hashes are upgraded transparently on login when the
  cost parameters change. Plaintext passwords are never stored or logged.
- Opaque tokens (refresh, email verification, password reset): 256 random bits,
  URL-safe; only their SHA-256 hash is stored, so a database leak does not leak tokens.
- Access tokens: JWT signed with Ed25519 (EdDSA), 10-minute lifetime, `kid` header for
  key rotation. They carry identifiers only: no roles, no personal data. Roles and
  session validity are re-checked against the database on every request.
"""

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.core.config import Settings

# --- passwords -----------------------------------------------------------------------


class Passwords:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Verified against when the account does not exist, so response time does not
        # reveal whether an email is registered.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash or self._dummy_hash, password) and (
                password_hash is not None
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)


# --- opaque tokens -------------------------------------------------------------------


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- access tokens -------------------------------------------------------------------

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 (JWT "typ" claim)


class InvalidAccessTokenError(Exception):
    """Raised for any unusable token; the reason is for logs/audit, never the client."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    token_id: str
    expires_at: datetime


class TokenSigner:
    def __init__(self, settings: Settings) -> None:
        self._private: dict[str, Ed25519PrivateKey] = {}
        for kid, seed in settings.jwt_signing_keys.items():
            raw = base64.b64decode(seed.get_secret_value())
            if len(raw) != 32:
                raise ValueError(f"JWT signing key {kid!r} must be a 32-byte Ed25519 seed")
            self._private[kid] = Ed25519PrivateKey.from_private_bytes(raw)
        if settings.jwt_active_key_id not in self._private:
            raise ValueError("active JWT key id is not in jwt_signing_keys")
        self._public: dict[str, Ed25519PublicKey] = {
            kid: key.public_key() for kid, key in self._private.items()
        }
        self._active = settings.jwt_active_key_id
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._ttl = timedelta(seconds=settings.access_token_ttl_seconds)

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": secrets.token_urlsafe(12),
            "typ": ACCESS_TOKEN_TYPE,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
        }
        return jwt.encode(
            claims, self._private[self._active], algorithm="EdDSA", headers={"kid": self._active}
        )

    def verify(self, token: str) -> AccessClaims:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._public.get(kid) if isinstance(kid, str) else None
            if key is None:
                raise InvalidAccessTokenError("unknown key id")
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],  # never accept "none" or HMAC confusion
                audience=self._audience,
                issuer=self._issuer,
                leeway=10,
                options={"require": ["exp", "iat", "nbf", "sub", "sid", "jti", "typ"]},
            )
            if claims["typ"] != ACCESS_TOKEN_TYPE:
                raise InvalidAccessTokenError("wrong token type")
            return AccessClaims(
                user_id=uuid.UUID(claims["sub"]),
                session_id=uuid.UUID(claims["sid"]),
                token_id=claims["jti"],
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise InvalidAccessTokenError(type(exc).__name__) from exc
