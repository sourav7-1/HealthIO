# Phase 4: Doctor portal (plus the Phase 1b frontend foundation)

## What was built

**Frontend foundation (Phase 1b, first part).** `apps/web` is now a React 19 + Vite + TypeScript + Tailwind 4 SPA, replacing the Next.js placeholder:
- React Router, with the doctor portal pages lazy-loaded.
- TanStack Query, React Hook Form + Zod, and the generated typed API client.
- The access token is kept in memory, with a silent refresh from the httpOnly cookie.
- Reusable UI in `src/components/ui`: button, field, input, select, checkbox, card, stat, badge, skeleton, empty/error states, alert, dialog, tabs, toast.
- A responsive portal shell: sidebar on desktop, drawer on phones.
- Still to do from Phase 1b: the compose split, the web Dockerfile, the PWA, and i18n.

**Doctor portal.**
| Area | Screens and actions |
|---|---|
| Dashboard | Total patients, today's appointments, follow-ups due in 14 days (overdue flagged), active treatments (medicines from prescriptions the doctor issued), recently seen patients, verification banner |
| Patients | Name search (only among the doctor's own patients who share their name), add a patient in person (with consent recorded by category), connect an existing patient (they must accept and choose what to share) |
| Patient chart | Overview (at a glance, problem list, allergies, recent activity), timeline with filters, visits, medications with adherence, prescriptions, tests, reports and documents, appointments, follow-ups |
| Actions | Record visit; write, sign and amend clinical notes; record an assessment or diagnosis as documented; order tests; upload a report (file plus values as printed); write, edit, issue and cancel prescriptions; record a medicine the patient already takes; set, complete and cancel follow-ups; book appointments |

**Safety and privacy rules applied**
- **Only related patients.** Every chart endpoint uses `patient_request(Permission.X)`: no link means 404, and a link without consent for that data means 403. The UI hides tabs and actions the doctor's consent does not cover.
- **Consent narrows everything:**
  - The dashboard and patient list show names only when the patient shares demographics.
  - Each dashboard item is limited to the categories that patient consented to share.
- **Drafts are private.** Draft notes and draft prescriptions are visible only to their author.
- **Signed and issued records are final.** Signed notes and issued prescriptions cannot be edited (409). Corrections are amendments or cancellations with a reason, and the history is kept.
- **Issuing starts nothing by itself.** An issued prescription creates medicines as *awaiting patient confirmation*.
- **No generated clinical content.** Adherence is shown only from recorded dose events; with none, it says so. Diagnoses and report values are recorded as the doctor documents them, and result flags only as printed.
- **Every clinical read and write is audited**, with the patient, resource and route.
- **Uploads** use presigned POST straight to storage. The API then checks size, magic bytes and SHA-256; content that doesn't match its declared type is quarantined. Downloads are 60-second links.

**Schema:** migration `0004`:
- In-person consent recorded by the clinician (`clinician_recorded`).
- Clinician-recorded medicines.
- Tests ordered by name, since no reviewed test catalogue exists yet and none was invented.

## Tests
- API: `tests/db/test_doctor_portal.py` (10 tests). It covers the full workflow, private drafts, uploads and quarantine, double booking, consent narrowing, patients reading their own chart, and an **unrelated doctor getting 404 on every read and write endpoint**.
- Web: Vitest tests for formatting, the query states (the 403 "not shared" state), the name fallback, and the add-patient consent rule.

## Run it
```bash
docker compose -f docker-compose.dev.yml up -d postgres redis minio minio-init mailpit
cd apps/api && uv run alembic upgrade head && uv run uvicorn app.main:app --reload   # :8000
pnpm --filter @health-io/web dev                                                    # :5173
```
Create an admin with `uv run python -m scripts.create_admin`. Register a doctor at `POST /api/v1/auth/register` (role `doctor`, with registration details), confirm the email from Mailpit (http://localhost:8025), then verify the doctor with `POST /api/v1/admin/doctors/{id}/verify`.

## Not yet
- Screen-level E2E tests (Playwright).
- A dedicated screen for the patient's side of connection requests (the API exists).
- Step-up MFA before issuing a prescription.
- ClamAV scanning. Set `HIO_UPLOAD_VIRUS_SCAN_REQUIRED=true` once the scanner is available.
