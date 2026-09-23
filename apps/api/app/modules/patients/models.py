"""Patients: the people whose health is recorded.

A patient profile is separate from a user account. `user_id` is NULL for dependants who
do not sign in themselves (young children, some elderly or dependent adults). They are
managed through caregiver relationships with `is_guardian = true`.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import Base, Entity, OptimisticLock, SoftDelete, str_enum, user_fk


class SexAtBirth(StrEnum):
    FEMALE = "female"
    MALE = "male"
    INTERSEX = "intersex"
    UNKNOWN = "unknown"


class BloodGroup(StrEnum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"
    UNKNOWN = "unknown"


class PatientStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"  # e.g. account closed; record retained per retention policy
    DECEASED = "deceased"


class PatientProfile(Base, Entity, SoftDelete, OptimisticLock):
    __tablename__ = "patient_profiles"
    __table_args__ = (
        # One self-profile per user account; dependants have no user_id.
        Index(
            "uq_patient_profiles_user_live",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_patient_profiles_abha_live",
            "abha_number_bidx",
            unique=True,
            postgresql_where=text("abha_number_bidx IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "date_of_birth IS NULL OR date_of_birth <= CURRENT_DATE", name="dob_not_future"
        ),
        CheckConstraint(
            "(status = 'deceased') = (deceased_at IS NOT NULL)", name="deceased_consistent"
        ),
        Index("ix_patient_profiles_family_given", "family_name", "given_name"),
    )

    user_id: Mapped[uuid.UUID | None] = user_fk()
    given_name: Mapped[str] = mapped_column(String(100), nullable=False)
    family_name: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex_at_birth: Mapped[SexAtBirth] = mapped_column(
        str_enum(SexAtBirth), nullable=False, default=SexAtBirth.UNKNOWN
    )
    gender_identity: Mapped[str | None] = mapped_column(String(50))
    blood_group: Mapped[BloodGroup] = mapped_column(
        str_enum(BloodGroup), nullable=False, default=BloodGroup.UNKNOWN
    )

    # ABHA (Ayushman Bharat Health Account): encrypted, with a blind index for lookup.
    abha_number: Mapped[str | None] = mapped_column(EncryptedString("patient_profiles.abha_number"))
    abha_number_bidx: Mapped[str | None] = mapped_column(String(64))
    abha_address: Mapped[str | None] = mapped_column(
        EncryptedString("patient_profiles.abha_address")
    )

    preferred_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="en", server_default="en"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    status: Mapped[PatientStatus] = mapped_column(
        str_enum(PatientStatus), nullable=False, default=PatientStatus.ACTIVE
    )
    deceased_at: Mapped[datetime | None]
