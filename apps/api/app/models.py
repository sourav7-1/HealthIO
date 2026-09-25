"""Imports every module's models so `Base.metadata` is complete (Alembic, tests).

Application code should import models from their own module, not from here.
"""

from app.core.models import Base
from app.modules.appointments.models import Appointment, FollowUp
from app.modules.assistant.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantPreference,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.modules.audit.models import AuditLog
from app.modules.care_team.models import DoctorPatientRelationship, DoctorProfile
from app.modules.caregivers.models import CaregiverPermission, CaregiverRelationship
from app.modules.clinical.models import (
    Allergy,
    ClinicalNote,
    DoctorVisit,
    MedicalCondition,
    MedicalHistoryEntry,
    SymptomReport,
)
from app.modules.consent.models import ConsentRecord
from app.modules.emergency.models import EmergencyContact, EmergencyProfile
from app.modules.extraction.models import PrescriptionScan
from app.modules.identity.models import (
    AuthSession,
    RefreshToken,
    User,
    UserActionToken,
    UserRole,
)
from app.modules.labs.models import (
    ReportShare,
    Test,
    TestOrder,
    TestOrderItem,
    TestReport,
    TestResult,
)
from app.modules.medications.models import (
    Medication,
    MedicationAdherence,
    MedicationChangeRequest,
    MedicationDose,
    MedicationEvent,
    MedicationSchedule,
)
from app.modules.notifications.models import Notification
from app.modules.notifications.push_models import PushSubscription
from app.modules.patients.models import PatientProfile
from app.modules.prescriptions.models import Prescription, PrescriptionItem
from app.modules.records.models import HealthDocument
from app.modules.reminders.models import ReminderPreference
from app.modules.timeline.models import RecordVersion

__all__ = [
    "Allergy",
    "Appointment",
    "AssistantConversation",
    "AssistantMessage",
    "AssistantPreference",
    "AuditLog",
    "AuthSession",
    "Base",
    "CaregiverPermission",
    "CaregiverRelationship",
    "ClinicalNote",
    "ConsentRecord",
    "DoctorPatientRelationship",
    "DoctorProfile",
    "DoctorVisit",
    "EmergencyContact",
    "EmergencyProfile",
    "FollowUp",
    "HealthDocument",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "MedicalCondition",
    "MedicalHistoryEntry",
    "Medication",
    "MedicationAdherence",
    "MedicationChangeRequest",
    "MedicationDose",
    "MedicationEvent",
    "MedicationSchedule",
    "Notification",
    "PatientProfile",
    "Prescription",
    "PrescriptionItem",
    "PrescriptionScan",
    "PushSubscription",
    "RecordVersion",
    "RefreshToken",
    "ReminderPreference",
    "ReportShare",
    "SymptomReport",
    "Test",
    "TestOrder",
    "TestOrderItem",
    "TestReport",
    "TestResult",
    "User",
    "UserActionToken",
    "UserRole",
]
