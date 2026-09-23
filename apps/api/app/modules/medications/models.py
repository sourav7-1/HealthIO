"""Medications: what the patient actually takes, when, and whether each dose was taken.

prescription_item ─(0..1)─► medication ─(1..n)─► medication_schedule ─(1..n)─► medication_dose
                                        └─(1..n)─► medication_adherence (daily rollup)

- A medication created from a prescription starts PENDING_CONFIRMATION until the patient
  (or an authorised caregiver) confirms the schedule. Nothing becomes ACTIVE silently.
- Schedules are versioned by ending one and creating the next (history is kept).
- Dose rows are materialised ahead of time by the reminder engine; (schedule_id,
  scheduled_at) is unique so materialisation is idempotent. PRN (as-needed) doses are
  logged with no schedule.
- `medication_doses` is the highest-volume table; it is designed to be range-partitioned
  by `scheduled_at` month when volume requires it (no FK points at it).
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Computed,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MealRelation, RecordSource
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


class MedicationSource(StrEnum):
    PRESCRIPTION = "prescription"  # from a prescription item (issued or recorded)
    SELF_REPORTED = "self_reported"  # e.g. over-the-counter, entered by patient/caregiver
    INTEGRATION = "integration"


class MedicationStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"  # course finished as prescribed
    STOPPED = "stopped"  # stopped before the planned end (who and why is recorded)
    ENTERED_IN_ERROR = "entered_in_error"


class Medication(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "medications"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["prescription_item_id"], "prescription_items"),
        CheckConstraint(
            "source <> 'prescription' OR prescription_item_id IS NOT NULL",
            name="prescription_source_has_item",
        ),
        CheckConstraint(
            "status NOT IN ('active', 'paused', 'completed', 'stopped') OR "
            "(confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)",
            name="confirmed_before_active",
        ),
        CheckConstraint(
            "(status = 'stopped') = (stopped_at IS NOT NULL)", name="stopped_consistent"
        ),
        CheckConstraint(
            "status <> 'stopped' OR (stopped_by IS NOT NULL AND stop_source IS NOT NULL)",
            name="stopped_has_actor",
        ),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="end_after_start",
        ),
        # One live regimen per prescription line.
        Index(
            "uq_medications_prescription_item_live",
            "prescription_item_id",
            unique=True,
            postgresql_where=text(
                "prescription_item_id IS NOT NULL AND status <> 'entered_in_error'"
            ),
        ),
        Index("ix_medications_patient_status", "patient_id", "status"),
    )

    source: Mapped[MedicationSource] = mapped_column(str_enum(MedicationSource), nullable=False)
    prescription_item_id: Mapped[uuid.UUID | None]

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    generic_name: Mapped[str | None] = mapped_column(String(200))
    drug_code: Mapped[str | None] = mapped_column(String(64))
    strength: Mapped[str | None] = mapped_column(String(64))
    dosage_form: Mapped[str | None] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(64))
    is_prn: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    instructions: Mapped[str | None] = mapped_column(Text)

    status: Mapped[MedicationStatus] = mapped_column(
        str_enum(MedicationStatus), nullable=False, default=MedicationStatus.PENDING_CONFIRMATION
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    confirmed_at: Mapped[datetime | None]
    confirmed_by: Mapped[uuid.UUID | None] = user_fk()

    # A stop is recorded as reported: by the doctor, or as the patient's own decision.
    # The platform itself never advises stopping (AI_SAFETY.md §2).
    stopped_at: Mapped[datetime | None]
    stopped_by: Mapped[uuid.UUID | None] = user_fk()
    stop_source: Mapped[RecordSource | None] = mapped_column(
        str_enum(RecordSource, name="stop_source")
    )
    stop_reason: Mapped[str | None] = mapped_column(String(300))


class ScheduleType(StrEnum):
    FIXED_TIMES = "fixed_times"  # e.g. 08:00 and 20:00 local time
    INTERVAL = "interval"  # e.g. every 8 hours
    AS_NEEDED = "as_needed"  # PRN: no reminders, doses logged when taken


class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class MedicationSchedule(Base, Entity, PatientOwned, OptimisticLock):
    __tablename__ = "medication_schedules"
    __table_args__ = (
        patient_scope_key(),
        UniqueConstraint("id", "medication_id", "patient_id"),
        patient_scoped_fk(["medication_id"], "medications"),
        patient_scoped_fk(["supersedes_schedule_id"], "medication_schedules"),
        CheckConstraint(
            "schedule_type <> 'fixed_times' OR cardinality(times_of_day) >= 1",
            name="fixed_times_has_times",
        ),
        CheckConstraint(
            "schedule_type <> 'interval' OR interval_minutes BETWEEN 15 AND 10080",
            name="interval_range",
        ),
        CheckConstraint("dose_amount IS NULL OR dose_amount > 0", name="dose_positive"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="until_after_from",
        ),
        CheckConstraint(
            "status <> 'ended' OR (effective_until IS NOT NULL AND ended_reason IS NOT NULL)",
            name="ended_has_end_and_reason",
        ),
        Index(
            "ix_medication_schedules_active",
            "medication_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    medication_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    schedule_type: Mapped[ScheduleType] = mapped_column(str_enum(ScheduleType), nullable=False)
    # Local wall-clock times in `timezone` (DST-safe expansion happens in code).
    times_of_day: Mapped[list[time]] = mapped_column(
        ARRAY(Time), nullable=False, default=list, server_default="{}"
    )
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    # Optional RFC 5545 RRULE for day patterns ("FREQ=WEEKLY;BYDAY=MO,TH", alternate days).
    recurrence_rule: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    dose_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    dose_unit: Mapped[str | None] = mapped_column(String(32))
    meal_relation: Mapped[MealRelation | None] = mapped_column(str_enum(MealRelation))
    effective_from: Mapped[datetime] = mapped_column(nullable=False)
    effective_until: Mapped[datetime | None]
    status: Mapped[ScheduleStatus] = mapped_column(
        str_enum(ScheduleStatus), nullable=False, default=ScheduleStatus.ACTIVE
    )
    ended_reason: Mapped[str | None] = mapped_column(String(200))
    supersedes_schedule_id: Mapped[uuid.UUID | None]


class DoseStatus(StrEnum):
    SCHEDULED = "scheduled"
    SNOOZED = "snoozed"
    TAKEN = "taken"
    SKIPPED = "skipped"
    MISSED = "missed"  # set by the reminder engine after the grace window
    CANCELLED = "cancelled"  # schedule changed or medication stopped before the dose


class DoseRecordedVia(StrEnum):
    PATIENT_APP = "patient_app"
    CAREGIVER_APP = "caregiver_app"
    REMINDER_ACTION = "reminder_action"  # tapped "Taken" in a notification
    DOCTOR = "doctor"
    SYSTEM = "system"  # e.g. marked missed, cancelled


class MedicationDose(Base, Entity, PatientOwned):
    __tablename__ = "medication_doses"
    __table_args__ = (
        patient_scoped_fk(["medication_id"], "medications"),
        # The schedule must belong to the same medication and patient.
        ForeignKeyConstraint(
            ["schedule_id", "medication_id", "patient_id"],
            [
                "medication_schedules.id",
                "medication_schedules.medication_id",
                "medication_schedules.patient_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("schedule_id", "scheduled_at"),
        CheckConstraint(
            "(schedule_id IS NULL) = (scheduled_at IS NULL)", name="scheduled_has_schedule"
        ),
        CheckConstraint("schedule_id IS NOT NULL OR status = 'taken'", name="prn_dose_is_taken"),
        CheckConstraint("(status = 'taken') = (taken_at IS NOT NULL)", name="taken_consistent"),
        CheckConstraint(
            "(status = 'snoozed') = (snoozed_until IS NOT NULL)", name="snoozed_consistent"
        ),
        CheckConstraint(
            "status NOT IN ('taken', 'skipped') OR recorded_via IS NOT NULL",
            name="patient_action_has_channel",
        ),
        CheckConstraint("dose_amount IS NULL OR dose_amount > 0", name="dose_positive"),
        CheckConstraint("snooze_count >= 0", name="snooze_count_non_negative"),
        # Reminder dispatch scans only open doses.
        Index(
            "ix_medication_doses_open_due",
            "scheduled_at",
            postgresql_where=text("status IN ('scheduled', 'snoozed')"),
        ),
        Index("ix_medication_doses_patient_scheduled", "patient_id", "scheduled_at"),
        Index("ix_medication_doses_medication_scheduled", "medication_id", "scheduled_at"),
    )

    medication_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    schedule_id: Mapped[uuid.UUID | None]
    scheduled_at: Mapped[datetime | None]
    status: Mapped[DoseStatus] = mapped_column(
        str_enum(DoseStatus), nullable=False, default=DoseStatus.SCHEDULED
    )
    snoozed_until: Mapped[datetime | None]
    snooze_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    taken_at: Mapped[datetime | None]
    dose_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    dose_unit: Mapped[str | None] = mapped_column(String(32))
    recorded_by: Mapped[uuid.UUID | None] = user_fk()
    recorded_via: Mapped[DoseRecordedVia | None] = mapped_column(str_enum(DoseRecordedVia))
    skip_reason: Mapped[str | None] = mapped_column(String(200))


class MedicationAdherence(Base, Entity, PatientOwned):
    """Daily adherence rollup per medication (recomputed idempotently by a nightly job).

    PRN medications are excluded; paused days have scheduled_count = 0.
    """

    __tablename__ = "medication_adherence"
    __table_args__ = (
        patient_scoped_fk(["medication_id"], "medications"),
        UniqueConstraint("medication_id", "day"),
        CheckConstraint(
            "scheduled_count >= 0 AND taken_count >= 0 AND taken_late_count >= 0 "
            "AND skipped_count >= 0 AND missed_count >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "taken_count + skipped_count + missed_count <= scheduled_count",
            name="outcomes_within_scheduled",
        ),
        CheckConstraint("taken_late_count <= taken_count", name="late_within_taken"),
        Index("ix_medication_adherence_patient_day", "patient_id", "day"),
    )

    medication_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # in the patient's timezone
    scheduled_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    taken_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    taken_late_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    skipped_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    missed_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    adherence_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        Computed(
            "CASE WHEN scheduled_count = 0 THEN NULL "
            "ELSE round(taken_count::numeric / scheduled_count, 4) END",
            persisted=True,
        ),
    )
    computed_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
