"""Schema conventions from docs/data-model.md, checked against the model metadata."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import Enum, ForeignKeyConstraint, Index, Table, Uuid
from sqlalchemy.orm import configure_mappers

from app.core.crypto import EncryptedString
from app.models import Base

TABLES: dict[str, Table] = dict(Base.metadata.tables)
NOT_PATIENT_SCOPED = {
    "users",
    "user_roles",
    "auth_sessions",
    "refresh_tokens",
    "user_action_tokens",
    "doctor_profiles",
    "patient_profiles",
    "tests",
}
MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mappers_configure() -> None:
    configure_mappers()
    assert len(TABLES) == 38


def test_every_table_has_uuid_primary_key_named_id() -> None:
    for name, table in TABLES.items():
        pk = list(table.primary_key.columns)
        assert [c.name for c in pk] == ["id"], name
        assert isinstance(pk[0].type, Uuid), name


def test_every_business_table_has_timestamps_and_actors() -> None:
    for name, table in TABLES.items():
        if name == "audit_logs":  # immutable; has occurred_at and actor_user_id instead
            assert {"occurred_at", "actor_user_id"} <= set(table.c.keys())
            continue
        missing = {"created_at", "updated_at", "created_by", "updated_by"} - set(table.c.keys())
        assert not missing, f"{name} is missing {missing}"


def test_patient_owned_tables_are_scoped_and_indexed() -> None:
    for name, table in TABLES.items():
        if name in NOT_PATIENT_SCOPED or name in {"notifications", "audit_logs"}:
            continue
        assert "patient_id" in table.c, f"{name} must carry patient_id"
        assert not table.c.patient_id.nullable, name
        leading = {
            next(iter(ix.expressions)).name  # type: ignore[union-attr]
            for ix in table.indexes
            if isinstance(ix, Index) and ix.expressions and hasattr(ix.expressions[0], "name")
        }
        assert "patient_id" in leading, f"{name} needs an index leading with patient_id"


def test_child_links_between_patient_tables_use_composite_keys() -> None:
    """A single-column FK into a patient-owned table could cross patients."""
    for name, table in TABLES.items():
        for fk in table.foreign_key_constraints:
            target = fk.referred_table.name
            if target in NOT_PATIENT_SCOPED or (target == name and len(fk.columns) > 1):
                continue
            if "patient_id" in fk.referred_table.c:
                cols = [c.name for c in fk.columns]
                assert "patient_id" in cols, f"{name} -> {target} via {cols} is not patient-scoped"
                assert isinstance(fk, ForeignKeyConstraint)


def test_enums_are_check_constrained_varchars() -> None:
    for name, table in TABLES.items():
        for col in table.c:
            if isinstance(col.type, Enum):
                assert col.type.native_enum is False, f"{name}.{col.name}"
                assert col.type.create_constraint is True, f"{name}.{col.name}"


def test_free_text_clinical_columns_are_encrypted() -> None:
    must_encrypt = {
        ("clinical_notes", "body"),
        ("users", "email"),
        ("users", "phone"),
        ("patient_profiles", "abha_number"),
        ("health_documents", "title"),
        ("health_documents", "original_filename"),
        ("emergency_contacts", "phone"),
        ("emergency_profiles", "critical_information"),
        ("prescriptions", "diagnosis_as_written"),
        ("notifications", "body"),
        ("audit_logs", "justification"),
    }
    for table, column in must_encrypt:
        assert isinstance(TABLES[table].c[column].type, EncryptedString), f"{table}.{column}"


def test_updated_at_trigger_covers_every_table() -> None:
    touch: set[str] = set()
    for path in sorted(MIGRATIONS.glob("[0-9]*.py")):
        touch |= set(getattr(_load_migration(path.stem), "TOUCH_TABLES", ()))
    with_updated_at = {n for n, t in TABLES.items() if "updated_at" in t.c}
    assert touch == with_updated_at


def test_statement_splitter_keeps_function_bodies_whole() -> None:
    m = _load_migration("0002_integrity_triggers")
    functions = m._statements(m.FUNCTIONS)
    assert len(functions) == 6
    assert all(f.startswith("CREATE FUNCTION") and f.endswith("$$") for f in functions)
    assert all(s.startswith("CREATE TRIGGER") for s in m._statements(m.TRIGGERS))
