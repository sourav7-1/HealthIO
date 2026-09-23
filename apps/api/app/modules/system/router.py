import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.policies import public

router = APIRouter(tags=["system"], dependencies=[public])
log = get_logger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, Literal["ok", "fail"]]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe: checks database, Redis and object storage",
    responses={503: {"model": ReadyResponse}},
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    state = request.app.state
    probes: dict[str, Callable[[], Awaitable[object]]] = {
        "database": state.db.ping,
        "redis": state.redis.ping,
        "storage": state.storage.ping,
    }

    async def run(name: str, probe: Callable[[], Awaitable[object]]) -> Literal["ok", "fail"]:
        try:
            await asyncio.wait_for(probe(), CHECK_TIMEOUT_SECONDS)
            return "ok"
        except Exception as exc:
            log.warning("readiness_check_failed", check=name, error=type(exc).__name__)
            return "fail"

    results = await asyncio.gather(*(run(n, p) for n, p in probes.items()))
    checks = dict(zip(probes, results, strict=True))
    healthy = all(r == "ok" for r in results)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ok" if healthy else "degraded", checks=checks)
