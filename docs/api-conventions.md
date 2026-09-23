# API conventions

## URLs and methods
- Versioned prefix: `/api/v1`. Probes (`/health`, `/ready`) sit at the root.
- Resource nouns, plural, in kebab-case: `/api/v1/patients/{patient_id}/medications`.
- `GET` reads, `POST` creates or triggers an action (`POST /prescriptions/{id}:issue` is written as `POST /prescriptions/{id}/issue`), `PATCH` makes partial updates, and `DELETE` soft-deletes.
- IDs are UUIDv7 strings.

## Requests and responses
- JSON bodies with `snake_case` fields. Timestamps are ISO 8601 in UTC (`2026-09-24T08:30:00Z`); dates are `YYYY-MM-DD`.
- Responses always use a declared Pydantic schema. ORM objects are never returned directly.
- Every response carries an `x-request-id` header. Clients may send their own (8 to 64 characters of `[A-Za-z0-9-_.]`).

## Pagination
Cursor-based:
```
GET /api/v1/patients?limit=50&cursor=<opaque>
→ { "items": [...], "next_cursor": "<opaque>" | null }
```
`limit` defaults to 20 and is capped at 100. Cursors are opaque strings; clients must not parse them.

## Errors: RFC 9457 problem+json
Every error, including validation failures and crashes, returns `Content-Type: application/problem+json`:
```json
{
  "type": "https://healthio.app/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "instance": "/api/v1/medications",
  "request_id": "5f0c…",
  "errors": [{ "loc": ["body", "dose"], "msg": "Field required", "type": "missing" }]
}
```
- `type` ends in a stable code clients can switch on: `not-found`, `conflict`, `unauthorized`, `forbidden`, `rate-limited`, `validation-error`, `internal-error`, and codes specific to each domain.
- Rejected input values are never echoed back.
- Rate-limited responses (`429`) include `Retry-After`.

## Authentication (Phase 3)
- Web: short-lived access token plus a rotating refresh token in an `httpOnly`, `Secure`, `SameSite=Lax` cookie.
- Mobile: `Authorization: Bearer <access>`; the refresh token is kept in SecureStore.

## Idempotency
Endpoints that create something with side effects (SOS, dose logging, prescription issue) accept an `Idempotency-Key` header. A repeated key within 24 hours returns the original result.
