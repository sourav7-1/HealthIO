# Phase 8: AI prescription reading

A patient (or permitted caregiver) photographs a paper prescription. AI reads it **only with the patient's consent**, and a person checks every field it flags against the photo. Only then does it become a prescription in the record. With no consent, or no AI configured, the same screen is used to type the prescription in next to the photo.

AI extraction is transcription, **not medical decision-making**.

## Pipeline
```
photo ─► health_documents (original kept in S3; type, size and magic-byte checks)
      ─► preprocessing (app/ai/preprocess.py): EXIF orientation, flatten, downscale ≤2000 px,
         re-encode JPEG without metadata (location etc.); greyscale + autocontrast for OCR
      ─► OCR (app/ai/ocr.py): optional Tesseract; words + boxes + confidence
      ─► vision model (app/ai/providers.py): Claude, forced tool call with a strict schema,
         image + OCR text (as untrusted data); invalid output retried once, then "failed"
      ─► schema guard (app/ai/schemas.py): fields outside the schema (e.g. an invented
         "diagnosis") are discarded; their names are recorded
      ─► confidence estimation (app/modules/extraction/assessment.py)
      ─► needs_review ─► person confirms / corrects / marks "not on the prescription"
      ─► conversion (app/modules/extraction/service.py): Prescription(source=uploaded,
         status=recorded, patient- or doctor-verified) + medicines awaiting reminder setup
```
**Fields extracted:**
- Per medicine: name, strength, dose, frequency, duration, meal relation, instructions.
- Doctor name, registration number and clinic, only when clearly visible.
- Prescription date.

There is no diagnosis field. Every field carries `value`, `confidence`, `legibility`, `evidence` (the verbatim text), `region` (a box on the photo, from OCR when found, otherwise from the model), `flags`, and a review `status`:
- `unverified`
- `confirmed` (the value is what the AI read)
- `corrected`
- `not_on_prescription`

## Confidence and "never guess"
**Bands** (AI_SAFETY.md §4.2):

| Band | Confidence | What happens |
|---|---|---|
| High | ≥ 0.90 | Shown as read. |
| Medium | 0.60–0.90 | Amber; must be confirmed. |
| Low | < 0.60 | **"Could not confidently read this field."** The box is left **empty**. The uncertain reading appears only when the person asks, and it still has to be confirmed. |

**Confidence is only ever lowered, and each reduction is recorded as a flag:**

| Flag | Condition | Cap |
|---|---|---|
| `no_evidence` | No verbatim evidence | 0.59 |
| `value_not_in_evidence` | Numbers in the value that are not in the evidence (a guessed or invented dose) | 0.30 |
| `ocr_disagrees` | OCR can't find the evidence for a critical field | 0.59 |
| `marked_illegible` | Marked illegible yet has a value | 0.30 |
| `partly_legible` | Partly legible | 0.59 |
| `handwritten` | Handwritten critical field | 0.89 (medium at best) |

**Rules:**
- Medicine, strength, dose and frequency **always** need an explicit decision.
- Other fields need one unless they were read at high confidence (or confidently absent).
- **Missing values stay missing.** A dose marked "not on the prescription" is saved as no dose. The dictionary-based interpretation (below) never fills a value.
- **Never silently modified:** the AI reading is stored once and frozen by a DB trigger. The person's review is stored separately, with per-field history (action, previous value, who, when).
- **Deterministic interpretation** (`normalize.py`) uses fixed dictionaries only:
  - `1-0-1`, `BD`, `TDS`, `SOS`;
  - `AC`/`PC`;
  - `5 days`, `5/7`, `2/52`;
  - `1 tab`;
  - day-first dates, never in the future.

  The meaning is shown next to the text ("Means: 1 in the morning, 1 at night"). Text that doesn't match is saved as written. For example, "1 month" is not turned into days; it is saved as "Duration as written: 1 month".
- The prompt (`app/ai/prompts/prescription_extraction_v1.md`):
  - forbids guessing, inferring doses and diagnosing;
  - treats the text in the image and the OCR as data, never instructions;
  - asks for reading problems as notes.

## Consent and who may verify
- **Consent:**
  - AI reading needs the platform consent `ai_processing` (notice version `2026-09-ai-en`).
  - It can be given by the patient, or by a dependant's guardian, who now holds `manage_consent` for dependants (DPDP s.9).
  - It can be withdrawn at any time: *Settings → AI reading of prescriptions*.
  - Without consent, nothing is sent to the model.
- **Verifying:**
  - The patient, a caregiver with `report_health_info` (both saved as `patient_verified`), or a linked doctor with `edit_clinical_records` (saved as `doctor_verified`).
  - Viewing needs `view_reports`; starting a scan needs `upload_reports`.
- **Labelling:** uploaded prescriptions are labelled everywhere, including the document view and the PDF: "Transcribed from an uploaded prescription photo and checked by … not a prescription issued on Health Io".

## Stored
Table `prescription_scans`, migration `0008`:
- **Original image:** a reference to the health document, plus the SHA-256 of the image.
- **AI output:** the reading and its assessment in `result`, and the OCR text. Both are encrypted and frozen.
- **Model metadata:** provider, model, prompt version, OCR engine, preprocessing steps, latency, tokens, attempts, response ID.
- **Review:** the review JSON, encrypted.
- **Outcome:** status, verifier and role, verification level, the prescription created, and the rejection reason.

**Triggers:**
- The AI result and metadata are frozen once stored.
- Only these status transitions are allowed: queued → running → needs_review → verified/rejected, and failed.
- Verified, rejected and failed scans are final.
- Rows are never hard-deleted.
- One open scan per photo.

**Audit** records paths and statuses only, never values:
- `consent.ai_processing_granted` / `_withdrawn`
- `prescription_scan.created`, `.read_by_ai` / `.failed` (model, band counts), `.reviewed` (e.g. `items.i1.strength:corrected`), `.verified` (role, item count, corrected count), `.rejected`, `.read`, `.list`
- `prescription.recorded_from_scan`

## Running it
```bash
# apps/api/.env
HIO_AI_PROVIDER=anthropic
HIO_ANTHROPIC_API_KEY=...        # zero-retention settings required before production
HIO_AI_OCR_ENGINE=tesseract      # optional; needs the tesseract binary
HIO_AI_JOBS_INLINE=false         # production: Celery task ai.process_prescription_scan
```
With the default `HIO_AI_PROVIDER=disabled`, the upload offers **Type it in** only. No sample or demo readings are ever shown.

**Web screens:**
- **Add a paper prescription** (patient and caregiver *Prescriptions*): take or choose a photo, then *Read it with AI* (with a consent tick box) or *Type it in*.
- **Review screen:**
  - The photo, with the field's region highlighted on hover or focus.
  - A crop of the photo next to each field, and confidence labels.
  - "Show what the AI thought it said" for unclear fields.
  - Add or remove lines; save is enabled only when nothing is outstanding.
- **Doctors** see "Paper prescriptions waiting to be checked" on the patient's Prescriptions tab.

## Tests
All tests use synthetic images and placeholder text. No real patient data is used, and no network calls are made.
- **`tests/test_prescription_ai.py` (28 tests):**
  - the normalisation dictionaries, and that unknown text stays uninterpreted;
  - the confidence caps (no evidence, a value not in the evidence, OCR disagreement, handwriting, illegible, absent);
  - low-confidence fields are not pre-filled;
  - an invented diagnosis is discarded and recorded;
  - a malformed region is dropped;
  - a missing dose stays missing, and unrecognised text is saved verbatim;
  - preprocessing applies EXIF orientation, strips metadata, and refuses PDFs, tiny images and broken files.
- **`tests/test_prescription_eval.py` + `tests/evals/prescriptions/cases.json`:** eval gates.
  - No wrong critical value is ever banded "high", including a guessed dose, a misread digit, an OCR disagreement, handwriting and an expanded abbreviation.
  - Clean printed readings stay "high".
  - `scripts/eval_prescriptions_live.py` runs the same set against the real model (costs credits; it has not been run yet).
- **`tests/db/test_prescription_scans.py` (6 tests):**
  - consent gating and withdrawal (nothing is sent without consent);
  - the full flow: low field not pre-filled, 422 until critical fields are decided, the correction trail, conversion without invented dose or diagnosis, encryption at rest, the frozen AI result, the audit sequence with no values;
  - manual entry;
  - failure then retry, one open scan per photo, reject;
  - AI disabled;
  - verifier roles (viewer 403, caregiver → patient-verified, doctor → doctor-verified), and 404 for strangers.
- **Web, `features/scans/scans.test.tsx`:**
  - a low-confidence field is empty and shows the message;
  - the uncertain reading is used only on request and must still be saved;
  - confirm, correct and "not on the prescription" actions;
  - read-only for non-verifiers;
  - save is blocked while issues remain;
  - warnings for handwriting, notes and discarded data.

## Not yet
- Matching against the drug catalogue, and look-alike/sound-alike confirmation (Phase 14; unmatched names are saved as written).
- The medication safety engine before activation (Phase 14). For now, medicines from uploads wait for the patient to set reminders, as with any prescription.
- PDF and HEIC uploads for AI reading (photos only for now), and multi-page prescriptions.
- A live eval run with a real API key; Tesseract in the Docker image; Hindi OCR data.
- Provider contract and zero-retention configuration before production (see the DPDP register).
