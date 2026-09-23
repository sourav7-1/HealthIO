"""Emergency information shown to responders (via SOS or the emergency QR view).

The patient chooses what the emergency view reveals. Access tokens and SOS events are
added with the emergency system in roadmap Phase 17.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import (
    Base,
    Entity,
    OptimisticLock,
    PatientOwned,
    SoftDelete,
    str_enum,
    user_fk,
)


class OrganDonorStatus(StrEnum):
    YES = "yes"
    NO = "no"
    UNDECIDED = "undecided"


class EmergencyProfile(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "emergency_profiles"
    __table_args__ = (Index("uq_emergency_profiles_patient", "patient_id", unique=True),)

    # Free text such as "Type 1 diabetic, carries insulin pen"; encrypted at rest.
    critical_information: Mapped[str | None] = mapped_column(
        EncryptedString("emergency_profiles.critical_information")
    )
    advance_directive: Mapped[str | None] = mapped_column(
        EncryptedString("emergency_profiles.advance_directive")
    )
    organ_donor: Mapped[OrganDonorStatus | None] = mapped_column(str_enum(OrganDonorStatus))
    # What the emergency view may show (patient-controlled; minimal by default).
    show_blood_group: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    show_allergies: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    show_conditions: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    show_medications: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    last_reviewed_at: Mapped[datetime | None]


class EmergencyContact(Base, Entity, PatientOwned, SoftDelete):
    __tablename__ = "emergency_contacts"
    __table_args__ = (
        Index(
            "uq_emergency_contacts_priority_live",
            "patient_id",
            "priority",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("priority BETWEEN 1 AND 10", name="priority_range"),
    )

    name: Mapped[str] = mapped_column(EncryptedString("emergency_contacts.name"), nullable=False)
    relationship_label: Mapped[str | None] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(EncryptedString("emergency_contacts.phone"), nullable=False)
    contact_user_id: Mapped[uuid.UUID | None] = user_fk()  # if the contact is also a user
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    notify_on_sos: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
