from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import FakeProbe


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_all_ok(client: AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "storage": "ok"},
    }


async def test_ready_reports_failed_dependency(app: FastAPI, client: AsyncClient) -> None:
    app.state.storage = FakeProbe(fail=True)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["storage"] == "fail"
    assert body["checks"]["database"] == "ok"
