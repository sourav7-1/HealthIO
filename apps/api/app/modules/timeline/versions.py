"""Giving the database trigger a reason for a change.

Every change to a versioned record is copied to `record_versions` by the database. The
reason is passed through the transaction-local setting `hio.change_reason`, which the
trigger reads. Services wrap corrections in `change_reason()`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _set(session: AsyncSession, value: str) -> None:
    await session.execute(select(func.set_config("hio.change_reason", value, True)))


@asynccontextmanager
async def change_reason(session: AsyncSession, reason: str) -> AsyncIterator[None]:
    """Changes flushed inside the block are versioned with `reason`.

    The setting is transaction-local, so a failed block leaves nothing behind once the
    transaction rolls back; on success it is cleared for later changes in the same
    transaction.
    """
    await _set(session, reason.strip()[:300])
    yield
    await session.flush()
    await _set(session, "")
