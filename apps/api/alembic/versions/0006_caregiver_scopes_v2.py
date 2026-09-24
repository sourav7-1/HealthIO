"""More granular caregiver scopes: prescriptions, visits, adherence, reported health info.

All four are read-only or write only caregiver-labelled information. Clinical writes stay
impossible for caregivers (the check constraint still rejects them).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 06:00:00+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPE_CHECK = "ck_caregiver_permissions_caregiver_permission_scope"
BASE_SCOPES = (
    "view_profile",
    "view_medical_history",
    "view_medications",
    "log_doses",
    "manage_reminders",
    "view_appointments",
    "manage_appointments",
    "view_reports",
    "upload_reports",
    "receive_alerts",
    "use_ai_assistant",
    "manage_emergency_info",
    "manage_caregivers",
)
NEW_SCOPES = ("view_prescriptions", "view_visits", "view_adherence", "report_health_info")


def _set_scope_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f(SCOPE_CHECK), "caregiver_permissions", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        "caregiver_permission_scope", "caregiver_permissions", f"scope IN ({allowed})"
    )


def upgrade() -> None:
    _set_scope_check(BASE_SCOPES + NEW_SCOPES)


def downgrade() -> None:
    # History rows cannot be deleted (append-only by policy); end the grants and map them to
    # the nearest older scope so the constraint can be restored.
    op.execute(
        sa.text(
            "UPDATE caregiver_permissions SET revoked_at = coalesce(revoked_at, now()), "
            "scope = 'view_medications' WHERE scope IN :removed"
        ).bindparams(sa.bindparam("removed", value=list(NEW_SCOPES), expanding=True))
    )
    _set_scope_check(BASE_SCOPES)
