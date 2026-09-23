# Health Io

**AI-Powered Personal Health Record & Medication Management Platform.** It connects doctors, patients, caregivers and admins around visits, records, tests, prescriptions, medicines, reminders, adherence and follow-ups, with AI that is advisory only. Built for India (DPDP Act 2023, ABDM).

## Start here
| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, modules, data model, AI, security, deployment, user journeys |
| [PROJECT_RULES.md](PROJECT_RULES.md) | Binding engineering rules and the definition of done |
| [SECURITY_MODEL.md](SECURITY_MODEL.md) | Data classification, authentication and authorisation, cryptography, DPDP controls, incident response |
| [AI_SAFETY.md](AI_SAFETY.md) | What AI may and must not do, verification, guardrails, eval gates |
| [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md) | 25 phases with exit criteria, and current status |
| [docs/](docs/) | ADRs, API conventions, threat model, DPDP register, phase notes |

## Layout
```
apps/api        FastAPI modular monolith + Celery workers   (Python 3.12, uv)
apps/web        React + Vite SPA: doctor portal built; other portals to come
packages/       generated API client, design tokens, shared TS config
infra/          Dockerfiles (compose split and production config come in Phases 1b and 24)
docs/           ADRs, API conventions, threat model, DPDP register, phase notes
```

## Prerequisites
Python 3.12 (uv installs it for you), [uv](https://docs.astral.sh/uv/), Node 24 with corepack (`corepack enable`), and Docker Desktop.

## Quick start
```bash
# Services + API + worker + beat
docker compose -f docker-compose.dev.yml up -d --build
curl http://localhost:8000/ready        # {"status":"ok","checks":{...}}

# API on the host with hot reload (after starting the services above)
cd apps/api && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload

# Web
pnpm install && pnpm --filter @health-io/web dev   # http://localhost:5173 (proxies /api to :8000)
```
API docs: http://localhost:8000/docs · MinIO console: http://localhost:9001 · Mailpit: http://localhost:8025 · Postgres on host port 5433.

## Checks
```bash
cd apps/api && uv run ruff check . && uv run mypy app tests scripts && uv run pytest
HIO_INTEGRATION=1 uv run pytest -m integration     # needs the compose services
pnpm turbo run lint typecheck test build
pnpm api:client                                    # regenerate the TS client after API changes
```
