"""Declarative base, standard columns and helpers shared by all module models.

Conventions (see docs/data-model.md):
- UUIDv7 primary keys generated in the application.
- `created_at`/`updated_at` (timestamptz, UTC) on every table; a DB trigger keeps
  `updated_at` correct even for bulk SQL updates.
- `created_by`/`updated_by` reference `users.id`; NULL means "system".
- Patient-owned tables carry `patient_id`. Child tables repeat it and reference the parent
  with a composite foreign key (child.parent_id, child.patient_id) → (parent.id,
  parent.patient_id), so a row can never point at another patient's parent record.
- Enums are stored as VARCHAR with a CHECK constraint (not native PG enums), which keeps
  adding values a simple, transactional migration.
- ORM relationships are declared only inside a module. Cross-module links are plain
  foreign keys (PROJECT_RULES.md §2).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.core.ids import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

USERS_ID = "users.id"
PATIENTS_ID = "patient_profiles.id"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012 (SQLAlchemy API)


# --- column helpers ------------------------------------------------------------------


def str_enum(enum_cls: type[enum.StrEnum], name: str | None = None) -> Enum:
    """VARCHAR + CHECK constraint storing the enum *values*."""
    return Enum(
        enum_cls,
        name=name or _snake(enum_cls.__name__),
        native_enum=False,
        create_constraint=True,
        length=max(len(m.value) for m in enum_cls),
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _snake(name: str) -> str:
    out = [name[0].lower()]
    for ch in name[1:]:
        out.append("_" + ch.lower() if ch.isupper() else ch)
    return "".join(out)


def user_fk(*, nullable: bool = True, index: bool = False) -> Any:
    """Reference to a user. Users are never hard-deleted (accounts are closed and
    pseudonymised), so RESTRICT keeps every historical reference valid."""
    return mapped_column(
        Uuid, ForeignKey(USERS_ID, ondelete="RESTRICT"), nullable=nullable, index=index
    )


def patient_scoped_fk(
    columns: list[str],
    parent_table: str,
    *,
    ondelete: str = "RESTRICT",
    use_alter: bool = False,
) -> ForeignKeyConstraint:
    """Composite FK (<parent_id>, patient_id) → parent(id, patient_id).

    `use_alter` adds the constraint after both tables exist (needed to break FK cycles).
    """
    (col,) = columns
    return ForeignKeyConstraint(
        [col, "patient_id"],
        [f"{parent_table}.id", f"{parent_table}.patient_id"],
        ondelete=ondelete,
        use_alter=use_alter,
    )


def patient_scope_key() -> UniqueConstraint:
    """Unique (id, patient_id) on a parent table, target of patient_scoped_fk()."""
    return UniqueConstraint("id", "patient_id")


# --- mixins --------------------------------------------------------------------------


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _user_ref() -> Mapped[uuid.UUID | None]:
    return mapped_column(Uuid, ForeignKey(USERS_ID, ondelete="RESTRICT"))


class Actors:
    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return _user_ref()

    @declared_attr
    def updated_by(cls) -> Mapped[uuid.UUID | None]:
        return _user_ref()


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @declared_attr
    def deleted_by(cls) -> Mapped[uuid.UUID | None]:
        return _user_ref()


class OptimisticLock:
    """`version` increments on every ORM update; a stale write raises StaleDataError."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        return {"version_id_col": cls.version}


class PatientOwned:
    @declared_attr
    def patient_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            Uuid, ForeignKey(PATIENTS_ID, ondelete="RESTRICT"), nullable=False, index=True
        )


class Entity(UUIDPrimaryKey, Timestamps, Actors):
    """Standard columns for every business table."""
