"""Record versions: the earlier states of clinical records, written by the database.

A trigger (`hio_record_version`, migration 0011) copies the old row into this table
whenever a tracked record's content changes, so an edit can never be silent, even from
raw SQL. Rows are append-only. Encrypted columns stay encrypted inside `snapshot`: their
ciphertext is bound to "<table>.<column>", which is how the history view decrypts them.
"""

import uuid

from sqlalchemy import Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, Entity, PatientOwned

# Tables whose changes are versioned, and columns whose changes are bookkeeping only
# (not a new version). Signed notes, issued prescriptions and verified reports are frozen
# instead (migration 0002) and corrected by superseding records.
VERSIONED_TABLES: dict[str, tuple[str, ...]] = {
    "medical_conditions": (),
    "allergies": (),
    "medical_history_entries": (),
    "doctor_visits": (),
    "symptom_reports": (),
    "health_documents": ("scan_status", "size_bytes", "sha256"),
    "test_orders": (),
    "test_reports": (),
    "appointments": (),
    "follow_ups": (),
}
BOOKKEEPING = ("updated_at", "updated_by", "version")


class RecordVersion(Base, Entity, PatientOwned):
    __tablename__ = "record_versions"
    __table_args__ = (
        Index(
            "ix_record_versions_patient_record", "patient_id", "table_name", "record_id", "created_at"
        ),
    )

    table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # The version number the old row had (NULL for tables without optimistic locking).
    version: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    changed_columns: Mapped[list[str]] = mapped_column(ARRAY(String(63)), nullable=False)
    # Why the record changed, when the service supplied one (`hio.change_reason`).
    reason: Mapped[str | None] = mapped_column(String(300))
