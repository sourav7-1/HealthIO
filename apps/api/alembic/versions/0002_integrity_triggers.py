"""Database-enforced integrity: immutable clinical records, append-only audit log,
guarded deletes and `updated_at` maintenance.

These rules hold even for raw SQL or a buggy service. Violations raise SQLSTATE 'HI001'
(immutability) or 'HI002' (invalid status transition), which the API maps to 409.

Hard deletes of protected rows are allowed only inside a transaction that has run
`SET LOCAL hio.allow_hard_delete = 'on'` (reserved for the audited DPDP erasure and
retention workflows). The audit log never allows it.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table with an updated_at column (all except audit_logs) at this revision.
# New tables add the trigger in their own migration (checked by tests/test_schema_rules.py).
TOUCH_TABLES = (
    "allergies",
    "appointments",
    "caregiver_permissions",
    "caregiver_relationships",
    "clinical_notes",
    "consent_records",
    "doctor_patient_relationships",
    "doctor_profiles",
    "doctor_visits",
    "emergency_contacts",
    "emergency_profiles",
    "follow_ups",
    "health_documents",
    "medical_conditions",
    "medical_history_entries",
    "medication_adherence",
    "medication_doses",
    "medication_schedules",
    "medications",
    "notifications",
    "patient_profiles",
    "prescription_items",
    "prescriptions",
    "test_order_items",
    "test_orders",
    "test_reports",
    "test_results",
    "tests",
    "user_roles",
    "users",
)

# Tables whose rows are history and must not be hard-deleted (use status/soft delete).
NO_DELETE_TABLES = (
    "users",
    "patient_profiles",
    "doctor_profiles",
    "doctor_patient_relationships",
    "caregiver_relationships",
    "caregiver_permissions",
    "consent_records",
    "medications",
    "medication_schedules",
    "medical_conditions",
    "allergies",
    "medical_history_entries",
    "health_documents",
)

FUNCTIONS = r"""
CREATE FUNCTION hio_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- Rejects an UPDATE that changes any column not listed in the trigger arguments.
CREATE FUNCTION hio_freeze_columns() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - TG_ARGV) IS DISTINCT FROM (to_jsonb(OLD) - TG_ARGV) THEN
        RAISE EXCEPTION '% row % is immutable in status %', TG_TABLE_NAME, OLD.id, OLD.status
            USING ERRCODE = 'HI001',
                  HINT = 'Create a new version that supersedes this record.';
    END IF;
    RETURN NEW;
END $$;

-- Allows a status change only if 'old>new' is listed in the trigger arguments.
CREATE FUNCTION hio_check_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT (OLD.status || '>' || NEW.status = ANY (TG_ARGV)) THEN
        RAISE EXCEPTION '% row %: status change % -> % is not allowed',
            TG_TABLE_NAME, OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'HI002';
    END IF;
    RETURN NEW;
END $$;

-- Blocks hard deletes unless the erasure/retention workflow has opted in.
CREATE FUNCTION hio_guard_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('hio.allow_hard_delete', true), '') <> 'on' THEN
        RAISE EXCEPTION '% rows are history and cannot be deleted (row %)', TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'HI001',
                  HINT = 'Use status or soft delete; hard deletes are for the erasure workflow.';
    END IF;
    RETURN OLD;
END $$;

-- Audit log: append-only, no exceptions.
CREATE FUNCTION hio_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'HI001';
END $$;

-- Child rows (items, results) are frozen once their parent is frozen.
-- TG_ARGV: [0] parent table, [1] FK column in child, [2] editable parent status.
CREATE FUNCTION hio_guard_child_of_frozen_parent() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_id uuid;
    parent_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        parent_id := (to_jsonb(OLD) ->> TG_ARGV[1])::uuid;
    ELSE
        parent_id := (to_jsonb(NEW) ->> TG_ARGV[1])::uuid;
    END IF;
    EXECUTE format('SELECT status FROM %I WHERE id = $1', TG_ARGV[0])
        INTO parent_status USING parent_id;
    -- Parent gone (cascade delete of a draft) or still editable: allow.
    IF parent_status IS NOT NULL AND parent_status <> TG_ARGV[2] THEN
        RAISE EXCEPTION '% rows cannot change: parent % % is %',
            TG_TABLE_NAME, TG_ARGV[0], parent_id, parent_status
            USING ERRCODE = 'HI001';
    END IF;
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> TG_ARGV[1]) <> (to_jsonb(NEW) ->> TG_ARGV[1]) THEN
        -- Moving a child from a frozen parent is also a change to that parent.
        EXECUTE format('SELECT status FROM %I WHERE id = $1', TG_ARGV[0])
            INTO parent_status USING (to_jsonb(OLD) ->> TG_ARGV[1])::uuid;
        IF parent_status IS NOT NULL AND parent_status <> TG_ARGV[2] THEN
            RAISE EXCEPTION '% rows cannot be moved off frozen % %',
                TG_TABLE_NAME, TG_ARGV[0], OLD.id USING ERRCODE = 'HI001';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;
"""

LIFECYCLE = ("updated_at", "updated_by", "version")


def _args(*cols: str) -> str:
    return ", ".join(f"'{c}'" for c in cols)


TRIGGERS = rf"""
-- Audit log: append-only.
CREATE TRIGGER trg_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION hio_append_only();
CREATE TRIGGER trg_audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION hio_append_only();

-- Clinical notes: frozen once signed; corrections are new notes that supersede.
CREATE TRIGGER trg_clinical_notes_freeze
    BEFORE UPDATE ON clinical_notes
    FOR EACH ROW WHEN (OLD.status <> 'draft')
    EXECUTE FUNCTION hio_freeze_columns({_args("status", *LIFECYCLE)});
CREATE TRIGGER trg_clinical_notes_transition
    BEFORE UPDATE ON clinical_notes
    FOR EACH ROW WHEN (OLD.status <> 'draft' AND OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION hio_check_status_transition(
        'signed>superseded', 'signed>entered_in_error', 'superseded>entered_in_error');
CREATE TRIGGER trg_clinical_notes_guard_delete
    BEFORE DELETE ON clinical_notes
    FOR EACH ROW WHEN (OLD.status <> 'draft')
    EXECUTE FUNCTION hio_guard_delete();

-- Prescriptions: frozen once issued/recorded; only cancellation fields may change.
CREATE TRIGGER trg_prescriptions_freeze
    BEFORE UPDATE ON prescriptions
    FOR EACH ROW WHEN (OLD.status <> 'draft')
    EXECUTE FUNCTION hio_freeze_columns(
        {_args("status", "cancelled_at", "cancelled_by", "cancel_reason", *LIFECYCLE)});
CREATE TRIGGER trg_prescriptions_transition
    BEFORE UPDATE ON prescriptions
    FOR EACH ROW WHEN (OLD.status <> 'draft' AND OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION hio_check_status_transition(
        'issued>cancelled', 'issued>superseded', 'issued>entered_in_error',
        'recorded>superseded', 'recorded>entered_in_error');
CREATE TRIGGER trg_prescriptions_guard_delete
    BEFORE DELETE ON prescriptions
    FOR EACH ROW WHEN (OLD.status <> 'draft')
    EXECUTE FUNCTION hio_guard_delete();
CREATE TRIGGER trg_prescription_items_guard
    BEFORE INSERT OR UPDATE OR DELETE ON prescription_items
    FOR EACH ROW
    EXECUTE FUNCTION hio_guard_child_of_frozen_parent('prescriptions', 'prescription_id', 'draft');

-- Test reports: frozen once verified.
CREATE TRIGGER trg_test_reports_freeze
    BEFORE UPDATE ON test_reports
    FOR EACH ROW WHEN (OLD.status = 'verified')
    EXECUTE FUNCTION hio_freeze_columns({_args("status", *LIFECYCLE)});
CREATE TRIGGER trg_test_reports_transition
    BEFORE UPDATE ON test_reports
    FOR EACH ROW WHEN (OLD.status = 'verified' AND OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION hio_check_status_transition('verified>entered_in_error');
CREATE TRIGGER trg_test_reports_guard_delete
    BEFORE DELETE ON test_reports
    FOR EACH ROW WHEN (OLD.status = 'verified')
    EXECUTE FUNCTION hio_guard_delete();
CREATE TRIGGER trg_test_results_guard
    BEFORE INSERT OR UPDATE OR DELETE ON test_results
    FOR EACH ROW
    EXECUTE FUNCTION hio_guard_child_of_frozen_parent('test_reports', 'report_id', 'pending_review');

-- Consent records: scope is fixed at creation; only the lifecycle may change.
CREATE TRIGGER trg_consent_records_freeze
    BEFORE UPDATE ON consent_records
    FOR EACH ROW
    EXECUTE FUNCTION hio_freeze_columns(
        {
    _args("status", "withdrawn_at", "withdrawn_by", "withdrawal_reason", "updated_at", "updated_by")
});
CREATE TRIGGER trg_consent_records_transition
    BEFORE UPDATE ON consent_records
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION hio_check_status_transition(
        'active>withdrawn', 'active>expired', 'active>superseded');

-- Taken/skipped doses are patient-reported history.
CREATE TRIGGER trg_medication_doses_guard_delete
    BEFORE DELETE ON medication_doses
    FOR EACH ROW WHEN (OLD.status IN ('taken', 'skipped'))
    EXECUTE FUNCTION hio_guard_delete();
"""


def _statements(sql: str) -> list[str]:
    """Split on ';' at line ends, ignoring ';' inside $$-quoted function bodies
    (asyncpg executes one statement at a time)."""
    out: list[str] = []
    buf: list[str] = []
    in_body = False
    for line in sql.splitlines():
        if line.strip().startswith("--") and not in_body:
            continue
        buf.append(line)
        if line.count("$$") % 2 == 1:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";")
            if stmt:
                out.append(stmt)
            buf = []
    if "\n".join(buf).strip():
        raise ValueError("unterminated SQL statement")
    return out


def upgrade() -> None:
    for stmt in _statements(FUNCTIONS):
        op.execute(stmt)
    for table in TOUCH_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_touch_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION hio_touch_updated_at()"
        )
    for table in NO_DELETE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_guard_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION hio_guard_delete()"
        )
    for stmt in _statements(TRIGGERS):
        op.execute(stmt)
    op.execute(
        "COMMENT ON TABLE audit_logs IS 'Append-only, hash-chained audit trail. "
        "No PHI values: identifiers and changed field names only.'"
    )


def downgrade() -> None:
    for fn in (
        "hio_guard_child_of_frozen_parent",
        "hio_append_only",
        "hio_guard_delete",
        "hio_check_status_transition",
        "hio_freeze_columns",
        "hio_touch_updated_at",
    ):
        # CASCADE drops every trigger that uses the function.
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
    op.execute("COMMENT ON TABLE audit_logs IS NULL")
