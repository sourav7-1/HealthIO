"""Fixtures for database integration tests.

A fresh `<dev db>_test` database is created and migrated to head once per session.
Each test runs in a transaction that is rolled back, so tests never see each other's
rows. Expected constraint failures run inside savepoints (see `expect_db_error`).

Test rows use neutral placeholder values only: no realistic medical data.
"""

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.config import Settings
from app.modules.care_team.models import DoctorProfile, DoctorVerificationStatus
from app.modules.identity.models import User, UserStatus
from app.modules.patients.models import PatientProfile
from tests.db import API_ROOT

pytestmark = pytest.mark.integration


def _urls() -> tuple[str, str, str]:
    base = Settings().database_url
    admin = str(base).replace("postgresql+asyncpg", "postgresql")
    test_db = f"{base.path.lstrip('/')}_test" if base.path else "healthio_test"
    test_url = str(base).rsplit("/", 1)[0] + f"/{test_db}"
    return admin, test_db, test_url


def _run_alembic(fn: Callable[[], None]) -> None:
    # Alembic's env.py calls asyncio.run(); run it in its own thread so it never
    # collides with pytest-asyncio's event loop.
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if errors:
        raise errors[0]


def alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def test_db_url() -> str:
    admin_url, test_db, test_url = _urls()

    async def recreate() -> None:
        conn = await asyncpg.connect(admin_url)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{test_db}"')
        finally:
            await conn.close()

    _run_alembic(lambda: asyncio.run(recreate()))
    _run_alembic(lambda: command.upgrade(alembic_config(test_url), "head"))
    return test_url


@pytest.fixture
async def session(test_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield s
        finally:
            await s.close()
            await trans.rollback()
    await engine.dispose()


def sqlstate(exc: DBAPIError) -> str | None:
    orig: Any = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig.__cause__, "sqlstate", None)


@asynccontextmanager
async def expect_db_error(session: AsyncSession, code: str) -> AsyncIterator[None]:
    """Assert the block fails with SQLSTATE `code`; the savepoint keeps the test usable."""
    savepoint = await session.begin_nested()
    with pytest.raises(DBAPIError) as info:  # noqa: PT012 (the caller's block + flush)
        yield
        await session.flush()
    await savepoint.rollback()
    assert sqlstate(info.value) == code, f"expected {code}, got {sqlstate(info.value)}"


# SQLSTATE codes used in assertions.
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
EXCLUSION_VIOLATION = "23P01"
IMMUTABLE = "HI001"
BAD_TRANSITION = "HI002"


# --- minimal builders (placeholders only) --------------------------------------------

Builder = Callable[..., Awaitable[Any]]


@pytest.fixture
def make_user(session: AsyncSession) -> Builder:
    async def build(**overrides: Any) -> User:
        n = uuid.uuid4().hex[:8]
        user = User(
            display_name=f"User {n}",
            email=f"user-{n}@example.com",
            email_bidx=n.ljust(64, "0"),
            status=UserStatus.ACTIVE,
            **overrides,
        )
        session.add(user)
        await session.flush()
        return user

    return build


@pytest.fixture
def make_patient(session: AsyncSession, make_user: Builder) -> Builder:
    async def build(*, dependant: bool = False) -> PatientProfile:
        user = None if dependant else await make_user()
        patient = PatientProfile(
            user_id=user.id if user else None,
            given_name="Placeholder",
            family_name="Patient",
        )
        session.add(patient)
        await session.flush()
        return patient

    return build


@pytest.fixture
def make_doctor(session: AsyncSession, make_user: Builder) -> Builder:
    async def build() -> DoctorProfile:
        user = await make_user()
        admin = await make_user()
        doctor = DoctorProfile(
            user_id=user.id,
            display_name="Dr Placeholder",
            registration_council="Test Council",
            registration_number=uuid.uuid4().hex[:10],
            verification_status=DoctorVerificationStatus.VERIFIED,
            verified_at=datetime.now(UTC),
            verified_by=admin.id,
        )
        session.add(doctor)
        await session.flush()
        return doctor

    return build


# --- HTTP-level fixtures -------------------------------------------------------------

TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def api_settings(test_db_url: str) -> Settings:
    from app.core.config import Environment

    return Settings(
        env=Environment.TEST,
        database_url=test_db_url,
        mail_backend="memory",
        # Cheap Argon2 for tests; production cost is set in Settings defaults.
        argon2_time_cost=1,
        argon2_memory_kib=1024,
    )


@pytest.fixture
async def api_app(api_settings: Settings, session: AsyncSession) -> AsyncIterator[Any]:
    import fakeredis

    from app.core.db import get_session
    from app.main import create_app
    from tests.conftest import FakeProbe

    app = create_app(api_settings)
    app.state.db = FakeProbe()
    app.state.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    app.state.storage = FakeProbe()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session  # every request shares the test's rolled-back transaction

    app.dependency_overrides[get_session] = _session
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def api(api_app: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=api_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def make_account(session: AsyncSession, api_app: Any) -> Builder:
    """A verified, active account with a password, created directly (fast path)."""
    from app.core.crypto import email_index
    from app.core.enums import Role
    from app.modules.identity.models import UserRole

    async def build(*roles: Role, email: str | None = None, dependant_profile: bool = False) -> Any:
        email = email or f"acct-{uuid.uuid4().hex[:10]}@example.com"
        user = User(
            display_name="Placeholder Account",
            email=email,
            email_bidx=email_index(email),
            password_hash=api_app.state.passwords.hash(TEST_PASSWORD),
            status=UserStatus.ACTIVE,
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()
        for role in roles:
            session.add(UserRole(user_id=user.id, role=role))
        if Role.PATIENT in roles:
            session.add(PatientProfile(user_id=user.id, given_name="Placeholder"))
        await session.flush()
        user.test_email = email  # type: ignore[attr-defined]
        return user

    return build


async def login(api: Any, email: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    resp = await api.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
