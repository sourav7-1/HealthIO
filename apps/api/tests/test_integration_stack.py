"""Integration tests against the real dev stack (docker-compose.dev.yml).

Run with: HIO_INTEGRATION=1 uv run pytest -m integration
"""

import os
import uuid

import httpx
import pytest
from redis.asyncio import Redis

from app.core.cache import create_redis
from app.core.config import Settings
from app.core.db import Database
from app.core.rate_limit import hit
from app.core.storage import Storage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("HIO_INTEGRATION") != "1", reason="set HIO_INTEGRATION=1"),
]


@pytest.fixture
def live_settings() -> Settings:
    return Settings()


async def test_database_ping(live_settings: Settings) -> None:
    db = Database(live_settings)
    try:
        await db.ping()
    finally:
        await db.dispose()


async def test_redis_rate_limit(live_settings: Settings) -> None:
    redis: Redis = create_redis(live_settings)
    key = f"it-{uuid.uuid4().hex}"
    try:
        results = [(await hit(redis, key, limit=1, window_seconds=60))[0] for _ in range(2)]
        assert results == [True, False]
    finally:
        await redis.aclose()


async def test_presigned_upload_and_download_round_trip(live_settings: Settings) -> None:
    storage = Storage(live_settings)
    await storage.ping()
    key = f"integration/{uuid.uuid4().hex}.txt"
    payload = b"synthetic test file"

    post = storage.presign_upload(key, "text/plain", max_bytes=1024)
    async with httpx.AsyncClient() as client:
        up = await client.post(
            post["url"], data=post["fields"], files={"file": ("f.txt", payload, "text/plain")}
        )
        assert up.status_code == 204, up.text

        down = await client.get(storage.presign_download(key))
        assert down.status_code == 200
        assert down.content == payload

        # The size condition is enforced by the store, not just by the client.
        too_big = storage.presign_upload(f"{key}.big", "text/plain", max_bytes=4)
        rejected = await client.post(
            too_big["url"],
            data=too_big["fields"],
            files={"file": ("f.txt", payload, "text/plain")},
        )
        assert rejected.status_code in (400, 403)

    storage.client.delete_object(Bucket=storage.bucket, Key=key)
