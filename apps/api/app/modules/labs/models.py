"""Labs: test catalogue, doctor orders, reports and individual results.

test (catalogue) ◄── test_order_item ──► test_order ◄── test_report ──► test_result
                                              (optional)       │
                                                   health_document (the report file)

Reports that do not come from a doctor (patient uploads, AI extraction) stay
PENDING_REVIEW until a person verifies them; results of unverified reports are never
used for trends or alerts. Verified reports are frozen (trigger, migration 0002).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.enums import RecordSource
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


class TestCategory(StrEnum):
    __test__ = False  # stop pytest collecting this class

    HAEMATOLOGY = "haematology"
    BIOCHEMISTRY = "biochemistry"
    ENDOCRINOLOGY = "endocrinology"
    IMMUNOLOGY = "immunology"
    MICROBIOLOGY = "microbiology"
    PATHOLOGY = "pathology"
    URINALYSIS = "urinalysis"
    IMAGING = "imaging"
    CARDIOLOGY = "cardiology"  # e.g. ECG, echo
    OTHER = "other"


class Test(Base, Entity):
    """Catalogue of orderable tests and analytes (reference data, not patient data).

    Populated from reviewed reference sources (e.g. LOINC); no rows are seeded here.
    """

    __test__ = False
    __tablename__ = "tests"
    __table_args__ = (
        Index("uq_tests_code", "code", unique=True),
        Index(
            "uq_tests_loinc_code",
            "loinc_code",
            unique=True,
            postgresql_where=text("loinc_code IS NOT NULL"),
        ),
        CheckConstraint(
            "loinc_code IS NULL OR loinc_code ~ '^[0-9]{1,7}-[0-9]$'", name="loinc_format"
        ),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)  # internal stable code
    loinc_code: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[TestCategory] = mapped_column(str_enum(TestCategory), nullable=False)
    specimen: Mapped[str | None] = mapped_column(String(64))
    default_unit: Mapped[str | None] = mapped_column(String(32))
    is_panel: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")


class TestOrderStatus(StrEnum):
    __test__ = False

    ORDERED = "ordered"
    SAMPLE_COLLECTED = "sample_collected"
    PARTIALLY_RESULTED = "partially_resulted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ENTERED_IN_ERROR = "entered_in_error"


class TestPriority(StrEnum):
    __test__ = False

    ROUTINE = "routine"
    URGENT = "urgent"
    STAT = "stat"


class TestOrder(Base, Entity, PatientOwned, OptimisticLock):
    __test__ = False
    __tablename__ = "test_orders"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["visit_id"], "doctor_visits"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_consistent"
        ),
        CheckConstraint("due_by IS NULL OR due_by >= ordered_at::date", name="due_after_order"),
        Index("ix_test_orders_patient_ordered", "patient_id", "ordered_at"),
        Index(
            "ix_test_orders_doctor_open",
            "ordering_doctor_id",
            postgresql_where=text(
                "status IN ('ordered', 'sample_collected', 'partially_resulted')"
            ),
        ),
    )

    ordering_doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    visit_id: Mapped[uuid.UUID | None]
    status: Mapped[TestOrderStatus] = mapped_column(
        str_enum(TestOrderStatus), nullable=False, default=TestOrderStatus.ORDERED
    )
    priority: Mapped[TestPriority] = mapped_column(
        str_enum(TestPriority), nullable=False, default=TestPriority.ROUTINE
    )
    clinical_indication: Mapped[str | None] = mapped_column(
        EncryptedString("test_orders.clinical_indication")
    )
    ordered_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    due_by: Mapped[date | None] = mapped_column(Date)
    cancelled_at: Mapped[datetime | None]
    cancelled_by: Mapped[uuid.UUID | None] = user_fk()
    cancel_reason: Mapped[str | None] = mapped_column(String(300))


class TestOrderItem(Base, Entity, PatientOwned):
    __test__ = False
    __tablename__ = "test_order_items"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["order_id"], "test_orders", ondelete="CASCADE"),
        UniqueConstraint("order_id", "test_id"),
        UniqueConstraint("order_id", "test_name"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # Catalogue link when the test is in `tests`; the name as ordered is always kept.
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tests.id", ondelete="RESTRICT")
    )
    test_name: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str | None] = mapped_column(String(300))


class TestReportStatus(StrEnum):
    __test__ = False

    PENDING_REVIEW = "pending_review"  # e.g. uploaded or AI-extracted, not yet verified
    VERIFIED = "verified"  # frozen
    REJECTED = "rejected"
    ENTERED_IN_ERROR = "entered_in_error"


class TestReport(Base, Entity, PatientOwned, OptimisticLock):
    __test__ = False
    __tablename__ = "test_reports"
    __table_args__ = (
        patient_scope_key(),
        patient_scoped_fk(["order_id"], "test_orders"),
        patient_scoped_fk(["document_id"], "health_documents"),
        CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name="verified_has_verifier",
        ),
        CheckConstraint(
            "reported_at IS NULL OR collected_at IS NULL OR reported_at >= collected_at",
            name="reported_after_collected",
        ),
        Index("ix_test_reports_patient_collected", "patient_id", "collected_at"),
    )

    order_id: Mapped[uuid.UUID | None]
    document_id: Mapped[uuid.UUID | None]
    source: Mapped[RecordSource] = mapped_column(str_enum(RecordSource), nullable=False)
    status: Mapped[TestReportStatus] = mapped_column(
        str_enum(TestReportStatus), nullable=False, default=TestReportStatus.PENDING_REVIEW
    )
    lab_name: Mapped[str | None] = mapped_column(String(200))
    collected_at: Mapped[datetime | None]
    reported_at: Mapped[datetime | None]
    conclusion: Mapped[str | None] = mapped_column(EncryptedString("test_reports.conclusion"))
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None] = user_fk()


class ResultFlag(StrEnum):
    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"
    CRITICAL_LOW = "critical_low"
    CRITICAL_HIGH = "critical_high"
    ABNORMAL = "abnormal"  # non-numeric abnormal (e.g. "positive")
    UNKNOWN = "unknown"


class TestResult(Base, Entity, PatientOwned):
    """One analyte value, stored exactly as printed (value, unit, reference range)."""

    __test__ = False
    __tablename__ = "test_results"
    __table_args__ = (
        patient_scoped_fk(["report_id"], "test_reports", ondelete="CASCADE"),
        UniqueConstraint("report_id", "sequence"),
        CheckConstraint("value_numeric IS NOT NULL OR value_text IS NOT NULL", name="has_value"),
        CheckConstraint(
            "reference_low IS NULL OR reference_high IS NULL OR reference_low <= reference_high",
            name="reference_range_ordered",
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        # Trend queries: one analyte for one patient over time.
        Index("ix_test_results_patient_test", "patient_id", "test_id"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tests.id", ondelete="RESTRICT")
    )
    analyte_name: Mapped[str] = mapped_column(String(200), nullable=False)  # as printed
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_text: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(32))
    reference_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_text: Mapped[str | None] = mapped_column(String(200))
    flag: Mapped[ResultFlag] = mapped_column(
        str_enum(ResultFlag), nullable=False, default=ResultFlag.UNKNOWN
    )
