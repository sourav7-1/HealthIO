"""Shared vocabulary used by several modules (a deliberate, small shared kernel)."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    DOCTOR = "doctor"
    PATIENT = "patient"
    CAREGIVER = "caregiver"


class RecordSource(StrEnum):
    """Where a clinical fact came from. Shown to users as provenance (AI_SAFETY.md §11)."""

    DOCTOR = "doctor"
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    AI_EXTRACTION = "ai_extraction"  # only ever stored after human verification
    INTEGRATION = "integration"  # e.g. ABDM, lab system
    SYSTEM = "system"


class VerificationStatus(StrEnum):
    """Human verification of data that did not come from the treating doctor."""

    UNVERIFIED = "unverified"
    PATIENT_VERIFIED = "patient_verified"  # confirmed by patient or authorised caregiver
    DOCTOR_VERIFIED = "doctor_verified"
    REJECTED = "rejected"


class DatePrecision(StrEnum):
    """Historical dates are often known only approximately ("in 2015", "March 2019")."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"


class MealRelation(StrEnum):
    BEFORE_FOOD = "before_food"
    AFTER_FOOD = "after_food"
    WITH_FOOD = "with_food"
    EMPTY_STOMACH = "empty_stomach"
    BEDTIME = "bedtime"
    ANY = "any"
