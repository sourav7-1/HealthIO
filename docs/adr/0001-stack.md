# ADR 0001: Technology stack

- **Status:** accepted; web and mobile rows superseded by [ADR 0002](0002-frontend-vite-spa.md)
- **Date:** 2026-09-24

## Context
Health Io serves four roles (doctor, patient, caregiver, admin) and depends on AI (prescription OCR, assistants), background scheduling (dose reminders) and Indian health-data rules (DPDP Act 2023, ABDM).

## Decision
| Area | Choice | Why |
|---|---|---|
| API | Python 3.12, FastAPI, Pydantic v2 | Python has the strongest OCR, imaging and AI tooling; FastAPI gives typed contracts and OpenAPI for free |
| Database | PostgreSQL 16, SQLAlchemy 2 (async), Alembic | Relational clinical data, strong constraints (for example, preventing double booking with exclusion constraints), JSONB for AI output |
| Background jobs | Celery + Redis, Celery beat | Reminders, OCR and notifications need retries, scheduling and acks-late delivery |
| Files | S3 (MinIO locally), AWS `ap-south-1` in production | Keeps data in India; presigned URLs keep file bytes off the API |
| Web | Next.js 16 (App Router), Tailwind 4 | One app with route groups per portal |
| Mobile | Expo (React Native) | Patients and caregivers need dependable push and local notifications for reminders, plus the camera for OCR |
| Contracts | OpenAPI → `openapi-typescript` + `openapi-fetch` | One source of truth for API types |
| OCR | OpenCV preprocessing → Tesseract (`eng+hin`) or Google Cloud Vision → Claude structuring | Dedicated OCR provides a text layer and fallback; Claude reads the image and OCR text together to produce structured, confidence-scored fields |
| LLM | Claude via `app/ai/provider.py` | Sonnet 5 for structuring and assistants, Haiku 4.5 for cheap classification; the interface allows swapping |
| Tooling | uv (Python), pnpm + Turborepo (JS) | Fast, reproducible installs, with lockfiles for both |

TypeScript is pinned to 5.9 for now. The native TypeScript 7 compiler will be adopted once Next.js and the ESLint tooling support it fully.

## Consequences
- Two languages, so API types cross the boundary only through the generated client (rule 5).
- Celery tasks must be idempotent because of acks-late delivery.
- The same container image serves the API, worker and beat, with a different command for each.
