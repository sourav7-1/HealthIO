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
            email=f"user-{n}@example.test",
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
