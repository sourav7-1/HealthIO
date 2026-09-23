"""Doctor portal support: clinician-recorded consent and medications; tests ordered by name.

- consent_records.grantor_capacity gains 'clinician_recorded' (consent given in person and
  recorded by the treating clinician, with the notice version shown).
- medications.source gains 'clinician_recorded' (an existing medicine recorded by a doctor).
- test_order_items: `test_name` (as ordered) is required and `test_id` becomes optional,
  so tests can be ordered before a reviewed test catalogue exists.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTOR_OLD = ("self", "guardian", "nominee")
GRANTOR_NEW = (*GRANTOR_OLD, "clinician_recorded")
SOURCE_OLD = ("prescription", "self_reported", "integration")
SOURCE_NEW = ("prescription", "self_reported", "clinician_recorded", "integration")


def _enum_check(table: str, column: str, name: str, values: tuple[str, ...], length: int) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.alter_column(table, column, type_=sa.String(length), existing_nullable=False)
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(name, table, f"{column} IN ({allowed})")


def upgrade() -> None:
    _enum_check("consent_records", "grantor_capacity", "grantor_capacity", GRANTOR_NEW, 18)
    _enum_check("medications", "source", "medication_source", SOURCE_NEW, 18)

    op.add_column("test_order_items", sa.Column("test_name", sa.String(200), nullable=True))
    op.execute(
        "UPDATE test_order_items i SET test_name = t.name FROM tests t WHERE t.id = i.test_id"
    )
    op.alter_column("test_order_items", "test_name", nullable=False)
    op.alter_column("test_order_items", "test_id", existing_type=sa.Uuid(), nullable=True)
    op.create_unique_constraint(
        op.f("uq_test_order_items_order_id_test_name"),
        "test_order_items",
        ["order_id", "test_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_test_order_items_order_id_test_name"), "test_order_items", type_="unique"
    )
    # Items ordered by name only cannot be represented in the old schema.
    op.execute("DELETE FROM test_order_items WHERE test_id IS NULL")
    op.alter_column("test_order_items", "test_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("test_order_items", "test_name")

    op.execute(
        "UPDATE medications SET source = 'self_reported' WHERE source = 'clinician_recorded'"
    )
    _enum_check("medications", "source", "medication_source", SOURCE_OLD, 13)
    # The old schema has no 'clinician_recorded' capacity. Keep the rows (consent history)
    # by relabelling them; the audit log still records who actually recorded each one.
    op.execute("ALTER TABLE consent_records DISABLE TRIGGER trg_consent_records_freeze")
    op.execute(
        "UPDATE consent_records SET grantor_capacity = 'self' "
        "WHERE grantor_capacity = 'clinician_recorded'"
    )
    op.execute("ALTER TABLE consent_records ENABLE TRIGGER trg_consent_records_freeze")
    _enum_check("consent_records", "grantor_capacity", "grantor_capacity", GRANTOR_OLD, 8)
