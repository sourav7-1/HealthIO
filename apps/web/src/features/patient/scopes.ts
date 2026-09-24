import type { Schemas } from "@/lib/api";

export type Scope = Schemas["CaregiverPermissionScope"];

/** Caregiver permissions in plain words, grouped from "see" to "do". None of them lets a
 * caregiver change a doctor's records or prescriptions; those cannot be granted at all. */
export const SCOPES: { value: Scope; label: string; group: "See" | "Do" | "Manage" }[] = [
  { value: "view_profile", label: "See basic details", group: "See" },
  { value: "view_medications", label: "See medicines and today's doses", group: "See" },
  { value: "view_prescriptions", label: "See prescriptions", group: "See" },
  { value: "view_adherence", label: "See how regularly medicines are taken", group: "See" },
  { value: "view_medical_history", label: "See medical history and allergies", group: "See" },
  { value: "view_visits", label: "See doctor visits and notes", group: "See" },
  { value: "view_appointments", label: "See appointments and follow-ups", group: "See" },
  { value: "view_reports", label: "See test reports and documents", group: "See" },
  { value: "receive_alerts", label: "Get alerts about missed doses", group: "See" },
  { value: "log_doses", label: "Mark doses as taken or skipped", group: "Do" },
  { value: "manage_reminders", label: "Set up medicine reminders", group: "Do" },
  { value: "report_health_info", label: "Add medicines, allergies and conditions (labelled as theirs)", group: "Do" },
  { value: "upload_reports", label: "Upload documents", group: "Do" },
  { value: "manage_appointments", label: "Manage appointments", group: "Do" },
  { value: "manage_emergency_info", label: "Update the emergency profile", group: "Do" },
  { value: "use_ai_assistant", label: "Use the health assistant", group: "Do" },
  { value: "manage_caregivers", label: "Manage other caregivers (guardians only)", group: "Manage" },
];

export function scopeLabel(scope: string): string {
  return SCOPES.find((s) => s.value === scope)?.label ?? scope;
}

/** `label` is the declaration in the first person; `other` describes someone else. */
export const DEPENDANT_BASES: { value: Schemas["DependantBasis"]; label: string; other: string; minor: boolean }[] = [
  { value: "parent_of_minor", label: "I am their parent", other: "Parent", minor: true },
  { value: "legal_guardian_of_minor", label: "I am their legal guardian", other: "Legal guardian", minor: true },
  { value: "power_of_attorney", label: "I hold a power of attorney for them", other: "Holds a power of attorney", minor: false },
  { value: "court_appointed_guardian", label: "I am their court-appointed guardian", other: "Court-appointed guardian", minor: false },
  { value: "adult_consented", label: "They have agreed that I manage their health record", other: "Agreed by the person", minor: false },
];

export function basisLabel(basis: string | null | undefined): string | null {
  return DEPENDANT_BASES.find((b) => b.value === basis)?.other.toLowerCase() ?? null;
}
