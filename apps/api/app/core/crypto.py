"""Field-level encryption and blind indexes for the most sensitive columns.

Ciphertext format (stored as text):  v1.<key_id>.<base64url(nonce ‖ ciphertext ‖ tag)>

- AES-256-GCM with a random 96-bit nonce per value.
- The column's context ("table.column") is bound as associated data, so a ciphertext
  copied into another column or table fails to decrypt instead of leaking.
- Keys come from a keyring (`HIO_ENCRYPTION_KEYS`, JSON {key_id: base64 key}). New values
  use `HIO_ENCRYPTION_ACTIVE_KEY_ID`; old key IDs stay readable, which allows rotation.
  In production the keyring is loaded from the KMS/secret manager (roadmap Phase 20/24).

Blind indexes (HMAC-SHA-256 with a separate key) allow exact-match lookup and uniqueness
on encrypted identifiers (email, phone, ABHA number) without storing them in plaintext.
"""

import base64
import hashlib
import hmac
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings

_FORMAT_VERSION = "v1"
_NONCE_BYTES = 12


class DecryptionError(Exception):
    pass


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class Keyring:
    def __init__(self, keys: dict[str, bytes], active_key_id: str, blind_index_key: bytes):
        if active_key_id not in keys:
            raise ValueError("active encryption key id is not in the keyring")
        for kid, key in keys.items():
            if len(key) != 32:
                raise ValueError(f"encryption key {kid!r} must be 32 bytes")
            if "." in kid:
                raise ValueError("key ids must not contain '.'")
        if len(blind_index_key) < 32:
            raise ValueError("blind index key must be at least 32 bytes")
        self._ciphers = {kid: AESGCM(key) for kid, key in keys.items()}
        self.active_key_id = active_key_id
        self._bidx_key = blind_index_key

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._ciphers[self.active_key_id].encrypt(
            nonce, plaintext.encode("utf-8"), context.encode("utf-8")
        )
        return f"{_FORMAT_VERSION}.{self.active_key_id}.{_b64e(nonce + sealed)}"

    def decrypt(self, token: str, context: str) -> str:
        try:
            version, kid, payload = token.split(".", 2)
            if version != _FORMAT_VERSION:
                raise DecryptionError("unknown ciphertext version")
            raw = _b64d(payload)
            cipher = self._ciphers[kid]
            return cipher.decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode("utf-8")
            ).decode("utf-8")
        except (ValueError, KeyError, InvalidTag) as exc:
            # Never include the token or plaintext in the error.
            raise DecryptionError(f"cannot decrypt value for {context}") from exc

    def blind_index(self, normalized_value: str, context: str) -> str:
        msg = f"{context}\x00{normalized_value}".encode()
        return hmac.new(self._bidx_key, msg, hashlib.sha256).hexdigest()


@lru_cache
def get_keyring() -> Keyring:
    s = get_settings()
    return Keyring(
        keys={kid: base64.b64decode(v.get_secret_value()) for kid, v in s.encryption_keys.items()},
        active_key_id=s.encryption_active_key_id,
        blind_index_key=base64.b64decode(s.blind_index_key.get_secret_value()),
    )


class EncryptedString(TypeDecorator[str]):
    """A text column encrypted in the application before it reaches PostgreSQL.

    Encrypted columns cannot be filtered, sorted or indexed. Pair with a blind index
    column when exact-match lookup is needed.
    """

    impl = Text
    cache_ok = True

    def __init__(self, context: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.context = context

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return get_keyring().encrypt(value, self.context)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return get_keyring().decrypt(value, self.context)


# --- normalisation for blind indexes -------------------------------------------------

_NON_DIGITS = re.compile(r"\D")


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().lower()


def normalize_phone(phone: str, default_country_code: str = "91") -> str:
    """Normalise to E.164 digits without '+'. Ten-digit numbers get the default code."""
    digits = _NON_DIGITS.sub("", phone)
    if phone.strip().startswith("+"):
        return digits
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return default_country_code + digits
    return digits


def normalize_abha_number(abha: str) -> str:
    return _NON_DIGITS.sub("", abha)


def email_index(email: str) -> str:
    return get_keyring().blind_index(normalize_email(email), "users.email")


def phone_index(phone: str) -> str:
    return get_keyring().blind_index(normalize_phone(phone), "phone")


def abha_index(abha: str) -> str:
    return get_keyring().blind_index(normalize_abha_number(abha), "patients.abha_number")
