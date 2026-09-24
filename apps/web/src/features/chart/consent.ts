/** Consent vocabulary shown to patients when sharing data with a doctor. */

/** The privacy notice version shown to the patient (must match the published notice). */
export const PRIVACY_NOTICE_VERSION = "2026-09-en";

export const DATA_CATEGORIES: { value: string; label: string; description: string }[] = [
  { value: "demographics", label: "Name and basic details", description: "Name, date of birth, sex" },
  { value: "conditions", label: "Medical history", description: "Conditions and diagnoses" },
  { value: "allergies", label: "Allergies", description: "Known allergies and reactions" },
  { value: "medications", label: "Medicines", description: "Current and past medicines" },
  { value: "prescriptions", label: "Prescriptions", description: "Prescriptions you write" },
  { value: "visits_and_notes", label: "Visits and notes", description: "Consultations and clinical notes" },
  { value: "tests_and_reports", label: "Tests and reports", description: "Test orders and results" },
  { value: "documents", label: "Documents", description: "Uploaded health documents" },
  { value: "adherence", label: "Adherence", description: "Whether medicines are taken as scheduled" },
  { value: "appointments", label: "Appointments", description: "Appointments and follow-ups" },
];
