"""Reminder preferences per patient (channels, quiet hours, snooze, missed-dose window).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23 22:47:43.026046+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TOUCH_TABLES = ("reminder_preferences",)


def upgrade() -> None:
    op.create_table(
        "reminder_preferences",
        sa.Column("reminders_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("channel_push", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("channel_sms", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("channel_email", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("show_medicine_names", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("quiet_hours_start", sa.Time(), nullable=True),
        sa.Column("quiet_hours_end", sa.Time(), nullable=True),
        sa.Column("default_snooze_minutes", sa.SmallInteger(), server_default="10", nullable=False),
        sa.Column("missed_after_minutes", sa.SmallInteger(), server_default="120", nullable=False),
        sa.Column(
            "notify_caregivers_on_missed", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)",
            name=op.f("ck_reminder_preferences_quiet_hours_pair"),
        ),
        sa.CheckConstraint(
            "default_snooze_minutes IN (5, 10, 15, 30, 60)",
            name=op.f("ck_reminder_preferences_snooze_choice"),
        ),
        sa.CheckConstraint(
            "missed_after_minutes BETWEEN 30 AND 720",
            name=op.f("ck_reminder_preferences_missed_after_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_reminder_preferences_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name=op.f("fk_reminder_preferences_patient_id_patient_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_reminder_preferences_updated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_preferences")),
    )
    op.create_index(
        op.f("ix_reminder_preferences_patient_id"),
        "reminder_preferences",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "uq_reminder_preferences_patient", "reminder_preferences", ["patient_id"], unique=True
    )
    op.execute(
        "CREATE TRIGGER trg_reminder_preferences_touch_updated_at BEFORE UPDATE ON "
        "reminder_preferences FOR EACH ROW EXECUTE FUNCTION hio_touch_updated_at()"
    )


def downgrade() -> None:
    op.drop_index("uq_reminder_preferences_patient", table_name="reminder_preferences")
    op.drop_index(op.f("ix_reminder_preferences_patient_id"), table_name="reminder_preferences")
    op.drop_table("reminder_preferences")
