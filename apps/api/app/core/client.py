"""Who is calling: client details captured for audit events."""

from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None
    request_id: str | None


def client_info(request: Request) -> ClientInfo:
    # Behind the production proxy, uvicorn --proxy-headers sets request.client from
    # X-Forwarded-For; the header is never trusted directly here.
    return ClientInfo(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )
