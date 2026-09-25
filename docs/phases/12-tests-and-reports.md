# Phase 12: Tests and reports

## Doctors
- **Order tests:** `POST /test-orders` with the **reason for the test** (`clinical_indication`, encrypted), priority and due date.
- **Mark status:** `POST /test-orders/{id}/status`.
  - Allowed moves: ordered → sample collected → partly resulted → completed.
  - "Entered in error" is possible from any of these and needs a note.
  - Cancelling (`/cancel`) needs a reason.
  - Each order shows `next_statuses`. Every change is kept in the order's history with its note.
- **Record or reference a report:** `POST /reports` takes either a file the doctor uploads or an existing report document (`document_id`), plus:
  - metadata: test name (defaults to the ordered tests), date on the report, laboratory, report number, notes;
  - optionally, values exactly as printed.

  The report is verified by that doctor.
- **Review uploaded reports:** `POST /reports/{id}/review`.
  - `verify` confirms the file is this patient's report and its details match. The report is then frozen.
  - `reject` needs a note, which the patient sees.
  - Verifying is explicitly **not** an interpretation of the result.
- **Entered in error:** `POST /reports/{id}/entered-in-error` works on any open report, with a reason.

## Patients (and caregivers with the scopes)
- **View:** ordered tests with reason, ordering doctor and status (`view_reports`).
- **Upload a report:** `upload_reports`, in two steps:
  1. Upload the file through the documents API as `lab_report`.
  2. Call `POST /reports/uploaded` with test name (or the order it answers), date, laboratory, report number and notes.

  The report is labelled "uploaded by you/caregiver" and stays **pending review**. Nothing reads values out of it.
- **View a report:** its details, a file link (`GET /reports/{id}/file`, 60 s), values as printed, and a reminder that Health Io never interprets results.
- **Withdraw:** your own upload, while it is still pending, with a reason. It stays in the record, marked withdrawn.
- **Share a report** (`manage_consent`, so the patient or a guardian, never a caregiver or doctor):
  - `POST /reports/{id}/shares` shares one report with one doctor on the care team, optionally until a date. `DELETE /report-shares/{id}` stops sharing.
  - A doctor without tests-and-reports consent then sees **only that report**: in the report list (`access: "shared"`), its detail, its file and the timeline. Orders and other reports stay hidden.
  - Shares are versioned and audited.

## Report metadata (migration 0012)
`test_reports` gains:
- `test_name`, `report_date`, `lab_reference`, `notes` (encrypted);
- the review fields `reviewed_at`, `reviewed_by`, `review_note`. A rejected or withdrawn report must carry a note (check constraint).

The freeze on verified reports still applies. Only the status and review fields may change, so a verified report can be marked entered in error, with who and why.

## Files: storage, validation, malware, authorization, audit
- **Storage interface:** `app.core.storage.Storage` (a Protocol).
  - `S3Storage` works with MinIO in development and S3 ap-south-1 in production. Tests use an in-memory fake.
  - Presigned POST uploads pin the key, content type, size (15 MB) and server-side encryption.
  - Keys never contain personal data. Downloads are 60-second attachment links.
- **Validation:** checked when the upload is started and again when it is completed.
  - Reports: **PDF, JPEG or PNG only**. Other documents also accept phone photos (WebP, HEIC).
  - Size limit, and magic bytes must match the declared type.
  - **PDFs with active content** (JavaScript, launch actions, embedded files, XFA) are quarantined.
- **Malware scanning:**
  - `app.core.malware.MalwareScanner` is the integration point. `ClamdScanner` speaks clamd's INSTREAM protocol.
  - Verdicts: clean → usable; infected → QUARANTINED (422); scanner error → PENDING_SCAN (never usable until a later scan).
  - The `records.scan_pending` beat job (every 2 min) scans files waiting for a verdict and marks objects that changed or disappeared as FAILED.
  - Configuration: `HIO_CLAMAV_HOST`/`PORT`, and `HIO_UPLOAD_VIRUS_SCAN_REQUIRED=true` in production.
  - Local ClamAV: `docker compose -f docker-compose.dev.yml --profile scan up -d clamav`.
- **Authorization:**
  - Every route declares its policy.
  - Documents also need the permission for their type (Phase 11).
  - Report reads work with `view_reports` or a live share.
  - Uploading, reviewing and sharing each need their own permission and side (patient/caregiver vs doctor).
- **Audit:** these events are in the patient's hash-chained audit log:
  - `document.upload_started`, `.upload_completed` (with scanner and verdict), `.upload_rejected` (with reason), `.download`, `.correct`, `document.scanned` (system);
  - `test_report.upload`, `.view`, `.file_download`, `.review`, `.entered_in_error`, `.share`, `.share_revoked`;
  - `test_order.status`.

## AI and reports
- **No AI diagnosis from reports:** nothing in this phase calls a model on a report.
- **Architecture for summaries:** `app/ai/report_summary.py` defines how summaries must work, but is disabled.
  - Input is built only from **verified** reports and only printed values (no identifiers, notes or conclusions).
  - The output schema has no field for a diagnosis, cause or treatment.
  - `check_summary()` rejects summaries that:
    - mention values or tests not on the report;
    - claim a flag the report did not print;
    - contain a number the report did not print;
    - use diagnostic or treatment language.
  - Rejected summaries are discarded. Anything shown carries a fixed "not a diagnosis" disclaimer, next to the original.
- **Details:** see `docs/ai/report-summaries.md`.

## Web
- **Patient, Tests & reports page:**
  - ordered tests with their reason and linked reports;
  - reports with source, status, and a detail dialog (file, metadata, values as printed, withdraw, sharing);
  - "Upload a report" (PDF, JPG or PNG only, 15 MB, pick an ordered test or name it);
  - "Other document" for everything else.
- **Doctor chart:**
  - Tests tab: status menu, cancel, and entered in error with a reason;
  - Reports tab: a banner for reports waiting for review, a Review/Details dialog (verify, reject with reason, entered in error), and "Shared with you" badges;
  - the report form gains test name, report date, report number and notes, and accepts PDF, JPG or PNG only.

## Tests
- **API, `tests/db/test_records_timeline.py`:**
  - type, size, fake-PDF and scripted-PDF rejection;
  - infected, error, then background scan to clean, with audit verdicts;
  - order → upload → status → review → frozen → entered in error;
  - doctor-recorded report with metadata;
  - per-report sharing (non-linked doctor, past expiry, doctor can't share, shared-only visibility, revoke);
  - caregiver scopes.
- **Unit tests:** `tests/test_records_and_reports.py`:
  - clamd replies, and a fake clamd server that checks the INSTREAM framing;
  - active-content detection and document permissions;
  - summary guardrails (only verified input, disabled, rejects diagnostic, invented or unprinted-flag output);
  - migration and model table lists stay in sync.
- **Web:** `features/reports/reports.test.tsx` covers file type and size checks, the upload payload, sharing and revoking, withdrawing, and verify/reject.
- **Totals:** 267 API tests (with the integration suite and MinIO) and 67 web tests pass.

## Not yet
- Reading values out of lab reports (OCR/AI) with patient confirmation, trend charts, and abnormal-value highlighting. These are planned as a later phase that builds on the summary guardrails.
- Doctors ordering from a test catalogue (LOINC); orders are by name today.
- Sharing with caregivers or external clinicians by link (Phase 17/20).
