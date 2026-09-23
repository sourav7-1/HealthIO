# Engineering rules

These rules apply to all code in this repository. A change that breaks one needs an ADR in `docs/adr/`.

## 1. Module boundaries
- Backend domains live in `apps/api/app/modules/<domain>/` with `router.py`, `schemas.py`, `models.py`, `service.py`, `repo.py` and `policies.py`.
- Calls go router → service → repo. Routers parse input, call one service method and return a schema. They hold no business logic.
- A module may import another module's **service** only, never its repo or models.
- `app/core/` holds cross-cutting code only (config, db, logging, errors, security, storage). It never imports from `modules/`.

## 2. Every endpoint declares a policy
- Access is denied by default. Each route has exactly one policy dependency from `app/core/policies.py`: `public` for probes and truly public endpoints, and `require(...)` (added in Phase 3) for everything else.
- `tests/test_route_policies.py` fails the build when a route has no policy or more than one.
- Patient-scoped routes check the **relationship** as well as the role: a doctor needs an active care relationship, and a caregiver needs a link with the right scope.

## 3. Health and personal data (PHI/PII)
- Never log PHI or PII. `app/core/logging.py` redacts sensitive keys and identifier patterns, but it is a safety net, not permission to log payloads.
- Log IDs (UUIDs), never names, phone numbers, ABHA numbers or clinical text.
- Error responses never echo request input.
- Sensitive columns use envelope encryption (`EncryptedString`, Phase 2).
- Every read or write of patient data writes an audit event (Phase 2 onwards).
- Files go to object storage through presigned URLs with server-side encryption. Never store them on local disk.
- Use synthetic data only in development, tests, fixtures and screenshots.

## 4. AI is advisory
- AI output (OCR, extraction, assistant suggestions, safety explanations) becomes clinical data only after a person confirms it.
- Medication safety decisions come from deterministic rules. The model only explains them.
- Patient-facing AI shows a disclaimer, never diagnoses, never changes a dose, and sends red-flag symptoms to emergency guidance.
- All model calls go through `app/ai/provider.py`. Prompts are versioned files under `app/ai/prompts/`.
- Every AI feature ships with an eval set in `apps/api/tests/evals/`.

## 5. Contracts first
- Pydantic schemas define the API. The OpenAPI document is exported and `packages/api-client` is generated from it (`pnpm api:client`).
- Do not hand-write API types in the web or mobile apps. CI fails if the generated client is out of date.

## 6. Data changes
- Change the schema only through Alembic migrations, and every migration has a working `downgrade`.
- Clinical records are soft-deleted (`deleted_at`) and never hard-deleted. DPDP erasure is a separate, audited workflow.
- Primary keys are UUIDv7.

## 7. Time
- Store timestamps in UTC (`timestamptz`). Reminders and "today" views use the user's IANA timezone (for example `Asia/Kolkata`).

## 8. Quality gates
- Python: `ruff format`, `ruff check`, `mypy --strict`, `pytest`. Services keep coverage at 80% or higher.
- TypeScript: `eslint`, `tsc --noEmit` (strict), `vitest`, and Playwright for E2E tests.
- Commits follow Conventional Commits (`feat(api): …`, `fix(web): …`).
- CI must be green before merging. Hooks are never skipped.

## 9. Security defaults
- Validate all input with Pydantic or Zod. Build SQL only with SQLAlchemy expressions, never string formatting.
- Secrets come from the environment or a secret manager and are never committed. Gitleaks runs in pre-commit and CI.
- API responses send `Cache-Control: no-store` and strict security headers.
- Rate-limit authentication, OTP and AI routes.
