from collections.abc import AsyncIterator
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Environment, Settings
from app.main import create_app


class FakeProbe:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def ping(self) -> None:
        if self.fail:
            raise ConnectionError("down")

    async def dispose(self) -> None:
        pass


@pytest.fixture
def settings() -> Settings:
    return Settings(env=Environment.TEST, cors_origins=["http://localhost:3000"])


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    app.state.db = FakeProbe()
    app.state.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    app.state.storage = FakeProbe()
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def problem(resp: Any) -> dict[str, Any]:
    assert resp.headers["content-type"] == "application/problem+json"
    body: dict[str, Any] = resp.json()
    return body
