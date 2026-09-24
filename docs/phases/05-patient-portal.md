# Phase 5: Patient portal

## What was built

**Web portal at `/patient`.** All pages are lazy-loaded. Patients land here after sign-in: `homeFor` sends doctors to `/doctor` first.

| Section | What the patient can do |
|---|---|
| Today (dashboard) | See today's doses and upcoming ones, marking each Taken, Skip (with an optional reason) or Snooze. Medicines waiting for confirmation. Doctor connection requests: accept with a choice of categories, or decline. Upcoming appointments and follow-ups, recent prescriptions and reports, and the 30-day adherence record. |
| My health | Profile summary; edit own demographics and blood group. |
| Medicines | Confirm a prescribed medicine and choose reminder times. Change reminder times. Add a self-reported medicine. Stop a self-reported medicine. Log an as-needed dose. See past medicines. |
| Prescriptions | Read-only prescriptions with items, directions, advice and cancellations. |
| Medical history | Allergies, conditions and past history. Add own allergies and conditions. Remove only own entries; doctor entries show "Read only". |
| Doctor visits | Visits, and a visit page with signed notes (superseded notes are labelled). |
| Tests & reports | Tests ordered, reports with values as printed, documents. Upload a document (PDF or photo, up to 15 MB, presigned). |
| Appointments | Appointments and follow-ups. |
| Emergency profile | Choose what is shown (blood group, allergies, conditions, medicines). Critical information, organ donor status, advance directive. Up to 10 emergency contacts. |
| Caregivers | Invite by email with scopes and an optional end date. Change scopes. Remove access. The list shows caregiver names. |
| Settings | Account name, time zone and language. Reminder preferences: channels, medicine names hidden by default, quiet hours, snooze length, missed-dose threshold, caregiver alerts. Larger text (per device). Change password. Signed-in devices. |

**Doctor-prescribed vs self-reported.** Every medicine, dose, allergy, condition, report and document carries a `SourceBadge` with an icon and words, never colour alone:
- "Prescribed by your doctor"
- "Recorded by your doctor"
- "From your doctor"
- "Added by you"

**Doctor records stay unchanged.** The API rejects any patient attempt to change them (403), and the UI never offers it:
- Stopping or editing a prescribed medicine.
- Deleting a doctor's allergy or condition.
- Editing notes, prescriptions or reports.

For a prescribed medicine, a patient can change only their own *reminder times*. The prescribed directions stay as written. A safety note says to talk to the doctor before stopping or changing a prescribed medicine.

**Backend added in this phase:**
- Reminder preferences (migration `0005`).
- Schedule expansion that handles time zones and DST.
- Dose materialisation, with a 48 h horizon and idempotent inserts.
- Take, skip and snooze (at most 3 snoozes), plus as-needed logging and missed-dose marking.
- Self-reported medicines, allergies and conditions, under the `REPORT_HEALTH_INFO` permission, which is never grantable to caregivers.
- Emergency profile and contacts.
- Profile editing, limited to patient-editable fields.
- Account update, and password change that signs out other sessions.
- Caregiver names in the caregiver list.

## Accessibility
- Touch targets are at least 44 px.
- Visible labels on every field.
- Status is shown as text and an icon, not colour alone.
- Confirmation dialogs for skipping doses and removing caregivers.
- `tel:` links for emergency contacts.
- A "Larger text" setting scales the whole UI (rem-based) and is stored per device, with a safe fallback when storage is unavailable.
- Plain-language labels throughout ("Snooze", "I took a dose now", "Added by you").

## Tests
- API: `tests/db/test_patient_portal.py` (8 tests), `tests/test_schedules.py` (5 tests). **115 API tests pass**, including integration.
- Web: `features/patient/patient.test.tsx` covers:
  - the source labels;
  - Taken;
  - Skip requires a confirmation and sends the reason;
  - the snooze limit;
  - no actions on a dose already taken.

  **14 web tests pass.** `pnpm turbo run lint typecheck test build` is green.

## Run it
The steps are the same as in [04-doctor-portal.md](04-doctor-portal.md), plus `uv run alembic upgrade head` for migration `0005`. Register with role `patient`, confirm the email in Mailpit, sign in, and you land on `/patient`.

## Not yet
- Reminder **delivery** (push, SMS, email). Preferences are stored, but the escalation ladder is Phase 10.
- The consent centre, a "who accessed my data" view and data-export requests.
- Onboarding flow, PWA and Web Push subscription.
- Hindi UI strings (the language preference is stored).
- Playwright E2E tests.
- The Expo mobile app.
- The main JS chunk is about 505 kB (a Vite warning), and should be split further.
