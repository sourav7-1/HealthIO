# Phase 13: AI health assistant

## What it answers
- **Record questions** about the person on screen: documented medicines and their schedules, prescription instructions as written, upcoming appointments and follow-ups, and summaries of what is recorded (conditions, allergies, symptoms, test reports with values as printed).
- **General health and medicine information,** only from the **trusted library**.

## What it never does
It never diagnoses, prescribes, changes a dose, tells anyone to stop or skip a medicine, calls results good or bad, or invents history. The prompt asks for this, and the checks below enforce it independently of the model.

## Flow (`app/modules/assistant/service.py`)
1. **Emergency triage first** (`safety.triage`, no model call).
   - Red flags in English and common Hindi phrasings: chest pain, breathing difficulty, stroke signs, unconsciousness or seizure, severe bleeding, severe allergic reaction, overdose or poisoning, worst-ever headache, a fever in an infant, and pregnancy emergencies.
   - A match returns fixed guidance immediately: call 112, ambulance 108, go to the emergency department.
   - Thoughts of self-harm also get Tele MANAS 14416. The web app shows tap-to-call buttons.
2. **Record context** (`context.py`): only sections the caller may see (the same permissions and consent as the rest of the app).
   - No identifiers are sent (no name, date of birth, phone, ABHA or address).
   - Each item has an id (`med:…`, `rxi:…`, `appt:…`, `report:…`) that answers must cite.
   - The person can switch record access off entirely.
3. **Trusted library** (`knowledge.py`):
   - Passages come from reviewed texts by allow-listed publishers: WHO, MoHFW, ICMR, CDSCO, MedlinePlus, DailyMed, NHS and CDC.
   - Retrieval uses PostgreSQL full-text search, with texts about the person's own medicines ranked first.
   - Nothing is fetched from the internet at question time.
4. **Model** (`app/ai/assistant_model.py`):
   - **Call:** one Claude request (`HIO_AI_ASSISTANT_MODEL`, default `claude-sonnet-5` per the approved plan) with a cached system prompt (`app/ai/prompts/health_assistant_v1.md`).
   - **Output:** a structured JSON answer (`output_config.format`): segments of kind `record`, `general` or `uncertain`, each with sources, plus `urgent`, `declined` and `questions_for_doctor`.
   - **Failures:** an invalid answer is retried once; a refusal or provider error is shown as "unavailable", never as an answer.
   - **Offline mode:** without an API key, the assistant only quotes matching record items and library passages, and says that AI answers are off.
5. **Checks before anyone sees the answer** (`safety.check_answer`):
   - `record` must cite items that were provided, and every number in it must appear in those items. `general` must cite a library passage. Otherwise the segment becomes `uncertain`, shown as "Not sure".
   - Diagnosis, prescribing, dose-change and stop/skip wording replaces the **whole** answer with a fixed decline that points to a doctor or pharmacist. Wording that tells people *not* to change things alone is allowed, and so is a record segment repeating its prescription's own words ("stop after 5 days").
   - Links are removed unless they belong to a cited library source.

## Three kinds of information, shown differently
- **From the record:** the record items it cites.
- **General information:** publisher, title, review date and a link.
- **Not sure:** says what is unknown and who can answer it.

Every answer carries a disclaimer. Emergency guidance always comes first.

## Prompt injection from uploaded documents (`app/ai/injection.py`)
- **What counts as untrusted:** text people typed or that came from uploads or OCR (instructions, advice, report conclusions and notes, symptom text, reasons), and the question itself.
- **Detection:** text written to steer an AI is **withheld**. The model only sees that the item exists and was withheld, and `assistant.untrusted_text_withheld` is audited. Detected:
  - override phrases and new-role instructions;
  - fake system tags and role markers;
  - prompt-leak requests and tool syntax;
  - exfiltration links;
  - "tell the patient to stop…" style commands.
- **Neutralising:** Unicode look-alikes are folded first. Clean text has angle brackets replaced, and is length-capped and wrapped in `<untrusted_document>`. The system prompt says such content is data, never instructions.
- **Library imports:** the same scan runs on imported library texts.
- **Last line of defence:** output checks mean a missed injection still cannot make the assistant give forbidden advice.

## Conversation history and privacy
- **Ownership:** conversations belong to the person who had them.
  - A patient's and a caregiver's chats about the same person are invisible to each other; the other person gets 404.
  - Doctors have no access.
- **Storage:** question and answer text is encrypted (`EncryptedString`). Audit events hold only metadata: mode, sections used, checks triggered, model, withheld count.
- **Private chat** (`save: false`): nothing is stored. The browser keeps earlier turns and sends at most 12.
- **Preferences** (`/me/assistant/preferences`):
  - keep saved chats for 1, 7, 30 or 90 days (shortening also applies to existing chats);
  - save new chats by default;
  - let the assistant read the record.
- **Deletion:** delete one chat or all your chats (hard delete; chats are not clinical records). The `assistant.purge_expired` job runs hourly.
- **Limits:** 60 questions per person per day, counted from the audit log.
- **Access:** needs `use_ai_assistant`. Patients have it; caregivers only if granted. With it, a caregiver's assistant sees only the sections their other scopes allow.

## Adding library content
`uv run python -m scripts.import_knowledge <dir|file.md> [--dry-run]`

- **Required front matter:** publisher key, title, the publisher's https URL, category, optional medicine names, a named reviewer, review date and review due date (see the script header).
- **Rejected:** untrusted publishers, off-domain URLs, expired reviews and injected instructions.
- **Content:** none is shipped. Text must be copied from the publisher (respecting its licence) and clinically reviewed before import. Until then, general questions are answered as "Not sure" rather than from the model's memory.

## Web
- **Where:** `/patient/assistant`, and "Health assistant" for each person a caregiver may use it for.
- **Chat:**
  - starter questions;
  - answers with coloured "From the record", "General information" and "Not sure" parts and their sources;
  - questions to ask the doctor;
  - an emergency panel with call buttons.
- **Saved chats:** list and delete.
- **Private chats:** a "Save this chat" toggle; switching it starts a new chat.
- **Privacy dialog:** retention, save by default, record access, and delete all.

## Tests
- **Unit, `tests/test_assistant_safety.py`:**
  - red-flag and crisis triage, including Hindi phrasing, infants and pregnancy; ordinary questions are not flagged;
  - injection cases (including full-width letters and exfiltration links) are withheld; ordinary text is wrapped and neutralised;
  - unsupported or invented record claims become "Not sure";
  - each forbidden category replaces the answer, while safe wording and quoted prescription instructions are allowed;
  - links are filtered, and the schema matches the answer model;
  - library validation rejects bad publisher, domain, scheme, expiry, reviewer, category and injection;
  - the Claude adapter uses the cached prompt and structured output, retries invalid output once, and handles refusals.
- **Integration, `tests/db/test_assistant.py`** (scripted model):
  - grounded record and library answers, with no identifiers sent and encrypted storage;
  - follow-up history;
  - emergencies never call the model;
  - forbidden advice is replaced and invented history relabelled;
  - injected record text is withheld and audited;
  - private mode stores nothing;
  - preferences (records off, retention applied to existing chats, purge);
  - caregiver scopes and chat privacy, and no doctor access;
  - offline mode and the daily limit.
- **Web:** `features/assistant/assistant.test.tsx` covers labelled parts and sources, emergency call links, saved vs private chats (history sent only when private) and errors.
- **Totals:** 327 API tests and 72 web tests pass.

## Not yet
- Hindi answers (the prompt and checks are English-first; triage already recognises some Hindi phrases).
- Streaming responses.
- An eval set of real-world phrasings for triage recall and forbidden-advice detection, run against the live model in CI (AI_SAFETY.md §9). The current tests cover the rules, not model quality.
- SOS button and emergency contacts inside the emergency panel (Phase 17).
- Explaining medication-safety warnings (Phase 14).
