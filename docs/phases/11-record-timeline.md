# Phase 11: Medical record timeline

## What it shows
One chronological list per patient (`GET /patients/{id}/timeline`) across these record types:

| Kind | Source | Permission (a doctor's is narrowed by consent) |
|---|---|---|
| `visit` | Doctor visits | `view_visits` |
| `note` | Signed clinical notes and amendments (drafts only for their author) | `view_visits` |
| `symptom` | Symptoms reported by the patient, a caregiver or a doctor (new `symptom_reports`) | `view_medical_history` |
| `assessment` | Diagnoses/assessments documented by a doctor | `view_medical_history` |
| `reported_condition` | Conditions the patient or a caregiver reported | `view_medical_history` |
| `prescription` | Issued/recorded prescriptions (drafts only for the prescriber) | `view_prescriptions` |
| `medication` | Medicines not from a prescription | `view_medications` |
| `test_order` | Tests ordered | `view_reports` |
| `report` | Test reports (or only the ones shared with this doctor, Phase 12) | `view_reports` or a live share |
| `appointment`, `follow_up` | Appointments and follow-ups | `view_appointments` |
| `document` | Uploaded health documents not attached to a report or prescription | `view_reports` **and** the permission for its type |

- **Missing permissions:** record types the caller has no permission or consent for are left out entirely.
- **Unrelated doctors:** get 404, like every patient route.
- **Titles:** say what was recorded and by whom. They never summarise or interpret clinical content.

## Filters and pagination
- **Query parameters:** `kind` (repeatable), `date_from`, `date_to`, `doctor_id`, `specialty` (case-insensitive, primary specialty), `order=newest|oldest`, `limit` (≤200), `cursor`.
- **Cursor:** opaque (time and key). Following it returns every record exactly once, in order.
- **`facets`:** counts of kinds, doctors and specialties, plus the date range of everything the caller may see. They are counted before filters, so the UI can offer only options that exist.
- **Doctor:** each event carries `doctor {id, name, specialty}`. It comes from the visit, the author, the prescriber, the ordering doctor, or the user who created a doctor-sourced row.

## Clinical records are never edited silently
- **Automatic versions:** migration `0011` adds `record_versions` and the `hio_record_version()` trigger.
  - Every change to a tracked record copies the old row into `record_versions`, with the changed columns, who changed it and the reason. This holds even for raw SQL.
  - Tracked tables: conditions, allergies, history entries, visits, symptoms, documents, test orders, test reports, appointments, follow-ups and report shares.
  - Bookkeeping (`updated_at`, `version`, a document's scan state) does not create versions.
  - Encrypted columns stay encrypted in the snapshot, and the history view decrypts them.
  - Versions are append-only (`HI001`) and deletable only by the erasure workflow.
- **Reasons:** corrections go through `change_reason(session, reason)`, which sets the transaction-local `hio.change_reason` for the trigger.
- **Superseding records:** signed notes, issued prescriptions and verified reports are frozen (migration 0002), and their correction chains appear in the history.
- **History endpoint:** `GET /patients/{id}/timeline/{kind}/{resource_id}/history` lists, oldest first:
  - created;
  - each change (field, before, after, who, when, why);
  - for notes and prescriptions, the amendment or revision chain.

  It returns 404 for records the caller cannot see. The timeline marks corrected records (`amended`) and records with history (`has_history`).

## Symptoms (new)
- **Patient or caregiver:** `POST /symptoms` (`report_health_info`). Saved in their words and labelled with who reported it.
- **Doctor:** `POST /symptoms/documented` (`edit_clinical_records`, verified doctor), optionally linked to a visit.
- **Corrections:** `PATCH /symptoms/{id}` needs `version` (optimistic lock, 409 if stale) and a `reason`.
  - Patients and caregivers correct only what they reported; doctors only what doctors recorded (403 otherwise).
  - "Entered by mistake" sets `entered_in_error`, which can't be changed afterwards.

## Documents
- **Access by type:** a document is visible only with `view_reports` plus the permission for its type:
  - prescription → prescriptions;
  - discharge summary or consultation note → visits;
  - vaccination record or certificate → medical history.
- **Where it applies:** the documents list, the download and the timeline. Hidden documents return 404.
- **Corrections:** `PATCH /documents/{id}` changes title, type, description or date with a reason, and only by the side that added the document. The file itself is never replaced.

## Web
- **Patient view** (`/patient/timeline`, and "Timeline" for each person a caregiver looks after):
  - grouped by month, with plain-language headlines ("You saw Dr …", "You reported: …");
  - specialty chips and who added each record;
  - Correct (own symptoms) and History buttons;
  - "Report a symptom", which reminds the person to get help for severe or sudden symptoms.
- **Doctor view** (chart → Timeline tab):
  - compact rows grouped by day, with record type, time, author and specialty, status and "Amended";
  - links into the chart, History, and "Record a symptom".
- **Both views:**
  - record-type chips with counts;
  - a filter panel (date range, doctor, specialty) and a newest/oldest toggle;
  - a result count and "Show older records" (cursor paging).
- **Chart overview:** "Recent activity" uses the same API.

## Tests
- **API, `tests/db/test_records_timeline.py`:**
  - order, facets, every filter, and a full cursor walk;
  - consent narrowing, hidden history, 404 for unrelated doctors;
  - symptom corrections (reason, stale version, doctor can't edit the patient's words, decrypted history, no plaintext in versions);
  - raw SQL edits still versioned, versions append-only;
  - document access by type and versioned corrections.
- **Unit tests:** `tests/test_records_and_reports.py` (cursor, filters, paging).
- **Web:** `features/timeline/timeline.test.tsx` covers both views, server-side filters and the history dialog.

## Not yet
- FHIR R4 mapping of timeline records (ABDM groundwork) moves to Phase 20.
- Allergies and medical-history entries are versioned but not yet shown as timeline events.
- The timeline is assembled in memory per request. A materialised timeline table is planned for Phase 23 load tests if large records need it.
