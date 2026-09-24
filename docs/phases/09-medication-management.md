# Phase 9: Medication management

## Where a medicine comes from
Every medicine has an `origin`, shown as its label everywhere (patient, caregiver and doctor views):

| Origin | Label | Created by |
|---|---|---|
| `doctor_prescription` | Prescribed by your doctor | Issuing an e-prescription (Phase 7) |
| `uploaded_prescription_ai` | Paper prescription · read by AI, checked | Confirming an AI-read scan (Phase 8) |
| `uploaded_prescription_typed` | Paper prescription · typed in | Confirming a manually typed scan |
| `self_reported` | Added by you | The patient or a caregiver with `report_health_info` |
| `clinician_recorded` | Recorded by your doctor | A doctor recording a medicine the patient already takes |

Migration `0009` backfills existing rows. Check constraints tie `origin` to `source`.

## States
- **Waiting to be set up** (`pending_confirmation`): from a prescription, until reminder times are chosen.
- **In use** (`active`).
- **Paused**: records who paused it, the reason and an optional restart date. There are no reminders while paused.
- **Course completed**: set automatically once the end date has passed. This is the only thing a scheduled job does.
- **Discontinued** (`stopped`): records who, why, and on whose advice.
- **Self-reported** is an origin, not a state. Past medicines keep their full history.

## Scheduling engine
`app/modules/medications/schedules.py` is pure code, and is fully unit-tested.
- **Fixed times of day** in the patient's time zone, DST-safe.
- **Every N hours**, counted in elapsed time: an 8-hourly dose stays 8 hours apart across clock changes.
- **As needed**: no reminders; doses are logged when taken.
- **Day patterns:** every day, every N days (counted from the start date), or chosen weekdays. Stored as a restricted RRULE subset; anything else is refused, never guessed.
- **Per schedule:** dose (amount and unit), food relation, start and end dates, time zone.
- **Versioning:** a schedule is never edited in place. A change ends the old version (its future unanswered doses are cancelled) and starts a new one. Resuming after a pause starts a new version.
- **Dose from the prescription:** confirming a prescribed medicine takes the dose and food relation from the prescription line.

## Changing a prescribed medicine
`change_policy.py` decides **who must confirm** a change. It never decides what is medically right.

| Change | For a prescribed medicine | For your own medicine |
|---|---|---|
| Move reminder times (same number a day) | Allowed | Allowed |
| Doses under 4 h apart, a night-time dose, times outside a written pattern such as `1-0-1` | Warning; must be acknowledged | Warning; must be acknowledged |
| Different number of doses a day, dose, food relation, days, as-needed ↔ scheduled, or course length | **Needs a clinician** | Allowed |
| Pause or discontinue | **Warning**, then a choice (below) | Allowed |

"Needs a clinician" gives three paths:
1. **Ask the doctor in the app.** This creates a change request, and nothing changes until a linked doctor approves (`change_doctor_prescription`). Approving applies exactly the proposal; declining leaves everything as it was. One pending request per medicine.
2. **"A doctor or pharmacist told me to."** The advisor's role and name are required. The change is applied and recorded as reported advice. This covers pharmacists and doctors who don't use the platform.
3. **For pause/stop only: "my own decision".** It is recorded as such after the warning. Patients may stop taking a medicine; the app records it honestly and never advises it.

The API returns `409` with the findings until the right confirmation is provided. `POST …/schedule/check` previews what a change needs.

**The prescription itself is never changed.** Only the regimen changes. A doctor who wants to change the prescription corrects it as a new version (Phase 7).

**No AI or automated process can change a regimen, enforced in the database.**
- `medication_events` CHECK constraints: a regimen event needs a person (`actor_user_id`).
- The `system` actor may only record `completed` or `duplicate_noted`.
- AI prescription reading only ever creates new medicines that wait for confirmation.

## History
- `medication_events` is append-only (the `hio_append_only` trigger).
- **Events recorded:** created, confirmed, schedule_changed (before/after, findings, how it was confirmed), paused, resumed, stopped, completed, change_requested/approved/declined/withdrawn, duplicate_noted.
- Each event has the actor, their role, and the reason. Where relevant it also has the advisor and the change request.
- Reasons and details are encrypted.
- Shown on each medicine's page and to the doctor.

## Duplicate detection (data level)
- **Generated keys:** PostgreSQL computes `name_key` (lower-case, form prefix such as Tab./Cap./Syp. and punctuation removed), `generic_key` and `strength_key` as generated columns, so every writer gets them.
- **Blocked:** a partial unique index refuses the same self-reported medicine (same name key and strength) twice while it is in use. The API returns 409 "already on the list".
- **Flagged:**
  - The view `medication_possible_duplicates` pairs medicines in use with the same name, or the same generic (including one medicine's generic matching another's name).
  - Each medicine lists its possible duplicates, and a history note is added when one is detected.
  - Patient, caregiver and doctor views all show the warning ("Taking both could mean taking too much").
- **Not yet:** therapeutic-class duplicates need the drug catalogue (Phase 14).

## API
**Medicine endpoints** (`/patients/{id}/medications/…`):

| Endpoint | Permission | Purpose |
|---|---|---|
| `GET {mid}` · `GET {mid}/history` | `view_medications` | One medicine with origin, prescribed line, schedule, duplicates and pending request · its history |
| `POST {mid}/schedule/check` · `PUT {mid}/schedule` | `manage_reminders` | Preview · apply a schedule change |
| `POST {mid}/pause` · `POST {mid}/resume` | `manage_reminders` | Pause · restart |
| `POST {mid}/stop` | `report_health_info` | Discontinue, with acknowledgement plus advisor or own decision for prescribed medicines |
| `POST {mid}/change-requests` | `manage_reminders` | Ask the doctor |

**Change-request endpoints** (`/patients/{id}/medication-change-requests/…`):

| Endpoint | Permission | Purpose |
|---|---|---|
| `GET` (the list) | `view_medications` | All requests |
| `POST {rid}/approve` · `POST {rid}/decline` | doctor with `change_doctor_prescription` | Answer a request |
| `POST {rid}/withdraw` | `manage_reminders` | Withdraw a request |

`PUT …/reminder-times` remains for times-only changes and goes through the same rules.

## Web
- **Medicines page:** grouped as *waiting to be set up*, *in use*, *paused* and *past*. Each card shows the origin label, schedule, course dates, duplicate warning and pending request.
- **Medicine page:**
  - What the prescription says (as written, with times a day, dose, food and course).
  - The current schedule, and the actions allowed by the viewer's permissions.
  - The history timeline.
- **Schedule dialog:** times, every N hours, days, dose, food, dates. *Review change* shows what the change needs, then offers *ask my doctor* or *a doctor or pharmacist told me to*.
- **Pause and discontinue dialogs:** a warning for prescribed medicines, then a choice of ask the doctor, a named doctor or pharmacist, or own decision.
- **Doctor's chart:** origin labels, the patient's actual schedule, duplicate flags, and *Changes the patient asked you to confirm* with Approve and Decline.

## Tests
- **`tests/test_medication_engine.py` (23 tests):**
  - repeat rules round-trip, and unsupported rules are refused;
  - every-other-day counting from the start date; weekdays;
  - 8-hourly doses across the US DST change stay exactly 8 h apart; the course dates are respected;
  - the policy: moving times is free; warnings need acknowledgement; each clinically relevant change needs a clinician; a dose the prescription doesn't state needs a clinician; own medicines get warnings only.
- **`tests/db/test_medications.py` (7 tests):**
  - origins, and the dose and food relation copied from the prescription;
  - timing change, acknowledgement, 409 for frequency and dose changes, applying with a named pharmacist, and that the prescription is unchanged;
  - the doctor approval flow, including that a patient cannot approve;
  - pause (reminders cancelled), resume and stop, with the history sequence;
  - course completion by the system actor;
  - duplicates: the 409, same strength vs different strength, flagging against the prescribed medicine, generic matching, and the unique index refusing a raw insert;
  - interval doses 8 h apart;
  - the database refuses a system regimen change; history is append-only.
- **Scan tests:** now assert `uploaded_prescription_ai` and `uploaded_prescription_typed`.
- **Web, `features/meds/meds.test.tsx`:**
  - origin labels;
  - the schedule dialog: no save without a clinician; naming a pharmacist plus acknowledgement enables save; ask-the-doctor path; timing acknowledgement;
  - the stop dialog: defaults to asking the doctor; own decision needs acknowledgement.
- **Totals:** 208 API tests and 32 web tests pass.

## Not yet
- Notifying the doctor about a new change request (Phase 19). Doctors see requests on the chart.
- Tapering and variable doses, and multiple schedules per medicine.
- Inventory and refill reminders (Phase 10).
- Therapeutic duplicates and interaction checks (Phase 14).
- Reminder delivery (Phase 10).
