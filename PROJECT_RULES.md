# Project rules

These rules are binding for all code, reviews and AI-assisted contributions. Breaking one requires an ADR in `docs/adr/` that explains why. When a rule and a deadline conflict, the rule wins.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY_MODEL.md](SECURITY_MODEL.md) · [AI_SAFETY.md](AI_SAFETY.md)

---

## 1. Product principles
1. **We are not the doctor.** The platform records, organises, reminds and explains documented information. It never diagnoses, prescribes, discontinues or changes a dose on its own (see [AI_SAFETY.md](AI_SAFETY.md)).
2. **Uncertainty is shown, never hidden.** Unverified or low-confidence data is visibly marked and needs confirmation by a person.
3. **Provenance is always shown.** Every clinical fact records where it came from (doctor-issued, patient-entered, AI-extracted and verified by whom) and who confirmed it.
4. **The patient owns their data.** Access follows consent; patients can see who accessed their data and can withdraw access.
5. **Safety beats convenience.** When in doubt, ask for confirmation, and fail closed.

## 2. Architecture rules
1. **Modular monolith.** Backend code lives in `apps/api/app/modules/<module>/`. Each table has one owning module.
2. **Layering inside a module:** `router → service → repo`. Routers parse and return schemas only. Business rules live in services. Only the repo touches the module's tables.
3. **Across modules:** call another module's `service` only; never import its `repo` or `models`, and never query its tables. Use domain events (transactional outbox) for side effects. Enforced by import-linter in CI.
4. **`app/core/` is cross-cutting** and never imports from `modules/`.
5. **No new infrastructure** (queues, databases, services) without an ADR. Prefer PostgreSQL, Redis and Celery.
6. **Frontend layering:** `routes → features → components/lib`. A feature exposes a public `index.ts`; other features import only from it.
7. **Contracts first:** Pydantic schemas → OpenAPI → generated `packages/api-client`. Hand-written API types in the frontend are not allowed. Run `pnpm api:client` after changing the API; CI fails on drift.

## 3. Access control rules
1. **Every route declares exactly one policy** (`public` or `require(...)`). A test fails the build otherwise (`tests/test_route_policies.py`).
2. **Deny by default.** A patient-scoped route checks role permission **and** relationship **and** consent. Having a role alone is never enough.
3. **Look up patient resources through the patient scope** (`WHERE patient_id = :authorised_patient`). Never fetch by ID alone and check afterwards.
4. When revealing a resource's existence would leak information, return **404, not 403**.
5. **Admins get no clinical data by default.** Any exception is a time-boxed, audited, reason-bearing grant.
6. Every caregiver action records `on_behalf_of_patient_id`.

## 4. Data rules
1. **PHI never goes into logs, analytics, error trackers, URLs or query strings.** Log IDs only. The redaction processor in `core/logging.py` is a safety net, not permission.
2. **Every read and write of clinical data writes an audit event** in the same transaction (`core/audit.py`).
3. **Soft delete** for clinical and user-generated data. Physical deletion happens only through the DPDP erasure or retention workflows.
4. **Issued prescriptions, signed notes and verified extractions are immutable.** Changes create new versions with a reason.
5. **Sensitive free text and identifiers** (notes, ABHA/Aadhaar-like numbers, AI conversation content) use `EncryptedString`. Searchable identifiers use blind indexes.
6. **Primary keys are UUIDv7.** Timestamps are `timestamptz` in UTC. Patient-facing times use the patient's IANA timezone.
7. **Schema changes go only through Alembic.** Every migration has a working `downgrade` and follows expand → migrate → contract for breaking changes.
8. **Only synthetic data** outside production: seeds, fixtures, tests, screenshots, demos, bug reports, and prompts to AI coding tools. Never copy production data down.
9. **Files** go to object storage through presigned URLs only, are scanned before use, and never touch the API container's disk.

## 5. AI rules (summary; [AI_SAFETY.md](AI_SAFETY.md) is authoritative)
1. AI output is **proposed** until a person verifies it. Nothing AI-produced changes a medication, prescription or record by itself.
2. Every model call goes through `app/ai/gateway.py` and writes an `ai_invocations` row.
3. **Interaction and allergy warnings come from structured data** in the `safety` engine. The LLM may only explain them.
4. Explanations cite sources (the patient's own records or reviewed knowledge-base documents); with no source, there is no claim.
5. Prompts are versioned files; changing one requires the pipeline's eval suite to pass in CI.
6. Forbidden outputs (diagnosis, prescribing, dose changes, "stop taking") are blocked by the output validator and covered by eval cases.

## 6. API rules
Details: [docs/api-conventions.md](docs/api-conventions.md).
1. `/api/v1` prefix; only additive changes within a version.
2. Errors are RFC 9457 problem+json with stable `type` codes, raised as `AppError` subclasses from services.
3. Never echo rejected input; never return ORM objects.
4. Cursor pagination (`limit` ≤ 100).
5. `Idempotency-Key` on endpoints with side effects (SOS, dose logging, prescription issue, uploads).
6. Optimistic concurrency (`version` / `If-Match`) on records that are edited concurrently.

## 7. Background job rules
1. **Every task is idempotent** (tasks are delivered at least once, with `acks_late`). Key the work on a natural key.
2. Tasks receive **IDs, not payloads containing PHI**, and load data inside the task.
3. Retryable errors retry with backoff and jitter; permanent errors go to `failed_jobs` and raise an alert. Nothing fails silently.
4. Reminder tasks go on the `reminders` queue only; never put slow work there.

## 8. Code quality
| | Python (`apps/api`) | TypeScript (`apps/web`, `packages/*`) |
|---|---|---|
| Format | `ruff format` | Prettier |
| Lint | `ruff check` (rules in `pyproject.toml`) | ESLint (with import-boundary rules) |
| Types | `mypy --strict` | `tsc --noEmit`, `strict`, `noUncheckedIndexedAccess` |
| Unit tests | pytest | Vitest + Testing Library + MSW |
| E2E | — | Playwright |
| Coverage | ≥ 80% on `service.py` and `policies.py` | ≥ 70% on `features/` |

- No `Any` or `# type: ignore` without a comment explaining why. No `eslint-disable` without a reason.
- Name things after domain language (`dose_event`, `care_relationship`), not technical filler (`data`, `info`, `manager`).
- Comments explain *why*, not *what*.
- Dependencies: add only with a clear need; prefer well-maintained libraries; lockfiles are committed.

## 9. Testing rules
1. Every service method has unit tests, including authorisation denials.
2. **Policy matrix tests:** for each patient-scoped route, cover self, linked doctor, unlinked doctor, caregiver with and without the scope, expired caregiver, admin and anonymous.
3. Integration tests run against real Postgres, Redis and MinIO (compose), marked `integration`.
4. Timezone and DST cases are required for anything to do with scheduling.
5. AI pipelines have eval suites with thresholds (see AI_SAFETY.md §9).
6. A bug fix comes with a regression test.

## 10. Git and review
1. Branch from `main`: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
2. **Conventional Commits:** `feat(medications): add PRN schedules`.
3. A PR must have green CI, one approving review, and a description with a **"Safety & privacy impact"** section (new PHI flows, new AI behaviour, new permissions; "none" is a valid answer).
4. Changes to `access`, `consent`, `audit`, `ai/guards`, `safety` or `core/crypto.py` need a second reviewer.
5. Never skip hooks, never force-push to `main`, and never commit secrets (gitleaks runs in pre-commit and CI).

## 11. Definition of done (per feature)
- [ ] API schema, generated client and frontend updated together
- [ ] Policy declared; policy-matrix tests added
- [ ] Audit events written for clinical reads and writes
- [ ] No PHI in logs (checked in review)
- [ ] Unit, integration and E2E tests pass; coverage targets met
- [ ] Accessibility: keyboard, labels, contrast (axe clean)
- [ ] English and Hindi strings added
- [ ] Docs updated (ARCHITECTURE, DPDP register if new data or purpose, threat model if a new trust boundary)
