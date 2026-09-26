# Phase 14: Medication safety layer

## Flow
```
current medicines + prescription lines + allergies + conditions
        │
        ▼
  safety engine (app/modules/safety/engine.py): deterministic rules, no AI
        │   uses only trusted reference data (app/modules/safety/reference.py)
        ▼
  potential warning: stored with source, severity, time, review status
        │
        ▼
  doctor review (with a note) · patient/caregiver acknowledgement
```
Every warning is headed **"Potential issue detected. Please confirm with a doctor/pharmacist."** The patient-facing text always ends with "Please don't stop, skip or change any medicine on your own because of this." A test checks that no warning text instructs a change (it reuses the assistant's forbidden-advice check).

## What is checked
| Check | Needs reference data? | How |
|---|---|---|
| Duplicate medicines | No | Same normalised name among current medicines and prescription lines |
| Duplicate active ingredients | Only for brand names | Ingredients come from the recorded generic name (e.g. "A + B"), or from a dataset product (by drug code or name). **Unknown ingredients stay unknown.** No guessing from brand names; the doctor preview lists medicines it couldn't check. |
| Drug-drug interactions | **Yes** | Only ingredient pairs an active dataset lists. The severity is the dataset's own |
| Contraindications | **Yes** | Dataset ingredient × ICD-10 prefix, against conditions recorded with an ICD-10 code (active, not refuted) |
| Allergy conflicts | Exact match: no. Class match: **yes** | An exact ingredient/name or code match with an active allergy (serious). The same cross-sensitivity class in a dataset (caution) |
| Inconsistent prescription details | No (Health Io consistency rules) | Written frequency ("1-0-1", BD, TDS, SOS…) vs doses per day; "when needed" with no reason, or with a fixed frequency; dose without unit; quantity less than dose × frequency × duration; end before start |

**Severity:**
- `info`: the source rates it minor.
- `caution`: the source rates it moderate, it is a duplicate, or an allergy class matches.
- `serious`: the source rates it major or contraindicated, or an allergy matches exactly.

The source's own rating is stored as `source_severity`.

## Trusted structured data only
- **Shipped data:** none. Health Io ships **no** interaction, contraindication or allergy-class data, and invents none. Without a dataset those checks don't run, and the app says so ("No interaction database is loaded yet").
- **Importing:** `uv run python -m scripts.import_drug_reference dataset.json [--dry-run]` (format in `reference.py`).
  - **Allowed sources:** the dataset must come from an allow-listed source (RxNorm, DailyMed, openFDA, CDSCO, NLEM India, DDInter, WHO ATC).
  - **Required metadata:** an https URL on that source's domain, a version, a licence and a named reviewer.
  - **Checked:** severities, ICD-10 prefixes and ingredient pairs are validated.
  - **Versions:** a new version retires the old one.
- **Licences:** check each source's terms before importing. For example, some interaction databases are non-commercial.
- **Traceability:** every warning records its source dataset, version and record reference.

## When checks run
- **Automatically:** a re-check runs, in the same transaction, when:
  - a medicine is recorded, self-reported, confirmed or stopped;
  - a change request is resolved;
  - a prescription is issued or cancelled, or a scan is confirmed;
  - an allergy or condition is recorded or removed.
- **Resolution:** open warnings for the same situation are refreshed rather than duplicated (a stable fingerprint of rule key and records involved). Warnings that no longer apply become `resolved`; they are never deleted.
- **Draft preview:** `POST /prescriptions/{id}/safety-check` checks a draft against current medicines, allergies and conditions without storing anything. The doctor's **Issue** dialog shows the result first ("Issue anyway" when there are findings).
- **On request:** `POST /safety-warnings/recheck`.

## Stored for each warning (`safety_warnings`, migration 0014)
- **What:** kind, severity, the source's own severity, title, and two texts (plain words for patients; the source's wording for clinicians). The texts are encrypted.
- **Source:** source type (reference dataset or consistency rule), name, version and record reference.
- **Scope:** the records involved, and which record sections a viewer needs to see the names.
- **Time:** the trigger, `detected_at`, `last_checked_at`, and status (open or resolved) with `resolved_at`.
- **Review:** `review_status` (unreviewed, acknowledged or reviewed), `reviewed_at`, the reviewer's role, and an encrypted note.
- **History:** review changes are versioned (record_versions), and the table is protected from deletes.

## Review and privacy
- **Doctors** (edit rights, verified) mark warnings reviewed with a required note. **Patients and caregivers** acknowledge them. An acknowledgement can't overwrite a doctor's review. Neither action changes any medicine.
- **Sharing:** warnings are visible with `view_medications`. A viewer without medical-history consent sees that something conflicts, but not the allergy or condition name.
- **Audit:** `safety_warning.list`, `.recheck`, `.review`, `.acknowledge` and `.preview`.

## Web
- **Patient and caregiver:** a "Medication safety" card on the Medicines page, with the headline, the do-not-change reminder, severity, source, "I've seen this", past warnings, and which checks are active.
- **Doctor:** the same card in the chart's Medicines tab, with "Mark reviewed" and a note. The **Issue** step shows the safety check, unknown ingredients and the reference data used.

## Tests
- **Unit, `tests/test_medication_safety.py`:**
  - normalisation and frequency parsing;
  - each rule, with nothing reported without data;
  - severity mapping and dataset products;
  - contraindication prefix matching, and allergy matches (exact and class);
  - each consistency rule, and that consistent lines raise nothing;
  - all warning texts are advisory;
  - stable fingerprints and dataset validation.
- **Integration, `tests/db/test_medication_safety.py`** (synthetic placeholder dataset):
  - nothing is invented without data;
  - an interaction is found and stored with source, severity and timestamps; re-checks don't duplicate it;
  - acknowledge, then review; a review needs a note, and an acknowledgement can't downgrade it; both are versioned;
  - it resolves when a medicine is stopped;
  - duplicates and allergies, with consent-aware wording;
  - the preview stores nothing, then issuing stores the warnings; patients can't use the preview;
  - dataset versions replace each other.
- **Web:** `features/safety/safety.test.tsx`.
- **Totals:** 372 API tests and 77 web tests pass.

## Not yet
- **Dose checks:** maximum daily dose, and age, pregnancy, renal or hepatic cautions. They need dosing reference data in the dataset format.
- **Drug-name matching:** matching free-text drug names to catalogue codes (normalised names only today), and a doctor override flow with a reason for "block" level findings (AI_SAFETY.md §6).
- **Plain-language explanations** of warnings by the assistant (AI_SAFETY.md §6: the model may only rephrase an existing warning, and the result is checked against the original).
