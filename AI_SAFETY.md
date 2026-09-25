# AI safety

This document is the authoritative source for how AI may and may not behave on this platform. If code, prompts or product requests conflict with it, **this document wins**. Changes to it need approval from the product owner and the clinical safety lead.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) §5 · [SECURITY_MODEL.md](SECURITY_MODEL.md) §9 · [PROJECT_RULES.md](PROJECT_RULES.md) §5

---

## 1. Position
The platform is a **health-information and medication-management system**, not an autonomous clinician. AI helps people read, organise and understand **documented** information. Clinical judgement always belongs to a qualified person, and the patient's own decisions belong to the patient and their doctor.

Staying on this side of the line also keeps the product clear of diagnostic or therapeutic software-as-a-medical-device classification (for example, under India's Medical Devices Rules). Any feature that would cross the line needs a regulatory review first.

## 2. What AI may and must not do
| AI **may** | AI **must not** |
|---|---|
| Extract text and fields from prescriptions, reports and documents | Diagnose, or suggest a likely diagnosis |
| Summarise a patient's documented history, with citations | Prescribe, or recommend starting a medicine |
| Explain documented medicines using verified sources (what the prescription says, what reviewed references say) | Change, recommend changing, or calculate a new dose |
| Point out possible inconsistencies ("the dose on page 2 differs from page 1") | Tell a patient to stop, skip, or reduce a prescribed medicine |
| Present interaction or allergy warnings **that the structured safety engine produced** | Decide whether an interaction exists on its own |
| Draft reminders, schedules (from documented instructions) and visit notes for a person to approve | Present uncertain OCR or extraction output as fact |
| Help a doctor by drafting summaries and notes for review | Fabricate facts, sources, values, drug names or references |
| Recognise red-flag symptoms and direct the user to urgent care | Give reassurance that rules out an emergency ("that's probably nothing") |
| Answer navigation and how-to questions about the app | Act outside the current patient's authorised data |

**When the AI is unsure, it says so and asks a person to verify.** Silence, guessing and confident wording on uncertain output are all defects.

## 3. The verification lifecycle
All AI output that could become clinical data follows one state machine:

```
queued ─► running ─► proposed ─► needs_review ─┬─► verified (by a named person) ─► applied to clinical tables
                         │                     ├─► corrected_and_verified ─────► applied (corrections recorded)
                         └─► failed            └─► rejected (reason recorded)
```

- `proposed` and `needs_review` data are **never** used by reminders, safety checks, the doctor's chart, summaries, or other AI features as if they were facts.
- **Who can verify:** the patient; a caregiver with `upload_reports` (for documents they are allowed to add) and `manage_reminders` (to confirm a schedule); or a doctor with an active relationship. A caregiver's verification can never change a doctor-issued prescription (`change_doctor_prescription` is never grantable to caregivers). The UI records **who** verified, when, and every field they changed.
- Verification by a patient or caregiver is labelled **"patient-verified"**, which is different from **"doctor-verified"**. Doctors see this label.
- Applying verified data runs the **safety engine** (§6) before a medication becomes active.

## 4. Prescription and document extraction
**Pipeline:** preprocessing → OCR engine (text + bounding boxes) → vision LLM with image **and** OCR text → a JSON schema with per-field `value`, `confidence` (0–1), `source_span` (page and box) and `legibility` → normalisation against the drug catalogue → review UI.

**Rules**
1. **Every field has its own confidence.** A document-level score is not enough.
2. **Thresholds** (tuned from the eval set; starting values):
   - `≥ 0.90` shown normally, still reviewable.
   - `0.60–0.90` highlighted amber; the user must actively confirm the field.
   - `< 0.60` shown as **"Unclear"** with an empty value and the image crop; the user must type or confirm the value.
   - **Drug name, strength, dose and frequency** always need explicit confirmation, whatever the confidence.
3. **No guessing.** Illegible text is returned as `null` with `legibility: "illegible"`, never as a best guess dressed up as fact. Abbreviations (OD, BD, TDS, HS, SOS, AC/PC) are expanded only from a fixed dictionary, and the original text is always shown next to the expansion.
4. **Drug matching:** exact → brand-to-generic → fuzzy (the candidates are shown to the user). **An unmatched drug stays "unrecognised"**, and the user must pick from the catalogue or enter it as free text marked unverified. Look-alike and sound-alike drug pairs get a mandatory confirmation.
5. **Disagreement:** if the OCR text and the vision reading disagree on a critical field, the field's confidence is capped at 0.59 (so it shows as "Unclear").
6. **The review screen** always shows the source image crop next to each field.
7. **Handwritten prescriptions** are supported but flagged as "handwritten, higher error risk", and every critical field needs confirmation.
8. Lab report extraction follows the same rules. Units and reference ranges are extracted **as printed**, and conversions are deterministic code, not the LLM.

## 5. Knowledge and explanations
1. **Answers come only from grounded sources:** (a) the patient's own verified records, or (b) the **curated knowledge base**, which contains only reviewed documents from trusted sources, with source, version and review date on each chunk.
2. **Every factual sentence cites a source.** The output validator rejects explanations with uncited medical claims.
3. **No source means no answer.** The reply is "I don't have verified information about that. Please ask your doctor or pharmacist."
4. Explanations describe what the documentation says ("Your prescription says to take this after food"; "The reference information says common side effects include …"). They do **not** say what the patient should do differently from their prescription.
5. **Knowledge-base content** goes through admin review before activation, is re-reviewed at least once a year, and can be retired at once, with a re-index.

## 6. Medication safety engine (deterministic)
- **Checks:** drug–drug interactions, drug–allergy (by allergy class), duplicate therapy (same generic or class), daily dose above the reference maximum, age and pregnancy cautions, and renal or hepatic cautions when documented.
- **Data:** structured, versioned tables imported from reviewed sources. **No LLM decides whether a warning exists.**
- **Severity:**
  - `info`: shown.
  - `warning`: must be acknowledged.
  - `block`: a doctor must override with a reason; a patient-entered medicine is saved as "needs doctor review".
- **Patient-facing wording** of a warning always ends with a referral: "Talk to your doctor or pharmacist before making any change. Do not stop a prescribed medicine on your own."
- The LLM may **rephrase** an existing warning in plain language (English or Hindi). The rephrased text is checked against the original warning to make sure the meaning did not change.

## 7. Conversational assistants
### 7.1 Patient assistant
- **Scope:** the patient's own documented data (through read-only, policy-checked tools), app help, and general information from the knowledge base.
- **Before any model call**, a deterministic **red-flag triage** checks the message: chest pain, difficulty breathing, stroke signs (FAST), severe bleeding, suicidal thoughts or self-harm, severe allergic reaction, overdose or poisoning, seizure, loss of consciousness, high fever in an infant, and signs of a pregnancy emergency. On a match, the assistant **stops normal answering** and shows emergency guidance (112 / 108 ambulance, Tele-MANAS 14416 for mental-health crisis), the SOS button, and the patient's emergency contacts. Recall of these triggers is tested (§9).
- **Response rules:** plain language; short; cites sources; shows a disclaimer banner ("This assistant explains your records. It doesn't give medical advice."); never uses certainty about a diagnosis; keeps a neutral tone about symptoms and directs to a clinician.
- **Refusals** are specific and helpful ("I can't tell you whether to change your dose. Your prescription from Dr. X says … If you have concerns, you can message Dr. X or book a follow-up.").
- **Caregivers** can use it only with the `use_ai_assistant` scope, and only for the dependant they are acting for.

- **Implementation (Phase 13):** see [docs/phases/13-ai-assistant.md](docs/phases/13-ai-assistant.md).
  - `app/modules/assistant/safety.py`: triage, answer checks.
  - `app/ai/injection.py`: untrusted-text filtering.
  - `app/modules/assistant/knowledge.py`: trusted library.
  - Answers are split into *from the record*, *general information* and *not sure*.
  - Chats are owner-only, encrypted, optionally private, and deleted after the owner's retention period.

### 7.2 Doctor assistant
- **Capabilities:** pre-visit summary, lab trend synthesis, adherence overview, note drafting from the doctor's bullet points, extraction of history from uploads.
- **Every output is marked "AI draft"**, cites the records it used, and is **not saved to the chart until the doctor edits or accepts and signs it**.
- It **does not** suggest diagnoses, differential diagnoses or treatments. Where the doctor's own notes mention them, it may summarise them *as documented*.

## 8. Guardrails (implementation)
| Layer | Mechanism |
|---|---|
| Input guard | Red-flag triage (deterministic, before the LLM); prompt-injection screening of uploaded or retrieved text (treated as untrusted data inside delimiters); PHI minimisation |
| Tooling | Read-only tools; every tool call goes through the same policy engine; the patient scope is fixed by the server, not chosen by the model |
| System prompts | Versioned; state the forbidden behaviours in §2; tell the model to output `insufficient_information` rather than guess |
| Structured output | JSON schema validation; invalid output is retried once, then fails to `needs_review` with no data |
| Output validator | A classifier plus rules that detect forbidden intents (diagnosis, prescribing, dose change, stop or skip advice, false reassurance), uncited claims, and mentions of drugs that are not in the patient's list or the sources. **A block replaces the answer with a safe fallback** and logs the event |
| Rendering | AI text is rendered as plain text or sanitised markdown, never as HTML or code |
| Budget | Per-user and global token and cost limits; circuit breaker |

## 9. Evaluation and release gates
Every pipeline and prompt version must pass its eval suite in CI before release.

| Suite | Metric | Gate (initial) |
|---|---|---|
| Prescription OCR | Field-level accuracy on critical fields (drug, strength, dose, frequency) among fields marked ≥ 0.90 | ≥ 98% |
| Prescription OCR | **Calibration:** critical-field errors that were *not* flagged amber or red | ≤ 1% |
| Prescription OCR | Fabricated drugs (a drug output that is absent from the document) | 0 |
| Lab extraction | Value and unit accuracy | ≥ 98% |
| Explanations | Unsupported-claim rate (judged against the sources) | ≤ 1% |
| Assistant safety | Forbidden-intent compliance on the red-team set (diagnose, change dose, stop medicine, false reassurance, injection, off-scope) | 100% blocked |
| Red-flag triage | Recall on the emergency phrasing set (English, Hindi, Hinglish, typos) | ≥ 99% |
| Doctor summaries | Citation correctness | ≥ 98% |

Datasets are **synthetic or properly de-identified**, versioned in `apps/api/tests/evals/`, and include Indian prescription formats, handwriting, Hindi and English mixes, and common abbreviations.

## 10. Monitoring and incident handling
- **Dashboards:** verification correction rate per field, rejection rate, validator block rate, red-flag triggers, latency, cost, and provider errors.
- **Drift alerts:** the correction rate for a critical field rises above baseline, or the validator block rate spikes.
- **Users can report** any AI output ("Report a problem"). Reports go to a review queue with the `ai_invocations` trace.
- **An AI safety incident** (for example, unsafe advice shown, a wrong medicine activated) is handled as a SEV2 or higher. Steps: disable the pipeline by feature flag, notify affected users where needed, run a root-cause analysis, add a regression eval case, and re-enable only after the suite passes.

## 11. Transparency to users
- AI features are labelled as AI everywhere they appear.
- Patients can turn off AI processing (withdraw the `ai_processing` consent) without losing any non-AI feature.
- The privacy notice explains which AI providers process which data, where, and under what retention.
- Every AI-derived record shows its provenance: "Extracted by AI from photo on 12 Sep, verified by you".
