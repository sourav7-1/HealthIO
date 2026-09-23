"""Outgoing email adapters.

`smtp` sends to Mailpit in development (http://localhost:8025) and to the provider in
production. `memory` keeps messages in-process for tests. There is deliberately no
"print to console" backend: auth emails contain single-use tokens, which must never
reach logs.

Emails carry no health information (ARCHITECTURE.md §10). Sending moves to the
notifications queue in Phase 19; this synchronous adapter is the foundation.
"""

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    # Machine-readable purpose + token for tests only; never rendered or logged.
    meta: dict[str, str] = field(default_factory=dict)


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._sender = settings.mail_from

    def _send_sync(self, email: OutgoingEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.text)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.send_message(msg)

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send_sync, email)


class MemoryMailer:
    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        self.outbox.append(email)


def create_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend == "memory":
        return MemoryMailer()
    if settings.mail_backend == "smtp":
        return SmtpMailer(settings)
    raise ValueError(f"unknown mail backend {settings.mail_backend!r}")
