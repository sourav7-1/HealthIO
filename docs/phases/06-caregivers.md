# Phase 6: Caregivers and dependants

## Use cases
| Situation | How it works |
|---|---|
| **Parent managing a child** | The parent adds the child from `/care` → *Add someone you look after*. This creates a **dependant profile** with no login of its own. The parent becomes the child's **guardian**. |
| **Adult child helping an elderly parent** | If the parent uses Health Io, the parent **invites** the child from *Caregivers* and ticks exactly what they may see and do. If the parent cannot use an account, the child adds them as a dependant with a stated basis (see below). |
| **Authorised carer for a dependent adult** | Added as a dependant with a basis of power of attorney, court-appointed guardian, or the person's own agreement. |

**Declared basis.** It must match the person's age. Minors take `parent_of_minor` or `legal_guardian_of_minor`. Adults take one of:
- `power_of_attorney`
- `court_appointed_guardian`
- `adult_consented`

The declaration must be accepted. It is audited with its version (`2026-09-dependant-en`). A caller can manage at most 20 dependants.

## Permissions
The permissions are granular, one row per scope. Revoked rows are kept as history.
- **See:**
  - `view_profile`, `view_medications`, `view_prescriptions`, `view_adherence`
  - `view_medical_history`, `view_visits`, `view_appointments`, `view_reports`
  - `receive_alerts`
- **Do:** `log_doses`, `manage_reminders`, `report_health_info`, `upload_reports`, `manage_appointments`, `manage_emergency_info`, `use_ai_assistant`.
- **Guardians only:** `manage_caregivers`.
- **Dependant guardians** also get `edit_profile`, but only for the dependant's demographics.

**Caregivers can never modify doctor-created clinical records.** This is enforced in three places:
1. `edit_clinical_records`, `change_doctor_prescription` and `delete_medical_records` are not scopes. They are removed from every caregiver decision (`NEVER_FOR_CAREGIVERS`), and a DB CHECK constraint rejects them too.
2. Everything a caregiver writes is labelled with its source:
   - allergies and conditions `source = caregiver`;
   - documents `source = caregiver`;
   - stopping a self-reported medicine `stop_source = caregiver`.
3. Removing an entry works only for patient- or caregiver-reported entries. A doctor's entry returns 403.

**Patient-controlled sharing:**
- Only the patient, or a guardian holding `manage_caregivers`, can invite, change scopes or revoke.
- An invitation grants nothing until accepted.
- Revocation takes effect on the next request.
- A caregiver cannot change their own scopes, and cannot revoke themselves through the patient's list; they use *leave*.
- The last guardian of a dependant cannot leave (409), so the record is never orphaned.

## API
| Endpoint | Policy | Purpose |
|---|---|---|
| `POST /me/dependants` | authenticated | Create a dependant; the caller becomes an active guardian with all grantable scopes |
| `GET /me/caregiving` | authenticated | Links and open invitations, with the patient's name and dependant flag |
| `GET /me/caregiving/dashboard` | authenticated | Per person: today's doses, missed doses (7 days), upcoming appointments, open follow-ups, recent reports, and the effective permissions. A section without the scope is `null` ("not shared"), never `[]` |
| `POST /caregiver-invitations/{id}/accept` · `/decline` | the invited user | Accept or decline |
| `POST /me/caregiving/{id}/leave` | the caregiver | Stop caring |
| `GET/POST /patients/{id}/caregivers`, `PUT …/{rel}/scopes`, `DELETE …/{rel}` | `manage_caregivers` | Manage caregivers (existing) |
| `GET /patients/{id}/caregivers/activity` | `manage_caregivers` | Last 100 audit events by this patient's caregivers |

Migration `0006` widens the scope CHECK constraint.

## Audit
- **Lifecycle:** `caregiver.invited`, `.accepted`, `.declined`, `.scopes_changed`, `.revoked`, `.left`, `.dependant_created`.
- **Record access:** every patient-scoped read or write already writes an event with `via=caregiver`. The dashboard writes `caregiver.dashboard_view`, listing the sections read for each person.
- **Denials:** `access.denied`.

The patient (or guardian) sees all of this under *Caregivers → What caregivers did recently*.

## Web
- **`/care` caregiver portal:**
  - Invitations to accept or decline.
  - A card per person, with dose actions only when `log_doses` is granted.
  - Adding a dependant, with an age-aware basis choice and a declaration.
- **Person switcher:** in the sidebar.
- **Record view:** each person's record is at `/care/:id/...`, reusing the patient pages through `ActivePatientProvider`:
  - The navigation shows only pages the caregiver has permission for.
  - Actions inside pages are hidden without the permission.
  - A banner says whose record is open.
- **Labels switch voice:** "Prescribed by your doctor" becomes "Prescribed by a doctor", and entries show "Added by a caregiver".
- **Patient portal:** new scopes, an option to invite another guardian (when managing a dependant), the activity log, and a "People I care for" link.
- **Sign-in:** users who are only caregivers land on `/care`.

## Tests
- API `tests/db/test_caregivers.py` (16 tests):
  - guardian powers and refusal of clinical writes;
  - caregiver-labelled entries;
  - basis and age rules;
  - the declaration;
  - invited scopes exactly as granted, and an adult's profile not editable by a caregiver;
  - `via=caregiver` access audit;
  - isolation between patients;
  - decline, last-guardian and leave rules, and immediate revocation;
  - dashboard `null` sections and audit;
  - the activity log visible to the patient but not the caregiver.
- Web: `care.test.tsx` covers "not shared" vs empty sections, and actions gated by `log_doses` in the caregiver voice. `patient.test.tsx` covers view-only doses.

## Not yet
- Handover when a minor turns 18: the account claim flow.
- Missed-dose alerts to caregivers with `receive_alerts` (Phase 10 reminder engine).
- Doctor linking for dependants, which today works only through a doctor adding the patient in person.
- Emailed invitations for people without an account.
- Playwright E2E tests.
