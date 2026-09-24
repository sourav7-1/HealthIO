"""Web Push delivery behind a small interface, so the reminder engine does not depend on
a vendor and tests use a fake. Messages carry identifiers and short, privacy-aware text;
tapping one opens the app, where the person is signed in and acts on the dose.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings

PUSH_TTL_SECONDS = 60 * 60  # a reminder older than an hour is no longer useful


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    tag: str  # replaces an earlier notification with the same tag on the device
    url: str  # opened when the notification (or an action) is tapped
    data: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, str]] = field(default_factory=list)

    def payload(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "body": self.body,
                "tag": self.tag,
                "url": self.url,
                "data": self.data,
                "actions": self.actions,
            }
        )


@dataclass(frozen=True)
class Target:
    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class PushResult:
    ok: bool
    gone: bool = False  # the subscription no longer exists; stop using it
    error: str | None = None


class PushSender(Protocol):
    configured: bool

    async def send(self, target: Target, message: PushMessage) -> PushResult: ...


class NullPushSender:
    configured = False

    async def send(self, target: Target, message: PushMessage) -> PushResult:
        return PushResult(ok=False, error="web push not configured")


class WebPushSender:
    configured = True

    def __init__(self, settings: Settings) -> None:
        assert settings.webpush_vapid_private_key is not None
        self.private_key = settings.webpush_vapid_private_key.get_secret_value()
        self.subject = settings.webpush_subject

    async def send(self, target: Target, message: PushMessage) -> PushResult:
        from pywebpush import WebPushException, webpush

        def _send() -> PushResult:
            try:
                webpush(
                    subscription_info={
                        "endpoint": target.endpoint,
                        "keys": {"p256dh": target.p256dh, "auth": target.auth},
                    },
                    data=message.payload(),
                    vapid_private_key=self.private_key,
                    vapid_claims={"sub": self.subject},
                    ttl=PUSH_TTL_SECONDS,
                    timeout=10,
                )
                return PushResult(ok=True)
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                return PushResult(ok=False, gone=status in (404, 410), error=f"push {status}")
            except Exception as exc:  # network errors etc.
                return PushResult(ok=False, error=type(exc).__name__)

        return await asyncio.to_thread(_send)


def build_push_sender(settings: Settings) -> PushSender:
    if settings.webpush_vapid_private_key is not None and settings.webpush_vapid_public_key:
        return WebPushSender(settings)
    return NullPushSender()
