import base64
import os

import pytest
from pydantic import SecretStr

from app.core.config import Environment, Settings
from app.core.crypto import (
    DecryptionError,
    EncryptedString,
    Keyring,
    get_keyring,
    normalize_email,
    normalize_phone,
    phone_index,
)
from app.core.ids import uuid7, uuid7_timestamp_ms


def _key() -> bytes:
    return os.urandom(32)


# --- UUIDv7 --------------------------------------------------------------------------


def test_uuid7_version_variant_and_order() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert all(u.version == 7 for u in ids)
    assert all((u.int >> 62) & 0b11 == 0b10 for u in ids)  # RFC 4122 variant
    assert ids == sorted(ids), "UUIDv7 must be monotonic within a process"
    assert len(set(ids)) == len(ids)


def test_uuid7_embeds_current_time() -> None:
    import time

    before = time.time_ns() // 1_000_000
    ts = uuid7_timestamp_ms(uuid7())
    assert before <= ts <= before + 1000


# --- encryption ----------------------------------------------------------------------


def test_round_trip_and_random_nonce() -> None:
    ring = Keyring({"k1": _key()}, "k1", _key())
    a = ring.encrypt("same text", "t.c")
    b = ring.encrypt("same text", "t.c")
    assert a != b  # random nonce: equal plaintexts are not linkable
    assert a.startswith("v1.k1.")
    assert ring.decrypt(a, "t.c") == "same text"


def test_ciphertext_is_bound_to_its_column() -> None:
    ring = Keyring({"k1": _key()}, "k1", _key())
    token = ring.encrypt("secret", "clinical_notes.body")
    with pytest.raises(DecryptionError):
        ring.decrypt(token, "appointments.reason")


def test_tampering_is_detected_and_error_leaks_nothing() -> None:
    ring = Keyring({"k1": _key()}, "k1", _key())
    token = ring.encrypt("very private", "t.c")
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(DecryptionError) as err:
        ring.decrypt(tampered, "t.c")
    assert "very private" not in str(err.value)
    assert token not in str(err.value)


def test_key_rotation_keeps_old_values_readable() -> None:
    k1, k2, bidx = _key(), _key(), _key()
    old = Keyring({"k1": k1}, "k1", bidx).encrypt("before rotation", "t.c")
    rotated = Keyring({"k1": k1, "k2": k2}, "k2", bidx)
    assert rotated.decrypt(old, "t.c") == "before rotation"
    assert rotated.encrypt("after", "t.c").startswith("v1.k2.")


def test_keyring_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        Keyring({"k1": b"short"}, "k1", _key())
    with pytest.raises(ValueError, match="not in the keyring"):
        Keyring({"k1": _key()}, "k2", _key())


def test_encrypted_string_type_round_trip() -> None:
    col = EncryptedString("x.y")
    stored = col.process_bind_param("hello", None)  # type: ignore[arg-type]
    assert stored is not None
    assert "hello" not in stored
    assert col.process_result_value(stored, None) == "hello"  # type: ignore[arg-type]
    assert col.process_bind_param(None, None) is None  # type: ignore[arg-type]


# --- blind indexes -------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant", ["9876543210", "+91 98765 43210", "09876543210", "+91-9876543210"]
)
def test_phone_normalisation_variants_share_an_index(variant: str) -> None:
    assert normalize_phone(variant) == "919876543210"
    assert phone_index(variant) == phone_index("9876543210")


def test_blind_index_is_keyed_and_context_separated() -> None:
    ring = Keyring({"k1": _key()}, "k1", _key())
    other = Keyring({"k1": _key()}, "k1", _key())
    assert ring.blind_index("a@b.c", "users.email") != ring.blind_index("a@b.c", "other")
    assert ring.blind_index("a@b.c", "users.email") != other.blind_index("a@b.c", "users.email")
    assert normalize_email("  A@B.c ") == "a@b.c"


def test_dev_keyring_loads() -> None:
    assert get_keyring().active_key_id == "dev1"


def test_dev_keys_are_refused_outside_dev() -> None:
    with pytest.raises(ValueError, match="development keys"):
        Settings(env=Environment.PRODUCTION)

    def real() -> SecretStr:
        return SecretStr(base64.b64encode(_key()).decode())

    with pytest.raises(ValueError, match="development keys"):  # dev JWT key alone is enough
        Settings(
            env=Environment.PRODUCTION,
            encryption_keys={"p1": real()},
            encryption_active_key_id="p1",
            blind_index_key=real(),
        )
    ok = Settings(
        env=Environment.PRODUCTION,
        encryption_keys={"p1": real()},
        encryption_active_key_id="p1",
        blind_index_key=real(),
        jwt_signing_keys={"j1": real()},
        jwt_active_key_id="j1",
    )
    assert ok.encryption_active_key_id == "p1"
