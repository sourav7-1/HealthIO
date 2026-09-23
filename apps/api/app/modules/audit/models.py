"""Audit log: the legal record of who did what to which patient's data.

Separate from application logs. Append-only (UPDATE/DELETE blocked by trigger and
revoked from the application role), and tamper-evident: each row stores
hash = SHA-256(prev_hash ‖ canonical content), chained in `seq` order.
Never store PHI values here, only identifiers and changed field *names*.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.ids import uuid7
from app.core.models import PATIENTS_ID, Base, str_enum, user_fk


class AuditOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    ERROR = "error"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("uq_audit_logs_seq", "seq", unique=True),
        CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name="hash_hex"),
        CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name="prev_hash_hex"),
        Index("ix_audit_logs_patient_occurred", "patient_id", "occurred_at"),
        Index("ix_audit_logs_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_action_occurred", "action", "occurred_at"),
        {
            # Kept identical to the COMMENT set in migration 0002.
            "comment": "Append-only, hash-chained audit trail. "
            "No PHI values: identifiers and changed field names only."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)

    actor_user_id: Mapped[uuid.UUID | None] = user_fk()  # NULL = system
    actor_role: Mapped[str | None] = mapped_column(String(32))  # active role context
    # The patient whose data was touched; for caregiver actions this is who they acted for.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey(PATIENTS_ID, ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "prescription.issue"
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    outcome: Mapped[AuditOutcome] = mapped_column(str_enum(AuditOutcome), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))  # policy decision reason
    changed_fields: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default="{}"
    )
    # Free-text justification (break-glass, overrides) may contain PHI: encrypted.
    justification: Mapped[str | None] = mapped_column(EncryptedString("audit_logs.justification"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)  # truncated (/24 or /48) by the writer
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
