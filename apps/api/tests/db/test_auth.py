"""Authentication: registration, login, lockout, tokens, logout, verification, reset."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import Role
from app.core.mail import MemoryMailer
from app.modules.audit.models import AuditLog
from app.modules.identity.models import AuthSession, User, UserStatus
from app.modules.identity.security import TokenSigner
from tests.db.conftest import TEST_PASSWORD, Builder, login

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"
CSRF = {"X-Requested-With": "healthio"}
STRONG = "a long passphrase for tests"


def outbox(api_app: Any) -> list[Any]:
    mailer: MemoryMailer = api_app.state.mailer
    return mailer.outbox


def last_token(api_app: Any, purpose: str) -> str:
    mail = next(m for m in reversed(outbox(api_app)) if m.meta.get("purpose") == purpose)
    token: str = mail.meta["token"]
    return token


async def audit_actions(session: AsyncSession) -> list[tuple[str, str, str | None]]:
    rows = await session.execute(
        select(AuditLog.action, AuditLog.outcome, AuditLog.reason_code).order_by(AuditLog.seq)
    )
    return [(a, o.value, r) for a, o, r in rows]


# --- registration and email verification ---------------------------------------------


async def test_register_verify_login_flow(api: Any, api_app: Any, session: AsyncSession) -> None:
    resp = await api.post(
        f"{AUTH}/register",
        json={
            "email": "New.User@Example.com",
            "password": STRONG,
            "display_name": "New",
            "role": "patient",
        },
    )
    assert resp.status_code == 202

    # Signing in before verification works, but nothing beyond /me and auth routes does.
    headers = await login(api, "new.user@example.com", STRONG)
    me = (await api.get("/api/v1/me", headers=headers)).json()
    assert me["status"] == "pending_verification"
    assert me["email_verified"] is False
    assert me["roles"] == ["patient"]
    assert me["patient_profile_id"] is not None
    blocked = await api.get(f"/api/v1/patients/{me['patient_profile_id']}/access", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["type"].endswith("/email-unverified")

    token = last_token(api_app, "email_verification")
    assert (await api.post(f"{AUTH}/email/verify", json={"token": token})).status_code == 204
    # Single use.
    again = await api.post(f"{AUTH}/email/verify", json={"token": token})
    assert again.status_code == 400

    allowed = await api.get(f"/api/v1/patients/{me['patient_profile_id']}/access", headers=headers)
    assert allowed.status_code == 200
    assert "self" in allowed.json()["via"]


async def test_verification_link_carries_token_in_fragment(api: Any, api_app: Any) -> None:
    await api.post(
        f"{AUTH}/register",
        json={
            "email": "frag@example.com",
            "password": STRONG,
            "display_name": "F",
            "role": "caregiver",
        },
    )
    mail = outbox(api_app)[-1]
    assert f"#token={mail.meta['token']}" in mail.text  # never in the query string
    assert "?token=" not in mail.text


async def test_duplicate_registration_does_not_reveal_the_account(
    api: Any, api_app: Any, session: AsyncSession
) -> None:
    body = {"email": "dup@example.com", "password": STRONG, "display_name": "D", "role": "patient"}
    first = await api.post(f"{AUTH}/register", json=body)
    second = await api.post(f"{AUTH}/register", json={**body, "display_name": "Other"})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert outbox(api_app)[-1].meta["purpose"] == "register_existing"
    count = await session.scalar(
        text("SELECT count(*) FROM users WHERE display_name IN ('D','Other')")
    )
    assert count == 1


async def test_weak_passwords_are_rejected(api: Any) -> None:
    for weak in ["short", "password123", "aaaaaaaaaaaa", "myname-weakling-9"]:
        resp = await api.post(
            f"{AUTH}/register",
            json={
                "email": "myname@example.com",
                "password": weak,
                "display_name": "M",
                "role": "patient",
            },
        )
        assert resp.status_code == 422, weak
        assert weak not in resp.text  # the password is never echoed


async def test_admin_role_cannot_be_self_registered(api: Any) -> None:
    resp = await api.post(
        f"{AUTH}/register",
        json={"email": "x@example.com", "password": STRONG, "display_name": "X", "role": "admin"},
    )
    assert resp.status_code == 422


async def test_passwords_and_identifiers_are_never_stored_in_plaintext(
    api: Any, session: AsyncSession
) -> None:
    await api.post(
        f"{AUTH}/register",
        json={
            "email": "stored@example.com",
            "password": STRONG,
            "display_name": "S",
            "role": "patient",
        },
    )
    row = (
        await session.execute(
            text("SELECT password_hash, email FROM users WHERE display_name = 'S'")
        )
    ).one()
    assert row.password_hash.startswith("$argon2id$")
    assert STRONG not in row.password_hash
    assert "stored@example.com" not in row.email  # encrypted column


# --- login ---------------------------------------------------------------------------


async def test_valid_login_issues_tokens_and_httponly_refresh_cookie(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    resp = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 600
    assert "refresh" not in resp.text  # the refresh token is only in the cookie
    cookie = resp.headers["set-cookie"]
    assert "hio_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie

    me = await api.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)
    assert ("auth.login", "allowed", None) in await audit_actions(session)


async def test_invalid_login_is_generic_and_audited(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    wrong = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": "wrong password!"}
    )
    unknown = await api.post(
        f"{AUTH}/login", json={"email": "nobody@example.com", "password": "wrong password!"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["type"] == unknown.json()["type"]
    assert wrong.json()["title"] == unknown.json()["title"]
    actions = await audit_actions(session)
    assert ("auth.login", "denied", "bad_password") in actions
    assert ("auth.login", "denied", "unknown_account") in actions


async def test_repeated_failures_lock_the_account(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    for _ in range(5):
        resp = await api.post(
            f"{AUTH}/login", json={"email": user.test_email, "password": "nope nope nope"}
        )
        assert resp.status_code == 401
    # Locked: even the right password fails, with the same generic error.
    locked = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": TEST_PASSWORD}
    )
    assert locked.status_code == 401
    assert locked.json()["type"].endswith("/invalid-credentials")
    actions = await audit_actions(session)
    assert ("auth.lockout", "denied", "too_many_failures") in actions
    assert ("auth.login", "denied", "locked") in actions

    await session.execute(
        text("UPDATE users SET locked_until = now() - interval '1 second' WHERE id = :id"),
        {"id": user.id},
    )
    await login(api, user.test_email)


async def test_login_rate_limit_per_ip(api: Any, api_app: Any) -> None:
    api_app.state.settings.login_rate_limit_per_minute = 3
    codes = [
        (
            await api.post(f"{AUTH}/login", json={"email": "r@example.com", "password": "x" * 12})
        ).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]


async def test_disabled_account_revealed_only_with_correct_password(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    user.status = UserStatus.SUSPENDED
    await session.flush()
    wrong = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": "wrong password!"}
    )
    right = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": TEST_PASSWORD}
    )
    assert wrong.status_code == 401
    assert right.status_code == 403
    assert right.json()["type"].endswith("/account-disabled")


# --- tokens, refresh, logout ---------------------------------------------------------


async def test_refresh_rotates_and_reuse_revokes_the_session(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    await login(api, user.test_email)
    old_cookie = api.cookies.get("hio_refresh")

    rotated = await api.post(f"{AUTH}/refresh", headers=CSRF)
    assert rotated.status_code == 200
    new_access = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
    assert api.cookies.get("hio_refresh") != old_cookie
    assert (await api.get("/api/v1/me", headers=new_access)).status_code == 200

    # An attacker replays the old (already used) refresh token.
    api.cookies.set("hio_refresh", old_cookie, path="/api/v1/auth")
    replay = await api.post(f"{AUTH}/refresh", headers=CSRF)
    assert replay.status_code == 401

    # The whole session is dead, including the legitimately rotated access token.
    assert (await api.get("/api/v1/me", headers=new_access)).status_code == 401
    revoked = await session.scalar(
        select(AuthSession.revoke_reason).where(AuthSession.user_id == user.id)
    )
    assert revoked is not None
    assert revoked.value == "refresh_reuse"
    assert ("auth.refresh_reuse_detected", "denied", "refresh_token_reuse") in await audit_actions(
        session
    )


async def test_refresh_requires_csrf_header_and_trusted_origin(
    api: Any, make_account: Builder
) -> None:
    user = await make_account(Role.PATIENT)
    await login(api, user.test_email)
    assert (await api.post(f"{AUTH}/refresh")).status_code == 403
    evil = await api.post(f"{AUTH}/refresh", headers={**CSRF, "Origin": "https://evil.example"})
    assert evil.status_code == 403


async def test_logout_invalidates_access_token_immediately(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    headers = await login(api, user.test_email)
    assert (await api.post(f"{AUTH}/logout", headers=headers)).status_code == 204
    after = await api.get("/api/v1/me", headers=headers)
    assert after.status_code == 401
    assert after.headers["www-authenticate"].startswith("Bearer")
    assert (await api.post(f"{AUTH}/refresh", headers=CSRF)).status_code == 401
    assert ("auth.logout", "allowed", None) in await audit_actions(session)


async def test_logout_all_ends_every_device(api: Any, make_account: Builder) -> None:
    user = await make_account(Role.PATIENT)
    phone = await login(api, user.test_email)
    laptop = await login(api, user.test_email)
    sessions = (await api.get(f"{AUTH}/sessions", headers=laptop)).json()
    assert len(sessions) == 2
    assert sum(s["current"] for s in sessions) == 1
    assert (await api.post(f"{AUTH}/logout-all", headers=laptop)).status_code == 204
    assert (await api.get("/api/v1/me", headers=phone)).status_code == 401
    assert (await api.get("/api/v1/me", headers=laptop)).status_code == 401


async def test_forged_expired_and_malformed_tokens_are_rejected(
    api: Any, api_app: Any, make_account: Builder, api_settings: Settings
) -> None:
    user = await make_account(Role.PATIENT)
    headers = await login(api, user.test_email)
    good = headers["Authorization"].split()[1]
    signer: TokenSigner = api_app.state.token_signer
    claims = signer.verify(good)

    forger = TokenSigner(
        api_settings.model_copy(
            update={
                "jwt_signing_keys": {"devjwt1": SecretStr(base64.b64encode(b"x" * 32).decode())}
            }
        )
    )
    expired = signer.issue(user.id, claims.session_id, now=datetime.now(UTC) - timedelta(hours=1))
    header, payload, sig = good.split(".")
    tampered = f"{header}.{payload}.{sig[:-4]}AAAA"
    none_alg = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        + f".{payload}."
    )
    for bad in [forger.issue(user.id, claims.session_id), expired, tampered, none_alg, "garbage"]:
        resp = await api.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401, bad
    assert (await api.get("/api/v1/me")).status_code == 401


# --- password reset ------------------------------------------------------------------


async def test_password_reset_flow(
    api: Any, api_app: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    old_session = await login(api, user.test_email)
    sent_before = len(outbox(api_app))

    unknown = await api.post(f"{AUTH}/password/reset-request", json={"email": "ghost@example.com"})
    known = await api.post(f"{AUTH}/password/reset-request", json={"email": user.test_email})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert len(outbox(api_app)) == sent_before + 1  # only the real account got mail

    token = last_token(api_app, "password_reset")
    weak = await api.post(f"{AUTH}/password/reset", json={"token": token, "new_password": "short"})
    assert weak.status_code == 422
    done = await api.post(f"{AUTH}/password/reset", json={"token": token, "new_password": STRONG})
    assert done.status_code == 204
    reused = await api.post(f"{AUTH}/password/reset", json={"token": token, "new_password": STRONG})
    assert reused.status_code == 400

    assert (await api.get("/api/v1/me", headers=old_session)).status_code == 401  # signed out
    bad = await api.post(
        f"{AUTH}/login", json={"email": user.test_email, "password": TEST_PASSWORD}
    )
    assert bad.status_code == 401
    await login(api, user.test_email, STRONG)
    assert outbox(api_app)[-1].meta["purpose"] == "password_changed"
    changed = await session.scalar(select(User.password_changed_at).where(User.id == user.id))
    assert changed is not None


async def test_expired_reset_token_is_rejected(
    api: Any, api_app: Any, make_account: Builder, session: AsyncSession
) -> None:
    user = await make_account(Role.PATIENT)
    await api.post(f"{AUTH}/password/reset-request", json={"email": user.test_email})
    token = last_token(api_app, "password_reset")
    await session.execute(
        text(
            "UPDATE user_action_tokens SET created_at = now() - interval '1 hour', "
            "expires_at = now() - interval '1 minute' WHERE user_id = :id"
        ),
        {"id": user.id},
    )
    resp = await api.post(f"{AUTH}/password/reset", json={"token": token, "new_password": STRONG})
    assert resp.status_code == 400
