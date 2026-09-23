import fakeredis
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from app.core.policies import public
from app.core.rate_limit import RateLimit, hit
from tests.conftest import problem


async def test_hit_counts_within_window() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    results = [(await hit(redis, "k", limit=2, window_seconds=60))[0] for _ in range(3)]
    assert results == [True, True, False]


async def test_rate_limit_dependency_returns_429(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/_limited", dependencies=[public, Depends(RateLimit("t", per_minute=2))])
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    codes = [(await client.get("/_limited")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    resp = await client.get("/_limited")
    assert problem(resp)["type"].endswith("/rate-limited")
    assert int(resp.headers["retry-after"]) > 0
