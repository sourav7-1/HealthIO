"""Permission catalogue: the single source of truth for who may do what.

Two kinds of permission:

* **Patient-scoped** permissions apply to one patient's data. A caller holds them through
  a relationship with that patient: being the patient, being a linked verified doctor
  (narrowed by the patient's consent), or being a caregiver with granted scopes.
* **Platform** permissions come from a role alone (for example verifying doctors). They
  never include access to anyone's health data.

Having a role is never enough to see a patient's data (PROJECT_RULES.md §3).
"""

from enum import StrEnum

from app.core.enums import Role
from app.modules.caregivers.models import CaregiverPermissionScope
from app.modules.consent.models import DataCategory


class Permission(StrEnum):
    # --- patient-scoped: grantable to caregivers (values match CaregiverPermissionScope)
    VIEW_PROFILE = "view_profile"
    VIEW_MEDICAL_HISTORY = "view_medical_history"
    VIEW_MEDICATIONS = "view_medications"
    LOG_DOSES = "log_doses"
    MANAGE_REMINDERS = "manage_reminders"
    VIEW_APPOINTMENTS = "view_appointments"
    MANAGE_APPOINTMENTS = "manage_appointments"
    VIEW_REPORTS = "view_reports"
    UPLOAD_REPORTS = "upload_reports"
    RECEIVE_ALERTS = "receive_alerts"
    USE_AI_ASSISTANT = "use_ai_assistant"
    MANAGE_EMERGENCY_INFO = "manage_emergency_info"
    MANAGE_CAREGIVERS = "manage_caregivers"

    # --- patient-scoped: not grantable to caregivers
    VIEW_VISITS = "view_visits"  # visits and clinical notes
    VIEW_PRESCRIPTIONS = "view_prescriptions"
    VIEW_ADHERENCE = "view_adherence"
    EDIT_PROFILE = "edit_profile"
    MANAGE_CONSENT = "manage_consent"
    EDIT_CLINICAL_RECORDS = "edit_clinical_records"  # visits, notes, problem list
    CHANGE_DOCTOR_PRESCRIPTION = "change_doctor_prescription"  # issue, amend, cancel
    DELETE_MEDICAL_RECORDS = "delete_medical_records"

    # --- platform (role-based, no patient data)
    VERIFY_DOCTORS = "verify_doctors"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT_LOG = "view_audit_log"
    LIST_OWN_PATIENTS = "list_own_patients"


CAREGIVER_GRANTABLE: frozenset[Permission] = frozenset(
    Permission(scope.value) for scope in CaregiverPermissionScope
)

# Defence in depth: removed from every caregiver decision even if a grant existed.
NEVER_FOR_CAREGIVERS: frozenset[Permission] = frozenset(
    {
        Permission.EDIT_CLINICAL_RECORDS,
        Permission.CHANGE_DOCTOR_PRESCRIPTION,
        Permission.DELETE_MEDICAL_RECORDS,
        Permission.EDIT_PROFILE,
        Permission.MANAGE_CONSENT,
        Permission.VIEW_VISITS,
        Permission.VIEW_PRESCRIPTIONS,
        Permission.VIEW_ADHERENCE,
    }
)

# Scopes that only a guardian (parent of a minor, legal representative) may hold.
GUARDIAN_ONLY: frozenset[Permission] = frozenset({Permission.MANAGE_CAREGIVERS})

# What a patient may do with their own record. Clinical records written by doctors
# are read-only for patients; nobody hard-deletes medical records through the API.
SELF_PERMISSIONS: frozenset[Permission] = CAREGIVER_GRANTABLE | {
    Permission.VIEW_VISITS,
    Permission.VIEW_PRESCRIPTIONS,
    Permission.VIEW_ADHERENCE,
    Permission.EDIT_PROFILE,
    Permission.MANAGE_CONSENT,
}

# What a linked, verified doctor may do, before narrowing by consent categories.
DOCTOR_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.VIEW_PROFILE,
        Permission.VIEW_MEDICAL_HISTORY,
        Permission.VIEW_MEDICATIONS,
        Permission.VIEW_APPOINTMENTS,
        Permission.MANAGE_APPOINTMENTS,
        Permission.VIEW_REPORTS,
        Permission.UPLOAD_REPORTS,
        Permission.VIEW_VISITS,
        Permission.VIEW_PRESCRIPTIONS,
        Permission.VIEW_ADHERENCE,
        Permission.EDIT_CLINICAL_RECORDS,
        Permission.CHANGE_DOCTOR_PRESCRIPTION,
    }
)

# Consent data category that must be shared with a doctor for each permission.
DOCTOR_PERMISSION_CATEGORY: dict[Permission, DataCategory] = {
    Permission.VIEW_PROFILE: DataCategory.DEMOGRAPHICS,
    Permission.VIEW_MEDICAL_HISTORY: DataCategory.CONDITIONS,
    Permission.VIEW_MEDICATIONS: DataCategory.MEDICATIONS,
    Permission.VIEW_APPOINTMENTS: DataCategory.APPOINTMENTS,
    Permission.MANAGE_APPOINTMENTS: DataCategory.APPOINTMENTS,
    Permission.VIEW_REPORTS: DataCategory.TESTS_AND_REPORTS,
    Permission.UPLOAD_REPORTS: DataCategory.TESTS_AND_REPORTS,
    Permission.VIEW_VISITS: DataCategory.VISITS_AND_NOTES,
    Permission.EDIT_CLINICAL_RECORDS: DataCategory.VISITS_AND_NOTES,
    Permission.VIEW_PRESCRIPTIONS: DataCategory.PRESCRIPTIONS,
    Permission.CHANGE_DOCTOR_PRESCRIPTION: DataCategory.PRESCRIPTIONS,
    Permission.VIEW_ADHERENCE: DataCategory.ADHERENCE,
}

# Platform permissions by role. Admins verify and manage accounts; they get no
# patient-scoped permission from their role.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(
        {Permission.VERIFY_DOCTORS, Permission.MANAGE_USERS, Permission.VIEW_AUDIT_LOG}
    ),
    Role.DOCTOR: frozenset({Permission.LIST_OWN_PATIENTS}),
    Role.PATIENT: frozenset(),
    Role.CAREGIVER: frozenset(),
}
