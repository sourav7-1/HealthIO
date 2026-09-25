import {
  CalendarClock,
  CalendarDays,
  ClipboardPlus,
  FileText,
  FlaskConical,
  HeartPulse,
  NotebookPen,
  Paperclip,
  Pill,
  Stethoscope,
  TestTube,
  Thermometer,
  type LucideIcon,
} from "lucide-react";

import type { TimelineEvent, TimelineKind } from "./api";

export interface KindMeta {
  /** Filter chip label in the doctor view. */
  label: string;
  /** Filter chip label in the patient view (plain words). */
  friendly: string;
  icon: LucideIcon;
  /** Dot/icon colour classes (tokens only, work in both themes). */
  tone: string;
}

export const KINDS: Record<TimelineKind, KindMeta> = {
  visit: { label: "Visits", friendly: "Doctor visits", icon: Stethoscope, tone: "bg-accent/12 text-accent" },
  symptom: { label: "Symptoms", friendly: "Symptoms", icon: Thermometer, tone: "bg-warning/12 text-warning" },
  note: { label: "Clinical notes", friendly: "Visit notes", icon: FileText, tone: "bg-accent/12 text-accent" },
  assessment: { label: "Assessments", friendly: "Doctor's assessments", icon: NotebookPen, tone: "bg-info/12 text-info" },
  reported_condition: { label: "Reported conditions", friendly: "Conditions you reported", icon: HeartPulse, tone: "bg-warning/12 text-warning" },
  prescription: { label: "Prescriptions", friendly: "Prescriptions", icon: ClipboardPlus, tone: "bg-success/12 text-success" },
  medication: { label: "Medicines", friendly: "Medicines", icon: Pill, tone: "bg-success/12 text-success" },
  test_order: { label: "Test orders", friendly: "Tests ordered", icon: FlaskConical, tone: "bg-info/12 text-info" },
  report: { label: "Reports", friendly: "Test reports", icon: TestTube, tone: "bg-info/12 text-info" },
  appointment: { label: "Appointments", friendly: "Appointments", icon: CalendarDays, tone: "bg-accent/12 text-accent" },
  follow_up: { label: "Follow-ups", friendly: "Follow-ups", icon: CalendarClock, tone: "bg-accent/12 text-accent" },
  document: { label: "Documents", friendly: "Documents", icon: Paperclip, tone: "bg-surface-2 text-muted" },
};

export const KIND_ORDER = Object.keys(KINDS) as TimelineKind[];

/**
 * Plain-language headline for the patient view. It rephrases what was recorded; it never
 * interprets clinical content (that is shown as written in `detail`).
 */
export function friendlyTitle(e: TimelineEvent, self: boolean): string {
  const you = self ? "You" : "They";
  const doctor = e.doctor?.name ?? (self ? "your doctor" : "a doctor");
  const after = (prefix: string) => e.title.slice(e.title.indexOf(":") + 1).trim() || prefix;
  switch (e.kind) {
    case "visit":
      return `${self ? "You saw" : "Saw"} ${doctor}`;
    case "note":
      return `${doctor} wrote ${e.amended ? "a corrected" : "a"} visit note`;
    case "symptom":
      return e.source === "doctor" ? `${doctor} noted: ${after("a symptom")}` : `${you} reported: ${after("a symptom")}`;
    case "assessment":
      return `${doctor}'s assessment: ${after("recorded")}`;
    case "reported_condition":
      return `${you} added a condition: ${after("recorded")}`;
    case "test_order":
      return `${doctor} ordered tests`;
    case "report":
      return e.title.startsWith("Report uploaded") ? `Report added: ${after("test")}` : `Report from ${after("the lab")}`;
    case "appointment":
      return `Appointment with ${doctor}`;
    case "follow_up":
      return `Follow-up with ${doctor} due`;
    case "document":
      return after("Document added");
    default:
      return e.title;
  }
}

export const SOURCE_LABEL: Record<string, string> = {
  doctor: "Doctor",
  patient: "Patient",
  caregiver: "Caregiver",
  ai_extraction: "Read from a photo, checked by a person",
  integration: "Lab or hospital system",
  system: "Health Io",
  doctor_issued: "Doctor",
  ocr_upload: "Uploaded photo",
  manual: "Patient",
  self_reported: "Patient",
  clinician_recorded: "Doctor",
  prescription: "Doctor",
};
