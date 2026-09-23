"""Append and verify hash-chained audit events.

`record_event` must be called inside the same transaction as the change it describes,
so a change can never be committed without its audit row (ARCHITECTURE.md §11).

Chain writes are serialised with a transaction-scoped advisory lock. That is simple and
correct; if audit volume ever makes it a bottleneck, the chain can be sharded (for
example one chain per patient) without changing the table.
"""

import hashlib
import ipaddress
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client import ClientInfo
from app.core.ids import uuid7
from app.modules.audit.models import AuditLog, AuditOutcome

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 0x48494F_4155444954  # "HIO" "AUDIT"
_HASHED_FIELDS: Sequence[str] = (
    "id",
    "occurred_at",
    "actor_user_id",
    "actor_role",
    "patient_id",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "reason_code",
    "changed_fields",
    "justification",
    "request_id",
    "ip_address",
    "user_agent_hash",
    "context",
)


@dataclass(frozen=True)
class AuditEvent:
    action: str
    outcome: AuditOutcome
    actor_user_id: uuid.UUID | None = None
    actor_role: str | None = None
    patient_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    reason_code: str | None = None
    changed_fields: list[str] = field(default_factory=list)
    justification: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


def truncate_ip(ip: str | None) -> str | None:
    """Keep only the network part (/24 for IPv4, /48 for IPv6): enough for abuse
    investigation, less identifying than a full address."""
    if not ip:
        return None
    addr = ipaddress.ip_address(ip)
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False).network_address)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID | ipaddress.IPv4Address | ipaddress.IPv6Address):
        return str(value)
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def compute_hash(prev_hash: str, row: AuditLog) -> str:
    content = {name: _canonical_value(getattr(row, name)) for name in _HASHED_FIELDS}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


async def record_event(session: AsyncSession, event: AuditEvent) -> AuditLog:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    prev = await session.scalar(select(AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1))
    prev_hash = prev or GENESIS_HASH

    row = AuditLog(
        id=uuid7(),
        # Set in the app (not by the DB default) so it is covered by the hash.
        occurred_at=datetime.now(UTC),
        actor_user_id=event.actor_user_id,
        actor_role=event.actor_role,
        patient_id=event.patient_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        outcome=event.outcome,
        reason_code=event.reason_code,
        changed_fields=sorted(event.changed_fields),
        justification=event.justification,
        request_id=event.request_id,
        ip_address=truncate_ip(event.ip_address),
        user_agent_hash=(
            hashlib.sha256(event.user_agent.encode()).hexdigest() if event.user_agent else None
        ),
        context=event.context,
        prev_hash=prev_hash,
    )
    row.hash = compute_hash(prev_hash, row)
    session.add(row)
    await session.flush()
    return row


async def record_client_event(
    session: AsyncSession,
    client: ClientInfo,
    *,
    action: str,
    actor_user_id: uuid.UUID | None,
    outcome: AuditOutcome = AuditOutcome.ALLOWED,
    patient_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    reason_code: str | None = None,
    changed_fields: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog:
    """Convenience wrapper that fills request details from `ClientInfo`."""
    return await record_event(
        session,
        AuditEvent(
            action=action,
            outcome=outcome,
            actor_user_id=actor_user_id,
            patient_id=patient_id,
            resource_type=resource_type,
            resource_id=resource_id,
            reason_code=reason_code,
            changed_fields=changed_fields or [],
            request_id=client.request_id,
            ip_address=client.ip,
            user_agent=client.user_agent,
            context=context or {},
        ),
    )


async def record_patient_event(
    session: AsyncSession,
    client: ClientInfo,
    *,
    actor_user_id: uuid.UUID | None,
    patient_id: uuid.UUID,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    changed_fields: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog:
    """Every read or write of a patient's clinical data (PROJECT_RULES.md §4)."""
    return await record_client_event(
        session,
        client,
        action=action,
        actor_user_id=actor_user_id,
        patient_id=patient_id,
        resource_type=resource_type,
        resource_id=resource_id,
        changed_fields=changed_fields,
        context=context,
    )


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None


async def verify_chain(session: AsyncSession, batch_size: int = 1000) -> ChainVerification:
    """Recompute every hash in `seq` order. Any edit, deletion or reordering breaks it."""
    prev_hash = GENESIS_HASH
    checked = 0
    last_seq = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.seq > last_seq)
                .order_by(AuditLog.seq)
                .limit(batch_size)
            )
        ).all()
        if not rows:
            return ChainVerification(ok=True, checked=checked)
        for row in rows:
            if row.prev_hash != prev_hash or compute_hash(prev_hash, row) != row.hash:
                return ChainVerification(ok=False, checked=checked, first_bad_seq=row.seq)
            prev_hash = row.hash
            last_seq = row.seq
            checked += 1
