# Development roadmap

Phases run in order. **A phase is done only when its exit criteria pass in CI** and a demo script exists in `docs/phases/NN.md`. Each phase keeps to [PROJECT_RULES.md](PROJECT_RULES.md), including the definition of done.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY_MODEL.md](SECURITY_MODEL.md) · [AI_SAFETY.md](AI_SAFETY.md)

## Status
| Phase | Name | Status |
|---|---|---|
| 0 | Project architecture and rules | ✅ Done (architecture docs v1.0 added 2026-09-24) |
| 1 | Backend foundation | ✅ Done (see [docs/phases/00-01.md](docs/phases/00-01.md)) |
| 1b | Frontend foundation and compose split | 🟡 Vite SPA, UI primitives and portal shell done ([docs/phases/04-doctor-portal.md](docs/phases/04-doctor-portal.md)); compose split, web Dockerfile, PWA and i18n remain |
| 2 | Database and medical data model | ✅ Done: schema, migrations, triggers, encryption, audit chain ([docs/data-model.md](docs/data-model.md)). Drug catalogue and synthetic seed deferred to Phases 14 and 1b |
| 3 | Authentication and RBAC | ✅ Core done ([docs/auth.md](docs/auth.md)): registration, login, lockout, JWT + rotating refresh, logout, email verification, password reset, RBAC + relationship + consent checks, caregiver scopes. **Remaining:** phone OTP, TOTP MFA + step-up, breached-password check, SPA screens (with Phase 1b) |
| 4 | Doctor portal | ✅ Done ([docs/phases/04-doctor-portal.md](docs/phases/04-doctor-portal.md)); Playwright E2E pending |
| 5 | Patient portal | 🟡 Web portal done ([docs/phases/05-patient-portal.md](docs/phases/05-patient-portal.md)); consent centre, access log, data export, onboarding, PWA/Web Push and Playwright E2E remain |
| 6 | Caregiver / family system | ✅ Core done ([docs/phases/06-caregivers.md](docs/phases/06-caregivers.md)): dependants, invitations, granular scopes, dashboard, switcher, activity log. Handover at 18, missed-dose alerts and E2E remain |
| 7 | Prescription management | ✅ Core done ([docs/phases/07-prescriptions.md](docs/phases/07-prescriptions.md)): full fields, immutable versions with corrections, document view, PDF export, audit. Signed PDF and step-up MFA remain |
| 8 | AI prescription OCR | ✅ Core done ([docs/phases/08-ai-prescription-reading.md](docs/phases/08-ai-prescription-reading.md)): consented AI reading, per-field confidence and regions, review and correction, conversion, audit. Drug catalogue matching (Phase 14), live eval run and Tesseract in the image remain |
| 9–25 | | Planned |

## Milestones
| Milestone | Phases | Outcome |
|---|---|---|
| **M1: Secure core** | 0–3 | Data model, authentication, RBAC, consent, audit |
| **M2: Clinical MVP** | 4–11 | Doctor and patient portals, caregivers, prescriptions, OCR, medications, reminders, records. *First closed pilot with synthetic and consented test users.* |
| **M3: Intelligence and care** | 12–19 | Labs, AI assistants, safety engine, appointments, emergency, adherence, notifications |
| **M4: Production readiness** | 20–25 | Privacy hardening, admin, polish, test depth, deployment, audit. *Public launch.* |

Security and safety are **not** left until Phase 20. The policy engine, audit writer, consent checks and AI guardrails are built into each feature as it lands. Phases 20 and 25 harden and verify them.

---

### Phase 0: Project architecture and rules ✅
Monorepo (pnpm + Turborepo; uv), CI (lint, types, tests, contract drift, secret scan), pre-commit, docs: ARCHITECTURE, PROJECT_RULES, SECURITY_MODEL, AI_SAFETY, this roadmap, API conventions, threat model, DPDP register, ADRs 0001–0002.

### Phase 1: Backend foundation ✅
Settings, async DB, Redis, S3 storage (presigned, SSE enforced), PHI-redacting structured logs, problem+json errors, request IDs, security headers, rate limiter, policy marker plus a route-policy test, `/health` and `/ready`, Celery app and beat, OpenAPI → generated TS client, dev compose stack. 17 tests (3 integration).

### Phase 1b: Frontend foundation and compose split
- Replace the Next.js skeleton with a **React 19 + Vite + TypeScript + Tailwind 4** SPA in `apps/web` ([ADR 0002](docs/adr/0002-frontend-vite-spa.md)): React Router 7, TanStack Query, the generated API client, the folder structure from ARCHITECTURE §19, ESLint boundary rules, Vitest + MSW, a Playwright scaffold, i18n (en, hi) scaffolding, `vite-plugin-pwa` shell.
- Base UI primitives in `components/ui` (Button, Input, FormField, Dialog, Toast, DataTable skeleton) with Tailwind tokens from `packages/ui-tokens`.
- `infra/docker/web.Dockerfile` (Vite build → nginx, non-root, CSP), with nginx proxying `/api`.
- Split compose into `compose.yml` + `compose.dev.yml` + `compose.prod.yml`; switch Postgres to `pgvector/pgvector:pg16`; add ClamAV.
- **Exit:** `pnpm turbo run lint typecheck test build` passes; `docker compose up` serves the SPA at `/` and the API at `/api` from one origin; a Playwright smoke test loads the landing page.

### Phase 2: Database and medical data model ✅
**Delivered** (see [docs/data-model.md](docs/data-model.md)):
- `core/ids.py` (UUIDv7), `core/models.py` (base, mixins, patient-scoped composite keys), `core/crypto.py` (AES-256-GCM column encryption with a rotatable keyring, blind indexes), `modules/audit/service.py` (hash-chained writer and verifier).
- 31 tables across 14 modules: users and roles, patient profiles (including dependants without a login), doctor profiles, doctor–patient relationships, caregiver relationships and per-scope permissions, consent records, conditions, allergies, medical history, visits, clinical notes, prescriptions and items, medications, schedules, doses, adherence, test catalogue, orders, reports and results, appointments, follow-ups, health documents, emergency profiles and contacts, notifications, audit logs.
- Migrations `0001` (baseline) and `0002` (triggers: immutable signed notes, issued prescriptions, verified reports and consents; append-only audit; guarded deletes; `updated_at`).
- Tests: schema-convention unit tests, and integration tests for ownership boundaries, immutability, double booking, dose idempotency, encryption at rest and audit tamper detection. CI checks migration reversibility and model/migration drift.

**Moved to later phases:** vitals (Phase 4), drug catalogue and interaction tables (Phase 14), AI jobs and extractions (Phase 8), outbox (Phase 3), separate DB roles for migrations, app and read-only use (Phase 20), and a synthetic dev seed script (Phase 1b; placeholders only, no realistic medical data).

### Phase 3: Authentication and RBAC
- Identity module: OTP (stub SMS adapter in development), email + password (Argon2id), TOTP MFA (required for doctors and admins), EdDSA access JWT, rotating refresh tokens with reuse detection, sessions and devices, step-up, lockout, breached-password check.
- Access module: role → permission catalogue, `require(permission, patient_from=…)` policy engine (role ∧ relationship ∧ consent ∧ state), decision reason codes to audit, break-glass skeleton.
- Consent module core: notices, grant, withdraw, evaluate (cached with event invalidation).
- Doctor onboarding with `unverified` state.
- SPA: login, OTP, MFA enrolment, role/context switcher, silent refresh.
- **Exit:** policy-matrix test harness in place; auth integration tests (rotation, reuse revocation, lockout, step-up); OWASP authentication checklist items pass.

### Phase 4: Doctor portal
Doctor shell, dashboard (today, flags), patient list and search, add patient by invite (pending relationship → patient consent), patient chart (summary, problems, allergies, medications, timeline placeholder), encounters (visits) with vitals and notes (versioned, signed), availability basics.
**Exit:** Playwright: doctor login → invite patient → patient accepts → doctor opens chart → creates a visit and signs a note. Policy matrix green for the new routes.

### Phase 5: Patient portal
Patient shell, onboarding (notice, optional consents, profile, allergies, conditions, emergency info, routine and timezone), dashboard (Today), doctor linking with category choice, consent centre, "who accessed my data" view, data export request. PWA install and Web Push subscription.
**Exit:** Playwright: signup → onboarding → link doctor with limited categories → verify the doctor cannot see a withheld category.

### Phase 6: Caregiver / family system
Invitations with scopes and expiry, guardian creation of dependant profiles (minors with verifiable parental consent; dependent adults with a declaration), caregiver shell with a dependant switcher and an "Acting for" banner, on-behalf-of auditing, guardian consent management, the handover flow at age 18.
**Exit:** scope-enforcement tests (each scope allow and deny), expiry and revocation tests, E2E for a guardian managing a child dependant.

### Phase 7: Prescription management
Doctor e-prescription builder (catalogue search, dose, frequency, duration, food relation, instructions), lifecycle `draft → issued → active → completed/cancelled`, immutable versions, step-up on issue, PDF with registration details, patient view and download; issuing creates **pending** regimens that the patient confirms. Manual prescription entry by the patient (labelled patient-entered).
**Exit:** E2E: issue → patient receives → confirms schedule → regimens active; an issued prescription cannot be edited in place.

### Phase 8: AI prescription OCR
Upload flow (presigned, scan, EXIF strip), `ai_jobs` pipeline (preprocess → Tesseract/Cloud Vision → vision LLM → schema with per-field confidence and spans → catalogue matching), review UI with image crops, confidence states, mandatory confirmation of critical fields, the verification state machine, provenance labels. AI gateway, `ai_invocations`, fixtures mode, output validator v1.
**Exit:** OCR eval suite gates from [AI_SAFETY.md](AI_SAFETY.md) §9 pass; E2E for upload → review → correct → verify → regimen; there is no code path from `proposed` to active.

### Phase 9: Medication management
Medication list (active, paused, stopped, with source and provenance), schedules (RRULE, times, food relation, PRN, tapering as documented), inventory and refill thresholds, "patient-reported change" flow (flagged to the doctor; never framed as advice), caregiver dose logging.
**Exit:** schedule-expansion tests covering timezones and DST (including users travelling), PRN, tapering, and paused periods.

### Phase 10: Smart reminder engine
Rolling 48-hour dose materialisation, due-dose dispatch, escalation ladder (push → re-push → SMS → missed → caregiver alert), snooze, quiet hours, idempotent delivery, local opt-in offline cache for today's doses, refill reminders, and schedule-shift suggestions from take-time patterns (suggestions only; the patient confirms, and doctor-issued timing constraints are respected).
**Exit:** simulated-clock escalation test; no duplicate sends when workers are killed; load test with 10k patients × 4 doses per day on the `reminders` queue.

### Phase 11: Medical records and timeline
Record upload with type, date and provider; document viewer through short-lived URLs; optional AI summary (advisory, cited); unified timeline (visits, prescriptions, records, labs, appointments, SOS) with filters; FHIR R4 mappers (export groundwork).
**Exit:** timeline pagination and policy tests; FHIR mapper tests; summary citation-correctness eval passes.

### Phase 12: Tests and reports
Doctor lab orders; lab report upload → extraction (values and units as printed, deterministic unit conversion) → verification → `lab_results`; trends per analyte with reference ranges; abnormal flags notify the doctor (and caregivers with the right scopes).
**Exit:** lab extraction eval gates pass; the trend chart renders from verified data only.

### Phase 13: AI health assistant
Knowledge base ingestion (curated sources, review workflow, pgvector), deterministic red-flag triage, grounded patient chat with read-only policy-checked tools, citations, disclaimers, refusal templates, English, Hindi and Hinglish, per-user budgets, conversation encryption and retention, "report a problem".
**Exit:** red-team and red-flag suites meet the [AI_SAFETY.md](AI_SAFETY.md) §9 gates; prompt-injection test set is blocked.

### Phase 14: Medication safety engine
Structured interaction, allergy-class, duplicate-therapy and max-dose data imports (versioned and reviewed); the engine runs on prescription issue, OCR verification and manual add; severity handling (`info`/`warning`/`block`), doctor override with reason, plain-language explanation checked for meaning preservation.
**Exit:** rule matrix tests over known pairs; `block` cannot be bypassed without a doctor override; patient wording always includes the do-not-stop referral.

### Phase 15: Doctor AI assistant
Pre-visit summary, lab trend synthesis, adherence overview, note drafting from bullet points; all marked as "AI draft", cited, saved only on doctor sign-off.
**Exit:** citation-correctness eval; E2E: draft → edit → sign; unsigned drafts never appear in the chart.

### Phase 16: Appointments and follow-ups
Availability, booking, reschedule and cancel, reminders, follow-up tasks from prescriptions or visits, no-show tracking, optional teleconsult link field. Double booking is prevented by a PostgreSQL exclusion constraint.
**Exit:** concurrency test for double booking; reminder tests.

### Phase 17: Emergency system
SOS (location with consent, alerts to caregivers and contacts over all channels, idempotent), emergency profile and QR (revocable time-limited token, minimal dataset, noindex, audited and notified views), break-glass completed (reason, 60 minutes, notification, review queue), red-flag triage linking to SOS.
**Exit:** SOS E2E with mocked channels; token revocation and expiry tests; a break-glass audit review appears in the admin queue.

### Phase 18: Analytics and adherence
Nightly rollups (`adherence_daily`), adherence by medication, patient and period (PRN and paused periods excluded), streaks, missed-by-time-of-day patterns, doctor panel view, patient-friendly charts, de-identified platform aggregates for admins (k ≥ 10).
**Exit:** unit tests for the adherence maths edge cases; rollups are idempotent.

### Phase 19: Notifications
Full notification service: in-app inbox, Web Push, email, SMS (DLT templates), WhatsApp adapter (optional); preferences per channel and category; PHI-safe templates (en, hi); delivery receipts; retries; admin provider status.
**Exit:** adapter contract tests; preferences and quiet-hours tests; a test asserts that no external-channel template contains PHI placeholders.

### Phase 20: Security and privacy hardening
PostgreSQL RLS backstop on patient-scoped tables; DPDP rights workflows (access, correction, erasure including derivatives and embeddings, grievance, nominee) with SLA timers; retention jobs; key rotation jobs; audit-chain anchoring; anomaly alerts; incident runbooks (`docs/runbooks/`); ABDM sandbox integration (ABHA linking, consent artefacts, FHIR exchange); CSP tightening; security.txt.
**Exit:** OWASP ASVS L2 checklist reviewed; erasure tests prove that derivatives are removed; ABDM sandbox link flow works.

### Phase 21: Admin portal
Doctor verification queue, user management (suspend, unlock, force logout, MFA reset with a checklist), drug catalogue, interaction data and knowledge-base review and activation, feature flags, failed-job replay, AI usage, cost and eval dashboard, audit search (audited), DPDP request queue, break-glass reviews. MFA step-up on every admin write.
**Exit:** E2E verify-doctor flow; admin cannot read clinical data (policy test).

### Phase 22: UI/UX polish
Design-system pass, WCAG 2.2 AA, large-text mode, complete Hindi translation, dark mode, empty, loading and error states, performance budgets (LCP < 2.5 s on mid-range Android over 4G), PWA polish.
**Exit:** axe shows no serious violations; Lighthouse ≥ 90 (performance, accessibility, best practices) on key pages.

### Phase 23: Testing
Close the gaps: policy matrix at 100% of patient-scoped routes, E2E per journey (ARCHITECTURE §22–26), k6 load tests (auth, reminders, uploads), chaos tests (kill workers, drop Redis), full AI eval suites in CI, mutation testing on `access` and `safety`.
**Exit:** coverage targets met; all suites green; load targets met.

### Phase 24: Docker and deployment
`compose.prod.yml` hardening (pinned digests, read-only filesystems, limits, secrets), TLS proxy, managed Postgres, Redis and object storage in an Indian region, CI/CD (build → sign → scan → staging → approval → production), migration step, OpenTelemetry, error tracking with PHI scrubbing, backups plus PITR, restore drill, uptime and queue-depth alerts.
**Exit:** automatic staging deploys from `main`; tested rollback; restore drill within RTO/RPO.

### Phase 25: Final security and production audit
Threat-model refresh, SAST, dependency and container scans clean, external pentest (IDOR sweep, auth, uploads, prompt injection), DPDP legal review and processor contracts, clinical safety review of AI features, go-live runbook, on-call rota.
**Exit:** all [SECURITY_MODEL.md](SECURITY_MODEL.md) §14 acceptance criteria checked; no open critical or high findings.

---

## External dependencies (collected when needed)
| Needed for | Item |
|---|---|
| Phase 3 | SMS provider account with DLT sender ID and templates (stub until then) |
| Phase 5 | VAPID keys for Web Push (generated locally) |
| Phase 8 | Anthropic API key (zero-retention terms); optional Google Cloud Vision |
| Phase 14 | Licensed or open interaction and drug datasets (licences reviewed) |
| Phase 20 | ABDM sandbox credentials |
| Phase 24 | Hosting account in an Indian region, domain, TLS |
| Phase 25 | Pentest vendor, legal counsel for DPDP |
