# Phase 7: Prescription management

## What a prescription holds
| Field | Where |
|---|---|
| Patient, doctor (prescriber), date | `patient_id`, `prescriber_doctor_id`, `prescribed_on` |
| Diagnosis / assessment **as documented** | `diagnosis_as_written`, encrypted; never inferred |
| Notes and advice | `advice`, encrypted |
| Follow-up | `follow_up_on` + `follow_up_instructions`, encrypted. Issuing creates a follow-up task linked to the prescription |
| Items | Medicine as written, generic name if known, strength, form, route, dose (amount and unit), frequency as written (e.g. `1-0-1`), duration, meal relation, as-needed with reason, instructions |
| Version | `revision` (1 = original), `revision_reason`, `supersedes_prescription_id` |
| Fingerprint | `content_sha256`: SHA-256 of the canonical issued content, printed on exports |

Migration `0007` adds the version, follow-up and fingerprint columns, with these checks:
- revision 1 if and only if there is no predecessor;
- a correction needs a reason;
- the fingerprint must be hex.

## Immutable versions
- **Draft:** editable, and visible only to the prescribing doctor.
- **Issued:** frozen. The DB trigger from migration 0002 rejects any change to the row or its items (HI001). The API returns 409 on edits.
- **Correction:**
  1. `POST …/prescriptions/{id}/revisions`, with a reason, creates a **new draft** at revision + 1 that points to the issued version. Only one correction at a time. Only the prescriber can start it.
  2. Issuing the correction sets the old row's status to `superseded`, the only change its trigger allows. The old content and fingerprint stay exactly as they were.
  3. The medicines started by the old version stop, recorded as stopped by the doctor with the reason. Their future reminders are cancelled, and their open follow-ups are closed.
  4. The corrected medicines wait for the patient to confirm them, as with any new prescription.
- **Cancellation** keeps the prescription in the record with the reason, and now also stops its medicines.
- Nothing is changed silently. Every version stays readable, and each document lists its version history.

## Views
- **Professional document view:** used by all three portals through the shared `features/chart/PrescriptionView.tsx`.
  - Header: prescriber, qualifications, registration number and council, practice.
  - Patient line; diagnosis as documented; a ℞ table with generic name, dose, frequency, food, duration and instructions.
  - Notes, follow-up and validity.
  - Footer: "Electronically issued" with the fingerprint.
  - Non-current versions show a warning banner and a watermark. The page links to every version.
- **Patient portal:** `/patient/prescriptions` shows current versions; each opens `/patient/prescriptions/:id`.
- **Caregiver portal:** caregivers with `view_prescriptions` use the same pages under `/care/:pid/…`. The patient line appears only if `view_profile` is also shared.
- **Doctor chart:** the Prescriptions tab adds **View** and **Correct**. The prescription form has generic name, notes and advice, follow-up, and a "What is being corrected?" field for corrections. The page is `/doctor/patients/:pid/prescriptions/:id`.

## PDF export architecture
```
GET /patients/{pid}/prescriptions/{id}/pdf       (view_prescriptions; audited "prescription.export")
      │
      ▼
_build_document()  →  PrescriptionDocument (app/modules/prescriptions/document.py)
      │                 one render model, also returned as JSON by GET …/{id}
      ▼
RENDERERS["pdf"].render(doc) → bytes     (DocumentRenderer protocol)
      │   ReportLabPdfRenderer (app/modules/prescriptions/pdf.py)
      ▼
application/pdf, Content-Disposition: attachment, Cache-Control: no-store
```
- **One render model.** The web view and the PDF can't disagree, and renderers only lay out what the model contains.
- **Warnings print too:** non-current versions carry a banner and watermark on every page. Every page also prints the fingerprint and version.
- **Fonts:** the built-in font is Latin-1 only. Set `HIO_PDF_FONT_PATH` (and `HIO_PDF_FONT_BOLD_PATH`) to a Unicode TTF such as Noto Sans for other scripts. Without it, those characters are replaced in the PDF only; the web view shows the original text.
- **Planned:**
  - PAdES digital signature with the doctor's certificate, after step-up MFA.
  - Rendering in a worker, with the file cached in object storage by `content_sha256`.
  - A FHIR `MedicationRequest` renderer for ABDM.
  - A verification page for the fingerprint.

## Audit
- Every change: `prescription.create_draft`, `.update_draft`, `.revision_started`, `.issue` (with revision and item count), `.superseded` (on the old version, with the successor and the number of medicines stopped), `.cancel`, `.discard_draft`.
- Every read: `prescription.list`, `prescription.read`, and exports as `prescription.export` (with format, revision and status).
- Each event carries `via` (doctor, self or caregiver).

## Tests
- **API, `tests/db/test_prescriptions.py` (6 tests):**
  - a complete prescription, its follow-up task and the document fields;
  - follow-up validation;
  - an issued prescription is immutable through the API (409) and in the database (HI001, for the row and its items);
  - the full correction flow: reason required, one correction at a time, the draft hidden from the patient, the old version superseded and unchanged, the old medicine stopped, the new one pending, the version list, a superseded version cannot be corrected again, the audit events, and the DB chain check;
  - the patient and a `view_prescriptions` caregiver can view and export, but get 403 on correct, cancel and edit, and the patient line is withheld without `view_profile`;
  - only the prescriber can correct.
- **API, `tests/test_prescription_document.py`:** the PDF renders for every status, text is escaped, text outside Latin-1 works without a Unicode font, banners appear on non-current versions, and the fingerprint is deterministic and changes with the content.
- **Web, `features/chart/prescription.test.tsx`:** all fields as written, the superseded watermark, patient details not shared, and the correction reason.
- **Totals:** 141 API tests and 21 web tests pass.

## Not yet
- AI OCR of paper prescriptions (Phase 8, not started, as requested).
- Signed PDFs, and step-up MFA before issuing.
- Drug catalogue search and safety checks (Phase 14).
- Carrying reminder times over to corrected medicines: the patient re-confirms them.
- Playwright E2E tests.
