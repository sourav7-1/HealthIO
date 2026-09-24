"""Prescription revisions, follow-up and content hash.

- `revision` numbers the versions of one prescription (1 = original). A correction is a
  new row that supersedes the previous one; issued rows stay frozen (migration 0002).
- `revision_reason` says why a correction was made (required from revision 2).
- `follow_up_on` / `follow_up_instructions` as written by the prescriber.
- `content_sha256` fingerprints the issued content; it is printed on exports so a copy
  can be checked against the record. Set by the service when issuing; not a constraint,
  because prescriptions issued before this migration are frozen and cannot be backfilled.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24 07:00:00+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prescriptions",
        sa.Column("revision", sa.SmallInteger(), server_default="1", nullable=False),
    )
    op.add_column("prescriptions", sa.Column("revision_reason", sa.String(300), nullable=True))
    op.add_column("prescriptions", sa.Column("follow_up_on", sa.Date(), nullable=True))
    op.add_column("prescriptions", sa.Column("follow_up_instructions", sa.Text(), nullable=True))
    op.add_column("prescriptions", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_check_constraint(
        "revision_chain",
        "prescriptions",
        "(revision = 1) = (supersedes_prescription_id IS NULL)",
    )
    op.create_check_constraint(
        "correction_has_reason",
        "prescriptions",
        "revision = 1 OR revision_reason IS NOT NULL",
    )
    op.create_check_constraint(
        "content_sha256_hex",
        "prescriptions",
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    for name in ("content_sha256_hex", "correction_has_reason", "revision_chain"):
        op.drop_constraint(op.f(f"ck_prescriptions_{name}"), "prescriptions", type_="check")
    for col in (
        "content_sha256",
        "follow_up_instructions",
        "follow_up_on",
        "revision_reason",
        "revision",
    ):
        op.drop_column("prescriptions", col)
