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

from app.core.crypto import EncryptedString
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
    CLINICIAN_RECORDED = "clinician_recorded"  # an existing medicine recorded by a doctor
    INTEGRATION = "integration"


class MedicationOrigin(StrEnum):
    """Where the medicine came from, shown to everyone as its label."""

    DOCTOR_PRESCRIPTION = "doctor_prescription"  # e-prescription issued on the platform
    UPLOADED_AI = "uploaded_prescription_ai"  # paper prescription read by AI, then checked
    UPLOADED_TYPED = "uploaded_prescription_typed"  # paper prescription typed in by a person
    SELF_REPORTED = "self_reported"  # added by the patient or caregiver
    CLINICIAN_RECORDED = "clinician_recorded"  # an existing medicine recorded by a doctor
    INTEGRATION = "integration"


class MedicationStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"  # course finished as prescribed
    STOPPED = "stopped"  # discontinued before the planned end (who and why is recorded)
    ENTERED_IN_ERROR = "entered_in_error"


OPEN_MEDICATION_STATUSES = ("pending_confirmation", "active", "paused")

# Duplicate detection keys, computed by PostgreSQL itself so every writer gets them:
# the name without its form prefix (Tab./Cap./Syp. …), letters and digits only.
_FORM_PREFIX = (
    r"^\s*(tab|tabs|tablet|cap|caps|capsule|syp|syrup|susp|suspension|inj|injection|oint"
    r"|ointment|cream|gel|drop|drops|inh|inhaler|sachet|lotion|spray)\.?\s+"
)
NAME_KEY_SQL = (
    f"lower(regexp_replace(regexp_replace(name, '{_FORM_PREFIX}', '', 'i'), "
    "'[^[:alnum:]]+', '', 'g'))"
)
GENERIC_KEY_SQL = (
    "CASE WHEN generic_name IS NULL OR btrim(generic_name) = '' THEN NULL ELSE "
    "lower(regexp_replace(generic_name, '[^[:alnum:]]+', '', 'g')) END"
)
STRENGTH_KEY_SQL = "lower(regexp_replace(coalesce(strength, ''), '[^[:alnum:].]+', '', 'g'))"


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
        CheckConstraint("(status = 'paused') = (paused_at IS NOT NULL)", name="paused_consistent"),
        CheckConstraint("status <> 'paused' OR paused_by IS NOT NULL", name="paused_has_actor"),
        CheckConstraint(
            "source <> 'self_reported' OR origin = 'self_reported'", name="self_reported_origin"
        ),
        CheckConstraint(
            "origin NOT IN ('doctor_prescription', 'uploaded_prescription_ai', "
            "'uploaded_prescription_typed') OR source = 'prescription'",
            name="prescription_origin",
        ),
        # The same medicine cannot be on the patient's own list twice while in use.
        Index(
            "uq_medications_self_reported_open",
            "patient_id",
            "name_key",
            "strength_key",
            unique=True,
            postgresql_where=text(
                "source = 'self_reported' "
                "AND status IN ('pending_confirmation', 'active', 'paused')"
            ),
        ),
        Index(
            "ix_medications_patient_name_key_open",
            "patient_id",
            "name_key",
            postgresql_where=text("status IN ('pending_confirmation', 'active', 'paused')"),
        ),
    )

    source: Mapped[MedicationSource] = mapped_column(str_enum(MedicationSource), nullable=False)
    origin: Mapped[MedicationOrigin] = mapped_column(str_enum(MedicationOrigin), nullable=False)
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

    paused_at: Mapped[datetime | None]
    paused_by: Mapped[uuid.UUID | None] = user_fk()
    pause_reason: Mapped[str | None] = mapped_column(String(300))
    resume_on: Mapped[date | None] = mapped_column(Date)  # optional planned restart

    name_key: Mapped[str] = mapped_column(
        String(200), Computed(NAME_KEY_SQL, persisted=True), nullable=False
    )
    generic_key: Mapped[str | None] = mapped_column(
        String(200), Computed(GENERIC_KEY_SQL, persisted=True)
    )
    strength_key: Mapped[str] = mapped_column(
        String(64), Computed(STRENGTH_KEY_SQL, persisted=True), nullable=False
    )


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


class MedicationEventType(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"  # a prescribed medicine accepted and scheduled
    SCHEDULE_CHANGED = "schedule_changed"
    PAUSED = "paused"
    RESUMED = "resumed"
    STOPPED = "stopped"
    COMPLETED = "completed"  # course end date passed
    CHANGE_REQUESTED = "change_requested"
    CHANGE_APPROVED = "change_approved"
    CHANGE_DECLINED = "change_declined"
    CHANGE_WITHDRAWN = "change_withdrawn"
    DUPLICATE_NOTED = "duplicate_noted"


class ActorRole(StrEnum):
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    DOCTOR = "doctor"
    SYSTEM = "system"  # scheduled jobs; may only record a course reaching its end date


class AdvisorRole(StrEnum):
    DOCTOR = "doctor"
    PHARMACIST = "pharmacist"


class MedicationEvent(Base, Entity, PatientOwned):
    """Append-only history of a medicine (migration 0009 blocks UPDATE and DELETE).

    Changes to a regimen are always made by a named person. The database refuses a
    schedule change, pause, stop or resume without a human actor, so no automated
    process (including AI) can change what a patient takes.
    """

    __tablename__ = "medication_events"
    __table_args__ = (
        patient_scoped_fk(["medication_id"], "medications"),
        CheckConstraint(
            "actor_role = 'system' OR actor_user_id IS NOT NULL", name="person_has_user"
        ),
        CheckConstraint(
            "actor_role <> 'system' OR event_type IN ('completed', 'duplicate_noted')",
            name="system_cannot_change_regimen",
        ),
        CheckConstraint(
            "(advised_by_role IS NULL) = (advised_by_name IS NULL)", name="advisor_complete"
        ),
        Index("ix_medication_events_medication_occurred", "medication_id", "occurred_at"),
        Index("ix_medication_events_patient_occurred", "patient_id", "occurred_at"),
    )

    medication_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[MedicationEventType] = mapped_column(
        str_enum(MedicationEventType), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = user_fk()
    actor_role: Mapped[ActorRole] = mapped_column(str_enum(ActorRole), nullable=False)
    # What changed (before/after regimen, findings acknowledged), encrypted JSON.
    details: Mapped[str | None] = mapped_column(EncryptedString("medication_events.details"))
    reason: Mapped[str | None] = mapped_column(EncryptedString("medication_events.reason"))
    # A clinician outside the platform who advised the change, as reported by the patient.
    advised_by_role: Mapped[AdvisorRole | None] = mapped_column(str_enum(AdvisorRole))
    advised_by_name: Mapped[str | None] = mapped_column(
        EncryptedString("medication_events.advised_by_name")
    )
    change_request_id: Mapped[uuid.UUID | None]


class ChangeRequestKind(StrEnum):
    SCHEDULE = "schedule"  # dose, frequency, days, food relation
    STOP = "stop"
    PAUSE = "pause"
    OTHER = "other"


class ChangeRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class MedicationChangeRequest(Base, Entity, PatientOwned, OptimisticLock):
    """A patient or caregiver asks a linked doctor to confirm a clinically relevant change.
    Nothing changes until a doctor approves; approving applies exactly the proposal."""

    __tablename__ = "medication_change_requests"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["medication_id"], "medications"),
        CheckConstraint("(status = 'pending') = (resolved_at IS NULL)", name="resolved_consistent"),
        CheckConstraint(
            "status NOT IN ('approved', 'declined') OR resolved_by IS NOT NULL",
            name="resolution_has_actor",
        ),
        CheckConstraint(
            "kind <> 'schedule' OR proposed IS NOT NULL", name="schedule_change_has_proposal"
        ),
        Index(
            "uq_medication_change_requests_pending",
            "medication_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_medication_change_requests_patient_status", "patient_id", "status"),
    )

    medication_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[ChangeRequestKind] = mapped_column(str_enum(ChangeRequestKind), nullable=False)
    status: Mapped[ChangeRequestStatus] = mapped_column(
        str_enum(ChangeRequestStatus), nullable=False, default=ChangeRequestStatus.PENDING
    )
    requested_by: Mapped[uuid.UUID] = user_fk(nullable=False)
    requester_role: Mapped[ActorRole] = mapped_column(
        str_enum(ActorRole, name="requester_role"), nullable=False
    )
    message: Mapped[str | None] = mapped_column(
        EncryptedString("medication_change_requests.message")
    )
    proposed: Mapped[str | None] = mapped_column(
        EncryptedString("medication_change_requests.proposed")
    )
    resolved_by: Mapped[uuid.UUID | None] = user_fk()
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None] = mapped_column(
        EncryptedString("medication_change_requests.resolution_note")
    )
