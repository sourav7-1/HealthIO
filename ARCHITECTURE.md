# Architecture

**AI-Powered Personal Health Record & Medication Management Platform** ("Health Io")

| | |
|---|---|
| Status | Accepted baseline, v1.0 (2026-09-24) |
| Companion docs | [PROJECT_RULES.md](PROJECT_RULES.md) · [SECURITY_MODEL.md](SECURITY_MODEL.md) · [AI_SAFETY.md](AI_SAFETY.md) · [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md) |
| Detail docs | [docs/api-conventions.md](docs/api-conventions.md) · [docs/threat-model.md](docs/threat-model.md) · [docs/dpdp-register.md](docs/dpdp-register.md) · [docs/adr/](docs/adr/) |

The platform is a **health-information and medication-management system**. It is not an autonomous clinician. Every clinical decision stays with a person (see [AI_SAFETY.md](AI_SAFETY.md)).

Core flow:

```
Doctor → Patient → Medical Records → Visits → Tests → Prescriptions → Medicines
       → Reminders → Adherence → Follow-up → AI assistance (advisory, verified by humans)
```

## Contents
1. [High-level architecture](#1-high-level-architecture)
2. [Frontend architecture](#2-frontend-architecture)
3. [Backend architecture](#3-backend-architecture)
4. [Database architecture](#4-database-architecture)
5. [AI architecture](#5-ai-architecture)
6. [Authentication architecture](#6-authentication-architecture)
7. [RBAC architecture](#7-rbac-architecture)
8. [Consent architecture](#8-consent-architecture)
9. [File storage architecture](#9-file-storage-architecture)
10. [Notification architecture](#10-notification-architecture)
11. [Audit logging architecture](#11-audit-logging-architecture)
12. [Security architecture](#12-security-architecture)
13. [API versioning strategy](#13-api-versioning-strategy)
14. [Error handling strategy](#14-error-handling-strategy)
15. [Background job strategy](#15-background-job-strategy)
16. [Docker architecture](#16-docker-architecture)
17. [Development environment](#17-development-environment)
18. [Production environment](#18-production-environment)
19. [Frontend folder structure](#19-frontend-folder-structure)
20. [Backend folder structure](#20-backend-folder-structure)
21. [Entity relationship overview](#21-entity-relationship-overview)
22. [Complete user journey](#22-complete-user-journey)
23. [Doctor journey](#23-doctor-journey)
24. [Patient journey](#24-patient-journey)
25. [Caregiver journey](#25-caregiver-journey)
26. [Admin journey](#26-admin-journey)

---

## 1. High-level architecture

The backend is a **modular monolith**: one deployable FastAPI application made of strictly bounded domain modules, plus Celery workers that run the same code base. There are no microservices at this stage. Module boundaries (service interfaces, domain events, no cross-module table access) are drawn so that a module such as `ai` or `notifications` can later be split into its own service without rewriting callers.

```
                         ┌──────────────────────────────────────────────┐
  Browser (React SPA,    │  Reverse proxy (nginx) — TLS, security       │
  installable PWA)  ───► │  headers, static SPA, /api → API             │
                         └──────────────┬───────────────────────────────┘
                                        │ same origin: /  and  /api/v1
                         ┌──────────────▼───────────────────────────────┐
                         │  FastAPI modular monolith (stateless, N×)    │
                         │  ┌────────┐┌────────┐┌──────────┐┌────────┐  │
                         │  │identity││ access ││ clinical ││  ...   │  │
                         │  └────────┘└────────┘└──────────┘└────────┘  │
                         │  core: config · db · policies · audit · errors│
                         └───┬──────────┬──────────┬───────────┬────────┘
                             │          │          │           │ outbox / tasks
                   ┌─────────▼──┐ ┌─────▼────┐ ┌───▼───────┐ ┌─▼─────────────────┐
                   │ PostgreSQL │ │  Redis   │ │ Object    │ │ Celery workers +  │
                   │ + pgvector │ │ cache,   │ │ storage   │ │ beat (reminders,  │
                   │            │ │ limits,  │ │ (S3 API)  │ │ AI jobs, notifs,  │
                   │            │ │ broker   │ │           │ │ maintenance)      │
                   └────────────┘ └──────────┘ └───────────┘ └──┬────────────────┘
                                                                  │ outbound only
                           ┌──────────────────────────────────────▼──────────────┐
                           │ External: LLM + vision (Claude), OCR (Tesseract      │
                           │ local / Cloud Vision), SMS, email, Web Push, ClamAV  │
                           │ (sidecar), ABDM gateway (later)                      │
                           └─────────────────────────────────────────────────────┘
```

**Key properties**
- **Stateless API:** sessions live in PostgreSQL and Redis, so API containers scale horizontally.
- **One database, one schema per module ownership.** Each table has exactly one owning module (§4).
- **Synchronous requests stay fast.** Slow or unreliable work (OCR, LLM calls, sending messages, rollups) always runs as a background job (§15).
- **All external AI and messaging calls are outbound from workers**, behind adapter interfaces that can be swapped.
- **Data residency:** production data is hosted in India (DPDP Act 2023; see [docs/dpdp-register.md](docs/dpdp-register.md)).

## 2. Frontend architecture

**Stack:** React 19, TypeScript (strict), Vite, Tailwind CSS 4, React Router 7 (data router), TanStack Query, React Hook Form + Zod, a Radix-based component library in `components/ui`, Recharts, `vite-plugin-pwa` for installability and Web Push.

**One SPA with four portals.** A single React app contains role-based areas (`/doctor`, `/patient`, `/caregiver`, `/admin`). Each area is a lazily loaded route tree, so a patient never downloads admin code. A user can hold several roles (for example, a doctor who is also a patient) and switches between them with an explicit role/context switcher. The active context is sent to the API and recorded in audit events.

**Layers**
| Layer | Responsibility |
|---|---|
| `app/` | Bootstrapping: providers (QueryClient, auth, theme, i18n), router, error boundaries |
| `routes/` | Route modules per portal. They compose feature components; minimal logic |
| `features/<domain>/` | Domain UI and hooks (`medications`, `prescriptions`, `ocr-review`, …): components, query hooks, forms, Zod schemas |
| `components/ui/` | Design-system primitives (Button, Dialog, DataTable, FormField, …). No domain knowledge |
| `lib/` | API client instance, auth token handling, formatting (dates, doses, timezones), permission helpers |
| `packages/api-client` | **Generated** typed client from the backend OpenAPI schema. The only source of API types |

**State**
- **Server state:** TanStack Query only. Query keys are organised per resource. Mutations invalidate related keys. No duplicated server data in global stores.
- **Client state:** React context for session/role context; component state for everything else. No Redux.
- **Forms:** React Hook Form + Zod. Zod validation mirrors backend rules for user experience only; the backend is the authority.

**Auth in the browser:** the access token is held **in memory only**. The refresh token is an `httpOnly`, `Secure`, `SameSite=Strict` cookie scoped to `/api/v1/auth`. A silent refresh runs on load and before expiry (§6). Nothing sensitive goes into `localStorage`.

**PWA and reminders:** the SPA is installable. Web Push delivers dose reminders even when the tab is closed. A service worker caches the app shell only. Health data is **not** cached offline by default; an explicit per-device opt-in allows today's dose schedule to be cached for offline reminders.

**Clinical UX rules:** AI-derived values are always visually marked (badge plus confidence) until verified; low-confidence fields block submission until the user confirms them; destructive or clinical actions need explicit confirmation; WCAG 2.2 AA; a large-text mode for older users; English and Hindi from the start (i18n keys, no hard-coded strings).

**Mobile:** there is no native app at this stage. The PWA covers patients and caregivers. A React Native app can later reuse `packages/api-client` and `packages/ui-tokens` (see [docs/adr/0002-frontend-vite-spa.md](docs/adr/0002-frontend-vite-spa.md)).

## 3. Backend architecture

**Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, Celery + Redis, structlog.

### 3.1 Modules (bounded contexts)
| Module | Owns | Key responsibilities |
|---|---|---|
| `identity` | users, credentials, sessions, mfa_factors, devices | Sign-up, login, OTP, MFA, token rotation, device list |
| `access` | roles, role_permissions, user_roles | RBAC catalogue, policy engine, relationship resolution, break-glass |
| `consent` | consents, consent_events, privacy_notices | Grant, withdraw and evaluate consent; notice versions; ABDM consent artefacts (later) |
| `patients` | patients, dependents, emergency_profiles | Patient profiles (with or without a login), dependants, demographics, emergency info |
| `care_team` | doctor_profiles, clinics, care_relationships | Doctor profiles and verification, doctor↔patient relationships |
| `caregivers` | caregiver_links, invitations | Caregiver invitations, scoped permissions, guardianship, expiry |
| `clinical` | encounters (visits), clinical_notes, conditions, allergies, vitals | Visits, notes, problem list, allergies, vitals |
| `prescriptions` | prescriptions, prescription_items, prescription_versions | Doctor e-prescriptions, uploaded or manual prescriptions, lifecycle, PDF |
| `medications` | medications, medication_schedules, dose_events, inventory | Active regimens, schedules, dose logging, refill tracking |
| `reminders` | reminder_jobs, reminder_policies | Materialising due doses, escalation ladders, snooze, quiet hours |
| `records` | medical_records, files, timeline (view) | Document store, metadata, unified timeline |
| `labs` | lab_orders, lab_reports, lab_results | Test orders, report ingestion, analyte results, trends |
| `appointments` | appointments, availability, follow_ups | Scheduling, follow-up tasks |
| `emergency` | emergency_contacts, sos_events, emergency_access_tokens | SOS, emergency QR view, break-glass requests |
| `drugs` | drugs, drug_products, interactions, allergy_classes | Drug catalogue (generics and Indian brands), interaction and allergy data |
| `safety` | safety_checks, safety_alerts, overrides | Deterministic medication-safety engine |
| `ai` | ai_jobs, extractions, ai_conversations, ai_messages, kb_documents, kb_chunks, ai_invocations | OCR/extraction pipelines, summaries, assistants, knowledge retrieval, AI audit |
| `notifications` | notifications, notification_preferences, deliveries, templates | Multi-channel delivery, preferences, in-app inbox |
| `audit` | audit_events | Append-only, hash-chained audit trail and patient-visible access log |
| `analytics` | adherence_daily, adherence_summary | Adherence rollups and dashboards |
| `admin` | feature_flags, verification_reviews, dpdp_requests | Admin workflows, platform settings, data-rights requests |

### 3.2 Inside a module
```
modules/medications/
├── router.py      HTTP layer: parse → call service → return schema. Declares a policy per route.
├── schemas.py     Pydantic request/response models (the API contract)
├── models.py      SQLAlchemy models (tables owned by this module only)
├── repo.py        Queries; the only place that touches this module's tables
├── service.py     Business logic and transactions; the module's public interface
├── policies.py    Access rules for this module's resources
├── events.py      Domain events this module publishes
└── tasks.py       Celery tasks for this module (if any)
```

### 3.3 Communication between modules
- **Synchronous:** module A calls module B's **service** (Python interface). It never imports B's repo or models. Import-linter contracts in CI enforce this.
- **Asynchronous:** domain events (for example `prescription.issued`, `dose.missed`, `consent.withdrawn`) are written to an **outbox table in the same transaction** as the change. A relay publishes them to Celery, and subscribers handle them idempotently. This keeps side effects (notifications, safety re-checks, timeline updates) reliable without distributed transactions, and it is the seam for later extracting a service.
- **Unit of work:** a request gets one DB session and transaction. Services receive it through dependency injection and never commit on their own.

### 3.4 Request pipeline
```
nginx → RequestContext (request id, access log) → SecurityHeaders → CORS (dev only)
      → auth (token → Principal) → policy (RBAC + relationship + consent) → router
      → service (UoW) → repo → DB   ⇒ audit event + outbox in the same transaction
```

## 4. Database architecture

- **PostgreSQL 16** with the **pgvector** extension (for knowledge retrieval) and `pgcrypto`. One database. Tables are grouped by owning module; a Postgres schema per module (`identity.*`, `clinical.*`, …) is considered once the table count justifies it. Until then, table-name prefixes are not needed because module ownership is recorded in code.
- **Keys:** UUIDv7 primary keys (time-ordered, index-friendly, not guessable). Never expose sequential IDs.
- **Standard columns:** `id`, `created_at`, `updated_at` (`timestamptz`, UTC), `created_by`, `updated_by`; `deleted_at` on clinical and user-generated tables (soft delete); `version` for optimistic locking on records that are edited concurrently (prescriptions, medications, notes).
- **Patient scoping:** every patient-owned row carries `patient_id` (indexed). This makes policy checks, consent filters, data export and erasure mechanical. Row-Level Security on `patient_id` is planned as defence in depth (roadmap Phase 20).
- **Clinical immutability:** issued prescriptions, signed notes and verified extractions are never updated in place. A change creates a new version row (`*_versions`) with a reason. This preserves the medico-legal history.
- **Encryption:** disk and backup encryption at the storage layer, plus **application-level envelope encryption** (AES-256-GCM data keys wrapped by a KMS/master key) for the most sensitive free-text and identifiers: clinical notes, ABHA/Aadhaar-like identifiers, emergency notes, AI conversation content. Encrypted columns cannot be searched; searchable identifiers are stored as keyed HMAC blind indexes.
- **JSONB** only for AI raw outputs, provider payloads and flexible metadata, never for core relational facts.
- **Vocabularies:** drugs mapped to generic, strength and form, plus ATC code where available; labs use LOINC codes where available; conditions accept free text with an optional ICD-10 code (ICD-10 is widely used in India). FHIR R4 mappings are produced at the edges (ABDM exchange, export), not as the internal model.
- **Migrations:** Alembic only; each migration is reversible, reviewed, and safe to run online (expand → migrate → contract for breaking changes).
- **Connections:** asyncpg pool per API container, PgBouncer (transaction mode) in production once there are several containers.
- **Backups:** daily full backup plus WAL archiving for point-in-time recovery; restore drills are part of the release checklist.

## 5. AI architecture

The AI module is a set of **pipelines behind a gateway**. Every AI output lands in a *proposed* state and needs verification by a person before it becomes clinical data. Full rules: [AI_SAFETY.md](AI_SAFETY.md).

```
             ┌─────────────────────────── ai module ───────────────────────────────┐
 request ──► │ Pipeline (OCR extraction | lab extraction | summary | explain | chat) │
             │   1. Input guard: PHI minimisation, injection screening, red-flag     │
             │      triage (symptom → emergency guidance, no LLM needed)             │
             │   2. Context builder: policy-checked data fetch for this patient      │
             │   3. Retrieval (explain/chat): pgvector over curated knowledge base   │
             │   4. Model gateway ─► provider adapter (Claude; OCR engines)          │
             │   5. Output validation: JSON schema, forbidden-intent classifier,     │
             │      citation check, confidence thresholds                            │
             │   6. Persist as PROPOSED + ai_invocations audit row                   │
             └─────────────────────────────────────┬────────────────────────────────┘
                                                   ▼
                      Human review UI (verify / edit / reject)  ──►  clinical tables
```

**Components**
- **Model gateway (`ai/gateway.py`):** a single interface for LLM, vision and embeddings. It handles provider selection, timeouts, retries, per-user and global budgets, zero-retention settings, and PHI minimisation (patient names and identifiers are replaced by placeholders before sending when the task does not need them).
- **OCR layer:** image preprocessing (OpenCV: deskew, denoise, contrast) → OCR engine (Tesseract `eng+hin` locally, Google Cloud Vision optional) → text with bounding boxes. The vision LLM receives **both** the image and the OCR text and returns fields with per-field confidence and source spans. Disagreement between OCR and the vision reading lowers confidence.
- **Normalisation:** extracted drug text is matched against the drug catalogue (exact → brand→generic → fuzzy). An unmatched drug stays **unmatched** and is never guessed.
- **Knowledge base (retrieval):** curated, versioned documents only (drug monographs and labels from trusted sources, the national essential medicines list, internal reviewed content). They are chunked and embedded into `kb_chunks` (pgvector) with source, version and review date. Explanations must cite retrieved chunks or the patient's own records; with no supporting source, the answer is "I don't have verified information on that".
- **Structured safety data:** interaction and allergy warnings come from the deterministic `safety` engine over structured tables. The LLM only rephrases a warning that already exists.
- **Prompts:** versioned files under `ai/prompts/`, each with an ID and version stored on every invocation.
- **Evaluation:** every pipeline has a gold dataset (synthetic or de-identified) and thresholds that run in CI (field accuracy, hallucination rate, refusal correctness, red-flag recall).
- **AI audit:** `ai_invocations` stores pipeline, prompt version, model, input and output hashes, token counts, latency, the safety verdict, and the reviewer decision.

**Pipelines at launch:** prescription OCR extraction, lab report extraction, record summarisation, medication explanation (retrieval-based), patient assistant (grounded chat), doctor assistant (pre-visit summary, note drafting). All of them are advisory.

## 6. Authentication architecture

| Actor | Primary login | Second factor |
|---|---|---|
| Patient, caregiver | Mobile OTP (SMS) or email + password | Optional TOTP; required for caregivers managing three or more dependants |
| Doctor | Email + password (Argon2id) | **Required** TOTP (WebAuthn/passkeys later) |
| Admin | Email + password | **Required** TOTP, plus step-up for sensitive actions |
| Dependant without a login | Has no credentials; acts only through a guardian caregiver | — |

**Tokens**
- **Access token:** a signed JWT (EdDSA), valid for **10 minutes**. Claims: `sub` (user ID), `sid` (session ID), `roles`, `ctx` (active role context), `amr` (authentication methods), `aal`. No health data in tokens.
- **Refresh token:** an opaque random value, stored only as a hash, valid for 14 days with sliding rotation. **Every refresh rotates it.** Reusing an old refresh token revokes the whole session family (a sign of theft).
- **Sessions table:** device label, IP, user agent, created and last-seen times. Users can see and revoke their sessions. Password or MFA changes revoke all other sessions.
- **Step-up authentication:** sensitive actions (issuing a prescription, break-glass, data export, admin changes, adding a caregiver) need a recent MFA (`auth_time` within 5 minutes); otherwise the API answers `401 step-up-required`.
- **OTP:** 6 digits, 5-minute TTL, stored hashed, at most 5 attempts, rate-limited by phone number and by IP, SMS through a DLT-registered template.
- **Account protection:** progressive lockout, notifications for a new device or login, breached-password check (k-anonymity range API) at sign-up and password change.
- **Doctor onboarding:** a doctor account starts `unverified`. An admin checks the medical council registration number and documents. Until verified, a doctor cannot be linked to patients or issue prescriptions.

## 7. RBAC architecture

Access = **role permission ∧ relationship ∧ consent ∧ state checks**. Evaluated by a single policy engine (`access` module) on every request. Denied by default.

**1. Roles → permissions (static, in code, versioned)**
| Role | Examples of permissions |
|---|---|
| `patient` | `self:read`, `self:write`, `medication:manage_own`, `caregiver:invite`, `consent:manage` |
| `caregiver` | Only what a caregiver link grants (below); nothing global |
| `doctor` | `patient:read_linked`, `encounter:write`, `prescription:issue`, `lab:order`, `note:sign` |
| `admin` | `doctor:verify`, `user:manage`, `catalog:manage`, `audit:read`, `flags:manage`. **No clinical read by default** |
| `support` (later) | Limited, audited, time-boxed access to account metadata; no clinical data |

**2. Relationship (resource-level) checks**
- **Self:** the principal is the patient.
- **Doctor:** there is an `active` `care_relationship(doctor, patient)`, created when the patient accepts a doctor or the doctor adds the patient and the patient consents.
- **Caregiver:** there is an `active`, unexpired `caregiver_link(caregiver, patient)` with the needed **scope**:
  `view_profile`, `view_medications`, `manage_medications`, `log_doses`, `view_records`, `upload_records`, `view_labs`, `manage_appointments`, `receive_alerts`, `use_ai_assistant`, `manage_emergency_info`, `manage_caregivers` (guardians only).
- **Guardian:** a caregiver link with `is_guardian=true` (parent of a minor, legal representative of a dependent adult) may give consent on the patient's behalf.

**3. Consent** (§8): a data category can be withheld from a doctor or caregiver even when a relationship exists.

**4. State:** doctor verified, account not suspended, link not expired, record not locked.

**Break-glass:** in an emergency, a verified doctor without a relationship can request temporary read access to the emergency dataset. They must state a reason; access lasts 60 minutes; the patient and guardians are notified immediately; the event is flagged for review.

**Implementation:** routes declare `Depends(require("prescription:issue", patient_from="path"))`. The engine resolves the principal, loads relationships and consents (cached per request), and returns an allow or deny decision **with a reason code** that is written to the audit event. A CI test fails any route without exactly one policy (already in place: `apps/api/tests/test_route_policies.py`). A policy matrix test (role × route × relationship) is maintained from Phase 3.

## 8. Consent architecture

Consent follows the DPDP Act 2023 (free, specific, informed, unambiguous, withdrawable) and is shaped to map to ABDM consent artefacts later.

**Model**
- `privacy_notices(version, language, purposes, published_at)`: consent is always tied to the notice version that was shown.
- `consents`: `grantor` (patient, or guardian for a dependant), `grantee` (a doctor, caregiver or organisation, or a platform purpose such as `ai_assistant`), `purpose` (`care_delivery`, `medication_reminders`, `ai_processing`, `caregiver_support`, `research` (off by default), …), `data_categories` (`demographics`, `medications`, `prescriptions`, `records`, `labs`, `notes`, `emergency`), `access` (`read`/`write`), `valid_from`, `valid_until`, `status` (`active`, `withdrawn`, `expired`).
- `consent_events`: an append-only history of grant, modify and withdraw, with actor and notice version.

**Rules**
- **Platform consents** (reminders, AI processing, SMS/WhatsApp) are asked for separately and are optional where the law allows. Refusing AI processing turns off AI features for that patient; the rest of the platform keeps working.
- **Sharing consents** are created when a care relationship or caregiver link is established, with a default set of categories that the patient can narrow.
- **Withdrawal** takes effect immediately: the policy engine reads consent state on every request (cached for at most 60 seconds, and the cache is invalidated on the `consent.withdrawn` event). Data already shared is not recalled, but future access stops.
- **Minors and dependants:** consent is given by the guardian (verifiable parental consent, DPDP s.9). When a minor turns 18, a handover flow gives them control and asks them to review each grant again.
- **Emergency (legitimate use, DPDP s.7):** break-glass and SOS processing are recorded as legitimate-use events, not consent.
- A patient-facing **consent centre** lists every grantee, purpose, category and expiry, with a one-click withdraw.

## 9. File storage architecture

- **Store:** S3-compatible object storage (MinIO in development; managed S3-compatible storage in an Indian region in production). One private bucket per environment. Public access is blocked; versioning is on.
- **Upload flow:**
  1. The client asks the API for an upload with its purpose, MIME type and size.
  2. The API checks policy and returns a **presigned POST**, limited to that key, content type, size (e.g. 15 MB) and SSE, valid for 5 minutes.
  3. The client uploads directly to storage.
  4. The client confirms, and the API records a `files` row with status `pending_scan`.
  5. A worker checks the magic bytes against the declared type, computes SHA-256, runs a **ClamAV scan**, strips EXIF/GPS metadata from images, and makes thumbnails and derivatives.
  6. The status becomes `clean` (usable) or `quarantined`.
- **Keys:** `env/patients/{patient_id}/{category}/{file_id}` for patient files and `env/doctors/{doctor_id}/verification/{file_id}` for doctor documents. Names never contain real names or other PHI; the original filename is stored encrypted in the database.
- **Download:** always goes through the API. Policy and consent are checked, an audit event is written, then a **presigned GET valid for 60 seconds** is returned with `Content-Disposition: attachment` for documents. Files are never served from the API origin.
- **Encryption:** server-side encryption (SSE-S3 or SSE-KMS) is always required. Uploads without it fail (already enforced in `core/storage.py`).
- **Retention:** soft delete marks the row; physical deletion happens only through the DPDP erasure workflow or the retention job, and is logged.
- **Allowed types:** JPEG, PNG, HEIC (converted), PDF. Office documents and executables are rejected.

## 10. Notification architecture

```
domain event (dose.due, dose.missed, appointment.reminder, sos.triggered, …)
   └─► notifications.service.notify(recipient, template, data, priority, idempotency_key)
          ├─ resolve recipients (patient, caregivers with receive_alerts, doctor)
          ├─ apply preferences, quiet hours (not for SOS) and consent
          ├─ render template per channel and language (PHI-safe variant for external channels)
          └─ write notifications + deliveries rows (outbox) ──► Celery "notifications" queue
                                                              └─► channel adapters
```

- **Channels:** in-app inbox (always on), Web Push (VAPID), email, SMS (DLT-registered templates, required in India), WhatsApp (later). Each channel is an adapter behind one interface and can be swapped.
- **PHI-safe content:** by default, external channels and lock-screen push carry **no clinical detail** ("You have a medicine reminder" / "Time for your 9 AM dose"). Patients can opt in to showing medicine names in push. SMS never contains medicine names, conditions or results.
- **Reliability:** idempotency keys (`dose_event_id:attempt`), retries with exponential backoff, a delivery status per attempt, and provider webhooks for delivery receipts.
- **Escalation ladders** (reminders): push at T → push again at T+10 min → SMS at T+30 min (if enabled) → the dose is marked missed at T+60 min, and caregivers with `receive_alerts` are alerted. Each step is configurable per patient.
- **Priority:** the `sos` and `critical` levels skip quiet hours and use every enabled channel at the same time.

## 11. Audit logging architecture

Audit logs are separate from application logs. Application logs are operational and redacted, with no PHI. The **audit trail** is the legal record of who did what to which patient's data.

- **Table `audit_events`** (append-only): `id`, `occurred_at`, `actor_user_id`, `actor_role_context`, `on_behalf_of_patient_id` (caregiver actions), `patient_id`, `action` (e.g. `prescription.issue`, `record.view`, `consent.withdraw`, `break_glass.start`), `resource_type`, `resource_id`, `outcome` (`allowed`/`denied`/`error`), `policy_reason`, `request_id`, `ip` (truncated), `user_agent_hash`, `justification` (break-glass, overrides), `changes` (field names only, never values), `prev_hash`, `hash`.
- **What is audited:** every write to patient data; every **read** of clinical data (record, note, lab, prescription, AI summary); every authentication event; every consent change; every admin action; every denied access attempt; every AI invocation (linked to `ai_invocations`).
- **Integrity:** events are written **in the same transaction** as the change they describe, so there is no change without an audit row. Each row stores `hash = SHA-256(prev_hash ‖ canonical_row)`. A nightly job verifies the chain and anchors the daily head hash in write-once storage. The application's database role has `INSERT`/`SELECT` only on `audit_events`; `UPDATE` and `DELETE` are revoked.
- **Visibility:** patients (and guardians) see "Who accessed my data" in their privacy centre. Admins with `audit:read` search the audit trail; that search is itself audited.
- **Retention:** 7 years (to be confirmed in legal review). Partitioned monthly.

## 12. Security architecture

Summary only; the full model is in [SECURITY_MODEL.md](SECURITY_MODEL.md).

- **Defence in depth:** TLS 1.2+ at the edge → security headers and CSP → authentication → policy engine (RBAC + relationship + consent) → service-level invariants → database constraints → encryption at rest → audit.
- **Transport:** HTTPS only, HSTS with preload, TLS between services inside the network where the platform allows it.
- **Data:** disk, backup and object-store encryption; envelope encryption for the most sensitive columns; keyed blind indexes for searching encrypted identifiers.
- **Application:** Pydantic validation on every input, SQLAlchemy parameterised queries, output schemas (no ORM leakage), rate limits, upload scanning, no rejected input echoed, no PHI in logs (redaction processor in place).
- **Secrets:** environment or secret-manager injection only; gitleaks in pre-commit and CI; rotation runbook.
- **Supply chain:** lockfiles (`uv.lock`, `pnpm-lock.yaml`), Dependabot, pip-audit and pnpm audit, Trivy image scans, pinned base images.
- **Detection:** alerts on audit anomalies (bulk reads, repeated denials, break-glass use, after-hours admin activity).

## 13. API versioning strategy

- **URL-prefix versioning:** `/api/v1/...`. The major version changes only for breaking changes.
- **Within a version, only additive changes:** new endpoints, new optional fields, new enum values (clients must tolerate unknown enum values). Removing or renaming fields, changing types, or tightening validation counts as breaking.
- **Breaking change process:** mount the `v2` router beside `v1` (they share services; only the schemas and routers differ). Mark v1 endpoints with `Deprecation` and `Sunset` headers and in OpenAPI. Keep them for at least 6 months.
- **Contract:** Pydantic schemas → OpenAPI → generated `packages/api-client`. CI fails if the committed client differs from the schema (already in place). An OpenAPI diff check in CI flags breaking changes in PRs.
- **Events and webhooks** carry `schema_version` in their payload.
- Details: [docs/api-conventions.md](docs/api-conventions.md).

## 14. Error handling strategy

- **Wire format:** RFC 9457 `application/problem+json` for every error (already implemented in `core/errors.py`): `type` (stable code URI), `title`, `status`, `detail`, `instance`, `request_id`, `errors[]` for validation.
- **Domain errors:** services raise typed `AppError` subclasses with stable codes, for example `consent-required`, `relationship-required`, `step-up-required`, `doctor-unverified`, `prescription-locked`, `extraction-unverified`, `safety-block-override-required`, `version-conflict`. Routers never build error responses themselves.
- **No leakage:** 500 responses carry no internals. Validation errors never echo input. Authorisation failures on patient resources return **404** when revealing existence would leak information (IDOR hardening) and 403 when the resource is already known to the caller.
- **Frontend:** a typed `ApiError` wraps problem documents; the UI switches on `type` codes (for example, `step-up-required` opens the MFA dialog, and `version-conflict` offers "reload and compare"). Route-level error boundaries show a friendly message plus the `request_id` for support. Toasts are for transient errors, inline messages for field errors.
- **Workers:** tasks separate retryable errors (network, 5xx, rate limits) from permanent ones. After the last retry, a task writes a `failed_jobs` row and raises an alert. It never fails silently.
- **Observability:** every error log carries `request_id` and `trace_id`; 5xx and unhandled task errors go to error tracking (with PHI scrubbing).

## 15. Background job strategy

**Celery + Redis** (broker), with the **same image and code base** as the API.

| Queue | Work | Notes |
|---|---|---|
| `reminders` | Dose materialisation, due-dose dispatch, escalations | Highest priority; dedicated workers; small tasks |
| `notifications` | Channel delivery, retries, receipts | Rate-limited per provider |
| `ai` | OCR, extraction, summarisation, embeddings | Long-running; low concurrency; per-user budgets |
| `events` | Outbox relay, domain event subscribers | Exactly-once effect via idempotent handlers |
| `files` | Virus scan, EXIF strip, thumbnails | Isolated; ClamAV sidecar |
| `maintenance` | Adherence rollups, retention, audit-chain verification, backups check | Nightly or off-peak |

- **Scheduling:** Celery beat. Every minute, a job materialises dose events for a rolling 48-hour window (idempotent on `(schedule_id, scheduled_at)`) and dispatches due ones.
- **Delivery semantics:** `acks_late` with `reject_on_worker_lost` (already configured) gives at-least-once delivery, so **every task must be idempotent**, keyed on a natural key.
- **Transactional outbox:** events are written with the business change and relayed by a beat task (`SELECT … FOR UPDATE SKIP LOCKED`), so a crash between commit and publish loses nothing.
- **Retries:** exponential backoff with jitter; a maximum attempt count per task type; a `failed_jobs` table as the dead-letter record, with replay from the admin portal.
- **Progress:** long AI jobs expose status through `ai_jobs` (`queued → running → needs_review → verified/rejected/failed`). The SPA polls, with Server-Sent Events added later.
- **Time:** everything is scheduled in UTC; patient-facing times are computed from the patient's IANA timezone (DST-safe with `zoneinfo`).

## 16. Docker architecture

| Image / service | Built from | Role |
|---|---|---|
| `api` | `infra/docker/api.Dockerfile` (multi-stage, uv, non-root) | FastAPI via uvicorn |
| `worker-*` | same image as `api`, different command | Celery workers per queue group |
| `beat` | same image | Celery beat (exactly one instance) |
| `web` | `infra/docker/web.Dockerfile` (Vite build → nginx, non-root) | Serves the SPA and reverse-proxies `/api` |
| `postgres` | `pgvector/pgvector:pg16` | Database (development and CI; managed service in production if available) |
| `redis` | `redis:7-alpine` | Cache, rate limits, broker |
| `minio` | `quay.io/minio/minio` | S3-compatible storage (development and CI) |
| `clamav` | `clamav/clamav` | Upload scanning |
| `mailpit` | `axllent/mailpit` | Development email catcher |

**Compose layout**
- `compose.yml`: base service definitions shared by all environments.
- `compose.dev.yml`: bind mounts, hot reload, exposed ports, dev-only services (mailpit), and a static MinIO KMS key.
- `compose.prod.yml`: pinned image tags, resource limits, restart policies, TLS proxy, secrets files, no dev services, internal network only except the proxy.

The current `docker-compose.dev.yml` is split into these files in roadmap Phase 1b.

**Image rules:** multi-stage builds, pinned base images by digest in production, non-root user, read-only root filesystem where possible, a healthcheck in every service, one process per container, no secrets baked in.

## 17. Development environment

```
Host (Windows / macOS / Linux)
├─ uv (Python 3.12) ──► apps/api     uv run uvicorn app.main:app --reload   (:8000)
├─ pnpm (Node 24)   ──► apps/web     pnpm dev (Vite)                        (:5173, proxies /api → :8000)
└─ Docker Compose (compose.yml + compose.dev.yml)
     postgres:5433  redis:6379  minio:9000/9001  mailpit:8025  clamav
     (optionally api, worker, beat in containers instead of on the host)
```
- **One-command setup:** `pnpm setup` (planned) → install JS dependencies, `uv sync`, copy `.env.example`, start compose, migrate, seed synthetic data.
- **Seed data:** synthetic only (Faker plus hand-made clinical fixtures): a demo doctor, a patient, a child dependant with a guardian, an elderly dependant with two caregivers, and an admin.
- **Stub adapters:** SMS and Web Push go to console or Mailpit; AI can run against recorded fixtures (`HIO_AI_MODE=fixtures`) so development and tests need no API keys.
- **Quality gates** run locally through pre-commit and in CI: ruff, mypy strict, pytest, eslint, tsc, vitest, gitleaks, and the OpenAPI client drift check.
- Host Postgres port **5433**, to avoid clashing with local PostgreSQL installs.

## 18. Production environment

**Stage 1 (launch; Docker Compose on managed infrastructure in an Indian region):**
```
Internet ─► DNS + CDN/WAF ─► TLS reverse proxy (nginx/Caddy)
                              ├─► web (nginx, static SPA)
                              └─► api ×2+  ─┬─► Managed PostgreSQL (encrypted, PITR, private)
                                            ├─► Managed Redis (private)
             worker-* ×N, beat ×1 ──────────┼─► S3-compatible storage (SSE-KMS, versioned)
                                            └─► ClamAV (internal)
Observability: OpenTelemetry → traces/metrics; logs → central store (redacted); Sentry (PHI scrubbing)
Backups: DB PITR + daily snapshot to a separate account/region within India; restore drill monthly
```
- Hosts sit in a private network. Only the proxy is public. SSH goes through a bastion or SSM with MFA.
- Secrets come from a secret manager and are delivered as Docker secrets or files, never environment variables baked into images.
- Deployments: CI builds signed images → staging (auto) → production (manual approval), with migrations as a separate pre-deploy step. Rollback means deploying the previous image tag (migrations are backwards-compatible because of expand/contract).
- **Stage 2 (scale):** the same images move to an orchestrator (ECS or Kubernetes) with autoscaling on queue depth for workers. No application changes are needed because the app is stateless and fully configured through the environment.

Environments: `local` → `ci` → `staging` (synthetic data only) → `production`. Real patient data exists **only** in production.

## 19. Frontend folder structure

```
apps/web/
├── index.html
├── vite.config.ts                 # React plugin, PWA plugin, /api proxy in dev
├── tailwind.css / src/styles/     # Tailwind 4 entry + design tokens
├── public/                        # icons, manifest assets
└── src/
    ├── main.tsx                   # bootstrap
    ├── app/
    │   ├── App.tsx                # providers
    │   ├── router.tsx             # route tree; lazy portal routes
    │   ├── providers/             # QueryProvider, AuthProvider, ThemeProvider, I18nProvider
    │   └── ErrorBoundary.tsx
    ├── routes/
    │   ├── public/                # landing, login, OTP, signup, invitation accept, emergency view (token)
    │   ├── patient/               # dashboard, medications, records, labs, appointments, assistant, privacy
    │   ├── caregiver/             # dependants switcher, dependant dashboard, alerts
    │   ├── doctor/                # dashboard, patients, patient chart, visit, prescribe, follow-ups
    │   └── admin/                 # verification queue, users, catalogue, audit, flags, data requests
    ├── features/                  # domain slices shared across portals
    │   ├── auth/  consent/  patients/  caregivers/  encounters/  notes/
    │   ├── prescriptions/  ocr-review/  medications/  reminders/  records/
    │   ├── labs/  appointments/  emergency/  ai-assistant/  safety-alerts/
    │   ├── notifications/  adherence/  timeline/  audit/
    │   └── <feature>/
    │       ├── api.ts             # TanStack Query hooks built on the generated client
    │       ├── components/
    │       ├── schemas.ts         # Zod form schemas
    │       └── index.ts           # public exports of the feature
    ├── components/
    │   ├── ui/                    # design-system primitives
    │   ├── layout/                # PortalShell, Sidebar, TopBar, RoleSwitcher
    │   └── clinical/              # ConfidenceBadge, AiDraftBanner, DoseTimeline, AllergyChip
    ├── lib/                       # api client, auth tokens, permissions, dates/tz, formatters, i18n
    ├── locales/                   # en/, hi/
    └── test/                      # test utils, MSW handlers
tests/e2e/                         # Playwright specs per portal
```
Import rules (enforced by ESLint boundaries): `routes → features → components/lib`. A feature may not import another feature's internals, only its `index.ts`.

## 20. Backend folder structure

```
apps/api/
├── pyproject.toml · uv.lock · alembic.ini · .env.example
├── alembic/versions/
├── app/
│   ├── main.py                    # app factory, middleware, router mounting  (exists)
│   ├── core/                      # cross-cutting (no imports from modules/)
│   │   ├── config.py  logging.py  errors.py  middleware.py        (exist)
│   │   ├── db.py  cache.py  storage.py  rate_limit.py  policies.py (exist)
│   │   ├── security.py            # hashing, JWT, OTP, TOTP
│   │   ├── crypto.py              # envelope encryption, EncryptedString, blind index
│   │   ├── outbox.py  events.py   # transactional outbox, event bus
│   │   ├── audit.py               # audit writer (used by all modules)
│   │   ├── uow.py  pagination.py  ids.py (UUIDv7)  time.py
│   ├── modules/
│   │   ├── system/                # health, ready (exists)
│   │   ├── identity/  access/  consent/  patients/  care_team/  caregivers/
│   │   ├── clinical/  prescriptions/  medications/  reminders/  records/  labs/
│   │   ├── appointments/  emergency/  drugs/  safety/  notifications/
│   │   ├── audit/  analytics/  admin/
│   │   └── <module>/  router.py schemas.py models.py repo.py service.py policies.py events.py tasks.py
│   ├── ai/
│   │   ├── gateway.py             # provider-agnostic interface, budgets, retries
│   │   ├── providers/             # claude.py, tesseract.py, cloud_vision.py, fixtures.py
│   │   ├── pipelines/             # prescription_ocr.py, lab_extract.py, summarize.py, explain.py, chat.py
│   │   ├── guards/                # input_guard.py, output_validator.py, red_flags.py
│   │   ├── retrieval/             # kb ingestion, chunking, embeddings, search
│   │   ├── prompts/               # versioned prompt files (*.md + metadata)
│   │   └── schemas.py             # extraction JSON schemas
│   └── workers/
│       ├── celery_app.py  tasks.py  (exist)
│       └── queues.py  beat_schedule.py
├── scripts/                       # export_openapi.py (exists), seed_dev.py, seed_drugs.py, kb_ingest.py
└── tests/
    ├── unit/  integration/  policy/  contract/
    └── evals/                     # AI gold datasets + thresholds
```

## 21. Entity relationship overview

```
users ─┬─< user_roles >── roles ──< role_permissions
       ├─< sessions, mfa_factors, devices(push subscriptions)
       ├─1 doctor_profiles ──< doctor_clinic >── clinics
       └─0..1 patients (self) ─────────────────────────────────────────────┐
                                                                            │
patients (health subject; user_id NULL for dependants without a login)     │
  ├─< care_relationships >── doctor_profiles           (doctor ↔ patient)  │
  ├─< caregiver_links >── users(caregiver)  [scopes, is_guardian, expiry]  │
  ├─< consents ──< consent_events        (grantor, grantee, purpose, cats) │
  ├─1 emergency_profiles ──< emergency_contacts;  ──< emergency_access_tokens
  ├─< conditions, allergies, vitals                                        │
  ├─< encounters (visits) ──< clinical_notes (versioned, signed)           │
  │        ├─< prescriptions ──< prescription_items >── drugs/drug_products│
  │        ├─< lab_orders ──< lab_reports ──< lab_results                  │
  │        └─< follow_ups                                                  │
  ├─< prescriptions (source: doctor | uploaded | manual)                   │
  │        └─< prescription_versions                                       │
  ├─< medications (regimen; from prescription_item or manual)              │
  │        ├─< medication_schedules ──< dose_events (taken/missed/…)       │
  │        └─1 inventory                                                   │
  ├─< medical_records ──< files                                            │
  ├─< appointments >── doctor_profiles                                     │
  ├─< ai_jobs ──< extractions (proposed → verified/rejected)               │
  ├─< ai_conversations ──< ai_messages                                     │
  ├─< safety_alerts (on medication/prescription) ──< overrides            │
  ├─< notifications ──< deliveries                                         │
  ├─< sos_events                                                           │
  └─< adherence_daily                                                      │
audit_events (patient_id, actor_user_id, …; append-only, hash-chained) ◄───┘
drugs ──< drug_products (brands) ; drugs >──< interactions >──< drugs ; drugs >── allergy_classes
kb_documents ──< kb_chunks (vector)        ai_invocations (every model call)
outbox_events · failed_jobs · feature_flags · privacy_notices · dpdp_requests
```

**Modelling decisions**
- `users` (someone who can log in) is separate from `patients` (someone whose health is recorded). A child or an elderly dependant is a `patients` row with no `user_id`, managed through `caregiver_links(is_guardian=true)`. One user can be a patient, a caregiver for several dependants, and a doctor at the same time.
- `medications` is the patient's **actual** regimen. It links back to the `prescription_item` it came from (or `source=manual/ocr`). Changes to a doctor-issued regimen go through the doctor, or are recorded as the patient's own "reported change" without altering the prescription.
- An `encounter` (visit) is the anchor for notes, orders and prescriptions created during a consultation. Uploaded documents that are not linked to a visit sit directly under the patient.

## 22. Complete user journey

```
1. Doctor signs up → submits registration details → admin verifies → doctor is active (MFA on).
2. Patient signs up with mobile OTP (or is invited by a doctor or caregiver) → accepts privacy notice
   → chooses optional consents (reminders, AI) → completes profile, allergies, conditions, emergency info.
3. Patient links a doctor (search / QR at clinic / doctor invite) → consent screen with data categories
   → care relationship becomes active.
4. Visit: doctor opens the chart (pre-visit AI summary, marked as a draft) → records vitals and notes
   → orders tests → writes an e-prescription → the safety engine checks interactions, allergies and duplicates
   → doctor resolves or overrides with a reason → signs and issues (step-up MFA) → sets a follow-up.
5. Patient gets a notification → reviews the prescription → the regimen is created → confirms the schedule
   (times fit their routine) → reminders start.
6. Outside prescriptions: the patient uploads a paper prescription photo → OCR + AI extraction →
   the review screen highlights uncertain fields → the patient (or caregiver) verifies or corrects
   → safety check → regimen created. Nothing is activated without confirmation.
7. Daily: reminders → taken/skipped/snoozed → missed doses escalate to caregivers → refill alerts.
8. Tests: the patient uploads lab reports → extraction → verification → trends; the doctor is notified of abnormal flags.
9. Follow-up: an appointment is booked from the follow-up task → the doctor sees adherence, labs and changes since the last visit.
10. Emergency: SOS → caregivers and contacts alerted with location; responders scan the emergency QR
    → limited emergency data, access logged.
11. Throughout: the patient sees who accessed their data, manages consents, exports or corrects data.
```

## 23. Doctor journey
1. **Onboard:** sign up → verify email → set up TOTP → submit council registration number, specialty and documents → wait for admin verification (the account is limited meanwhile).
2. **Set up practice:** clinic details, availability slots, prescription template (letterhead, registration number), default follow-up intervals.
3. **Find or add patients:** search linked patients; add a new patient by phone or ABHA address (the invite creates a pending relationship that the patient consents to); scan a patient's share QR.
4. **Before the visit:** the dashboard shows today's appointments, abnormal lab flags, poor adherence and new patient uploads. The AI pre-visit summary is labelled "AI draft", with citations to records.
5. **During the visit:** open or create an encounter → vitals → clinical notes (AI can draft from bullet points; the doctor edits and signs) → problem list and allergies → order tests.
6. **Prescribe:** drug search (generic or brand) → dose, frequency, duration, food relation and instructions → the safety panel shows interactions, allergy conflicts, duplicates and dose limits → resolve, or override with a reason → preview → **step-up MFA** → issue (immutable, versioned, PDF).
7. **After the visit:** set a follow-up; the patient is notified; the doctor watches adherence and responds to escalations.
8. **Review uploads:** see patient-uploaded external prescriptions and reports (clearly marked "patient-verified", not "doctor-verified"); optionally confirm them into the record.
9. **Emergency:** break-glass access to the emergency dataset of an unlinked patient, with a reason, time limit and patient notification.

## 24. Patient journey
1. **Sign up** with mobile OTP → accept the privacy notice (English or Hindi) → choose optional consents (reminders, AI, SMS).
2. **Build a profile:** demographics, blood group, allergies, conditions, current medicines (manual entry or photo), emergency contacts, timezone and daily routine (wake, meal and sleep times drive schedule suggestions).
3. **Connect:** link doctors (with the categories they may see) and invite caregivers (choosing scopes and expiry).
4. **Medicines:** "Today" view of doses → mark taken or skipped → snooze → refill tracking. Doctor-issued medicines cannot be edited by the patient; the patient can report a change or stop ("I stopped this"), which is recorded and flagged to the doctor. **The platform never advises stopping a medicine.**
5. **Prescriptions:** view and download doctor prescriptions; upload paper ones → verify the AI extraction field by field.
6. **Records and tests:** upload documents and lab reports → verify extracted values → see the timeline and lab trends.
7. **Appointments:** book, reschedule, and get reminders; complete follow-ups.
8. **AI assistant:** ask about *their own documented* medicines and records ("What is this medicine for, according to my prescription?"). Answers cite sources, show a disclaimer, and escalate red-flag symptoms to emergency guidance.
9. **Emergency:** SOS button; manage the emergency profile and QR (show or revoke).
10. **Privacy centre:** consents, the "who accessed my data" log, data export, correction and erasure requests, and a nominee.

## 25. Caregiver journey
1. **Invitation:** a patient invites the caregiver (or, for a new dependant, the caregiver creates the dependant profile as a guardian after a declaration and, for minors, verifiable parental consent).
2. **Accept:** the caregiver signs up or logs in → sees the granted scopes and expiry → accepts.
3. **Switch dependants:** the header switcher chooses which patient they are acting for. The UI always shows "Acting for: Name", and every action is audited as "on behalf of".
4. **Daily care (within scopes):** today's doses for each dependant, log doses, refill alerts, missed-dose alerts, upload records or prescription photos, verify OCR (if they hold `manage_medications`), manage appointments.
5. **Alerts:** missed-dose escalations, SOS alerts with location, abnormal lab flags (only with `view_labs` and `receive_alerts`).
6. **Guardian-only:** manage consents and other caregivers for a dependant; perform the handover when a minor turns 18.
7. **Lifecycle:** access ends automatically at expiry, or immediately when the patient or guardian revokes it. The caregiver sees clearly when access has ended.

## 26. Admin journey
1. **Sign in** with MFA; step-up for sensitive actions. Admins do **not** see clinical data by default.
2. **Doctor verification queue:** review registration details and documents → approve, reject or ask for more information → the decision is audited and the doctor notified.
3. **User management:** search accounts by metadata → suspend, unlock, force logout, reset MFA (with an identity-verification checklist); no clinical view.
4. **Catalogue management:** the drug catalogue, interaction data imports (versioned and reviewed before activation), knowledge-base documents (review, approve, retire), lab reference ranges.
5. **Platform operations:** feature flags, failed-job replay, notification provider status, AI usage and cost dashboard, eval results.
6. **Compliance:** audit search (itself audited), DPDP data-rights queue (access, correction, erasure, grievance) with SLA timers, break-glass review queue, breach-response runbook trigger.
7. **Reports:** aggregate, de-identified platform metrics only (active users, adherence distribution, verification throughput).
