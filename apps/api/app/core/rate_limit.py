"""Fixed-window rate limiting backed by Redis.

Use as a route dependency: `Depends(RateLimit("otp-send", per_minute=5))`.
The key is the authenticated subject when there is one (Phase 3), else the client IP.
"""

import time

from fastapi import Request
from redis.asyncio import Redis

from app.core.errors import RateLimitedError


async def hit(redis: Redis, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Count one hit. Returns (allowed, seconds_until_reset)."""
    window = int(time.time() // window_seconds)
    bucket = f"rl:{key}:{window}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds)
        count, _ = await pipe.execute()
    reset = window_seconds - int(time.time() % window_seconds)
    return int(count) <= limit, reset


class RateLimit:
    """`per_minute` is a number, or the name of a Settings field (read per request)."""

    def __init__(self, scope: str, *, per_minute: int | str) -> None:
        self.scope = scope
        self.per_minute = per_minute

    async def __call__(self, request: Request) -> None:
        redis: Redis = request.app.state.redis
        limit = (
            getattr(request.app.state.settings, self.per_minute)
            if isinstance(self.per_minute, str)
            else self.per_minute
        )
        subject = getattr(request.state, "subject_id", None) or (
            request.client.host if request.client else "unknown"
        )
        allowed, reset = await hit(redis, f"{self.scope}:{subject}", int(limit), 60)
        if not allowed:
            raise RateLimitedError(headers={"Retry-After": str(reset)})
