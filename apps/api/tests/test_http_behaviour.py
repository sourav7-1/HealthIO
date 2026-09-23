from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel

from app.core.errors import ConflictError
from app.core.policies import public
from tests.conftest import problem


async def test_unknown_route_is_problem_json(client: AsyncClient) -> None:
    resp = await client.get("/nope")
    assert resp.status_code == 404
    body = problem(resp)
    assert body["type"].endswith("/not-found")
    assert body["instance"] == "/nope"
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_app_error_and_validation_and_crash(app: FastAPI, client: AsyncClient) -> None:
    class Payload(BaseModel):
        phone: str
        age: int

    @app.get("/_conflict", dependencies=[public])
    async def conflict() -> None:
        raise ConflictError("already exists")

    @app.post("/_validate", dependencies=[public])
    async def validate(p: Payload) -> None:
        return None

    @app.get("/_crash", dependencies=[public])
    async def crash() -> None:
        raise RuntimeError("secret internals")

    resp = await client.get("/_conflict")
    assert resp.status_code == 409
    assert problem(resp)["detail"] == "already exists"

    resp = await client.post("/_validate", json={"phone": "9876543210", "age": "old"})
    assert resp.status_code == 422
    body = problem(resp)
    assert body["errors"][0]["loc"] == ["body", "age"]
    assert "9876543210" not in resp.text  # rejected input is never echoed back

    resp = await client.get("/_crash")
    assert resp.status_code == 500
    assert "secret internals" not in resp.text
    assert problem(resp)["title"] == "Internal server error"


async def test_security_headers(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "strict-transport-security" not in resp.headers  # only in production


async def test_request_id_is_propagated_or_generated(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"x-request-id": "abc12345-trace"})
    assert resp.headers["x-request-id"] == "abc12345-trace"

    resp = await client.get("/health", headers={"x-request-id": "bad id\n"})
    assert len(resp.headers["x-request-id"]) == 32


async def test_cors_allows_only_configured_origin(client: AsyncClient) -> None:
    ok = await client.options(
        "/health",
        headers={"origin": "http://localhost:3000", "access-control-request-method": "GET"},
    )
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = await client.options(
        "/health",
        headers={"origin": "https://evil.example", "access-control-request-method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers
