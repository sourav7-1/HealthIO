# Health Io

A care platform that connects doctors, patients, caregivers and admins around prescriptions, medicines, reminders, records and AI assistance. Built for India (DPDP Act 2023, ABDM).

## Layout
```
apps/api        FastAPI backend, Celery workers        (Python 3.12, uv)
apps/web        Next.js 16 web app, all four portals   (pnpm)
apps/mobile     Expo app for patients and caregivers   (Phase 5)
packages/       generated API client, design tokens, shared config
infra/          Dockerfiles; Terraform comes later
docs/           rules, ADRs, API conventions, threat model, DPDP register, phase notes
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
pnpm install && pnpm dev               # http://localhost:3000
```
API docs: http://localhost:8000/docs · MinIO console: http://localhost:9001 · Mailpit: http://localhost:8025

## Checks
```bash
cd apps/api && uv run ruff check . && uv run mypy app tests scripts && uv run pytest
pnpm turbo run lint typecheck test build
pnpm api:client                         # regenerate the TS client after API changes
```

Read [docs/RULES.md](docs/RULES.md) before contributing.
