import type { TimelineEvent } from "./api";

/** Where a record opens in the doctor's chart. */
export function doctorLink(e: TimelineEvent, patientId: string): string | null {
  const base = `/doctor/patients/${patientId}`;
  switch (e.kind) {
    case "visit":
    case "note":
      return e.visit_id ? `${base}/visits/${e.visit_id}` : null;
    case "symptom":
    case "assessment":
      return e.visit_id ? `${base}/visits/${e.visit_id}` : `${base}/overview`;
    case "reported_condition":
      return `${base}/overview`;
    case "prescription":
      return `${base}/prescriptions/${e.resource_id}`;
    case "medication":
      return `${base}/medications`;
    case "test_order":
      return `${base}/tests`;
    case "report":
    case "document":
      return `${base}/reports`;
    case "appointment":
      return `${base}/appointments`;
    case "follow_up":
      return `${base}/follow-ups`;
    default:
      return null;
  }
}

/** Where a record opens in the patient portal (or a caregiver's view of the person). */
export function patientLink(e: TimelineEvent, base: string): string | null {
  switch (e.kind) {
    case "visit":
    case "note":
      return e.visit_id ? `${base}/visits/${e.visit_id}` : `${base}/visits`;
    case "symptom":
    case "assessment":
    case "reported_condition":
      return `${base}/history`;
    case "prescription":
      return `${base}/prescriptions/${e.resource_id}`;
    case "medication":
      return `${base}/medications/${e.resource_id}`;
    case "test_order":
    case "report":
    case "document":
      return `${base}/tests`;
    case "appointment":
    case "follow_up":
      return `${base}/appointments`;
    default:
      return null;
  }
}
