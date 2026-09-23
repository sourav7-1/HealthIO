"""Appointments and follow-ups.

Double booking is prevented by the database: an exclusion constraint forbids two live
appointments for the same doctor with overlapping time ranges (needs btree_gist).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import (
    Base,
    Entity,
    OptimisticLock,
    PatientOwned,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
    user_fk,
)


class AppointmentStatus(StrEnum):
    REQUESTED = "requested"  # patient asked; doctor must confirm
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class AppointmentMode(StrEnum):
    IN_PERSON = "in_person"
    TELECONSULT = "teleconsult"
    HOME_VISIT = "home_visit"


class Appointment(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "appointments"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["follow_up_id"], "follow_ups"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("ends_at - starts_at <= interval '12 hours'", name="duration_reasonable"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_consistent"
        ),
        ExcludeConstraint(
            ("doctor_id", "="),
            (func.tstzrange(text("starts_at"), text("ends_at"), "[)"), "&&"),
            name="no_doctor_double_booking",
            using="gist",
            where=text("status IN ('requested', 'scheduled', 'confirmed', 'checked_in')"),
        ),
        Index("ix_appointments_patient_starts", "patient_id", "starts_at"),
        Index("ix_appointments_doctor_starts", "doctor_id", "starts_at"),
    )

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    follow_up_id: Mapped[uuid.UUID | None]
    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    ends_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        str_enum(AppointmentStatus), nullable=False, default=AppointmentStatus.REQUESTED
    )
    mode: Mapped[AppointmentMode] = mapped_column(str_enum(AppointmentMode), nullable=False)
    reason: Mapped[str | None] = mapped_column(EncryptedString("appointments.reason"))
    location: Mapped[str | None] = mapped_column(String(200))
    teleconsult_url: Mapped[str | None] = mapped_column(String(500))
    booked_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    cancelled_at: Mapped[datetime | None]
    cancelled_by: Mapped[uuid.UUID | None] = user_fk()
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class FollowUpStatus(StrEnum):
    OPEN = "open"
    BOOKED = "booked"  # an appointment references this follow-up
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FollowUp(Base, Entity, PatientOwned, OptimisticLock):
    """A doctor's instruction to review the patient again (e.g. "review in 2 weeks")."""

    __tablename__ = "follow_ups"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["source_visit_id"], "doctor_visits"),
        patient_scoped_fk(["source_prescription_id"], "prescriptions"),
        patient_scoped_fk(["completed_visit_id"], "doctor_visits"),
        CheckConstraint(
            "status <> 'completed' OR completed_at IS NOT NULL", name="completed_has_time"
        ),
        Index(
            "ix_follow_ups_doctor_open_due",
            "doctor_id",
            "due_date",
            postgresql_where=text("status IN ('open', 'booked')"),
        ),
        Index("ix_follow_ups_patient_due", "patient_id", "due_date"),
    )

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    source_visit_id: Mapped[uuid.UUID | None]
    source_prescription_id: Mapped[uuid.UUID | None]
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(EncryptedString("follow_ups.reason"))
    status: Mapped[FollowUpStatus] = mapped_column(
        str_enum(FollowUpStatus), nullable=False, default=FollowUpStatus.OPEN
    )
    completed_at: Mapped[datetime | None]
    completed_visit_id: Mapped[uuid.UUID | None]
