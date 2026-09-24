# Authentication and authorization

How users sign in, how tokens work, and how access to patient data is decided. Code lives in `apps/api/app/modules/identity/` (authentication) and `apps/api/app/modules/access/` (authorization).

Related: [SECURITY_MODEL.md](../SECURITY_MODEL.md) §4–5 · [ARCHITECTURE.md](../ARCHITECTURE.md) §6–7 · [data-model.md](data-model.md)

## 1. Endpoints

| Method and path | Policy | Purpose |
|---|---|---|
| `POST /api/v1/auth/register` | public, 20/min/IP | Create a patient, caregiver or doctor account. Always `202` with the same body |
| `POST /api/v1/auth/email/verify` | public | Confirm the email with the emailed token |
| `POST /api/v1/auth/email/verification-request` | authenticated (unverified allowed) | Send a new verification link |
| `POST /api/v1/auth/login` | public, 10/min/IP | Email + password → access token (body) + refresh cookie |
| `POST /api/v1/auth/refresh` | public + CSRF header, 30/min | Rotate the refresh token; new access token |
| `POST /api/v1/auth/logout` | authenticated | Revoke this session |
| `POST /api/v1/auth/logout-all` | authenticated | Revoke every session |
| `GET /api/v1/auth/sessions` · `DELETE …/{id}` | authenticated | List or revoke signed-in devices |
| `POST /api/v1/auth/password/reset-request` | public | Always `202`; emails a link only if the account exists |
| `POST /api/v1/auth/password/reset` | public | Set a new password with the emailed token; signs out everywhere |
| `GET /api/v1/me` | authenticated (unverified allowed) | Account, roles, platform permissions, profile IDs |
| `GET /api/v1/patients/{id}/access` | any relationship with the patient | The caller's effective permissions for that patient |
| `GET/POST /api/v1/patients/{id}/caregivers` | `manage_caregivers` | List or invite caregivers (with scopes) |
| `PUT …/caregivers/{rel}/scopes` · `DELETE …/caregivers/{rel}` | `manage_caregivers` | Change scopes or revoke |
| `POST /api/v1/caregiver-invitations/{rel}/accept` · `…/decline` | authenticated (the invited user) | Accept (grants the caregiver role) or decline |
| `POST /api/v1/me/dependants` | authenticated | Create a dependant profile (child or represented adult); the caller becomes its guardian ([phase 6](phases/06-caregivers.md)) |
| `GET /api/v1/me/caregiving` · `…/dashboard` · `POST …/{rel}/leave` | authenticated | People I care for, the caregiver dashboard, stop caring |
| `GET /api/v1/patients/{id}/caregivers/activity` | `manage_caregivers` | What caregivers opened or did, from the audit log |
| `GET /api/v1/me/caregiving` | authenticated | Patients I care for, and pending invitations |
| `GET /api/v1/doctor/patients` | `list_own_patients` (doctor role) | Only patients linked to this verified doctor |
| `POST /api/v1/admin/doctors/{id}/verify` | role `admin` | Verify a doctor's registration |

Admins cannot self-register. Create one with `uv run python -m scripts.create_admin --email … --name …`.

## 2. Accounts and passwords

- **Passwords:** Argon2id (64 MiB, t=3). Hashes are upgraded automatically on login when the cost changes. Policy: 10–128 characters; rejects very common passwords, passwords with fewer than 4 distinct characters, and passwords containing the email name. Password fields are `SecretStr`, so they never appear in reprs, logs or validation errors.
- **Email and phone** are stored encrypted, with HMAC blind indexes for lookup (see data-model.md §7).
- **Account status:** `pending_verification` (can sign in, but every policy except `/me` and auth routes answers `403 email-unverified`), `active`, and `locked` / `suspended` / `closed` (cannot sign in; revealed only after a correct password).
- **Registration never reveals** whether an email is taken: the response is identical, and the existing owner receives a "someone tried to register" email instead.

## 3. Tokens and invalidation

| Token | Form | Lifetime | Storage |
|---|---|---|---|
| Access | JWT, EdDSA (Ed25519), `kid` header; claims `sub`, `sid`, `jti`, `typ`, `iss`, `aud`, `iat`, `nbf`, `exp`. No roles and no personal data | 10 min | Client memory; `Authorization: Bearer` |
| Refresh | 256-bit opaque, single use | 14 days sliding, 30 days absolute cap | `httpOnly; SameSite=Strict; Path=/api/v1/auth` cookie (`Secure` outside local/test). Server stores only its SHA-256 |
| Email verification / password reset | 256-bit opaque, single use | 24 h / 30 min | Emailed in the link's **URL fragment** (`#token=`), so it never reaches server logs or `Referer`. Server stores only the hash |

**Invalidation strategy.** Every access token names its session (`sid`). On each request the API checks that the session is live and the account can sign in, and it reloads roles. So logout, logout-all, password reset, suspension and role changes take effect on the **next request**, not when the token expires.

**Refresh rotation and reuse detection.** Each refresh marks the presented token used and issues a new one (under a row lock). Presenting a used token means it was copied: the whole session is revoked (`refresh_reuse`), which also kills the access tokens that session issued.

**CSRF.** The refresh endpoint is the only one that uses a cookie. It also needs `X-Requested-With: healthio`, and rejects any `Origin` outside the configured web origins.

## 4. Brute-force and abuse protection

- Per-IP rate limits (Redis): login 10/min, refresh 30/min, other auth forms 20/min.
- Per-account lockout: 5 consecutive failures lock the account for 15 minutes. During the lock even the right password gets the same generic `401`.
- The same Argon2 work is done for unknown accounts (no timing oracle), and one error shape covers every failure (no enumeration).
- Auth emails are capped at 5 per address per hour.
- Every attempt is audited with a reason (`unknown_account`, `bad_password`, `locked`, `status_*`).

## 5. Authorization model

A caller's permissions **for one patient** are the union of their relationships with that patient:

| Relationship | Condition | Permissions |
|---|---|---|
| **Self** | Caller holds the `patient` role and owns the patient profile | Every caregiver-grantable permission, plus `edit_profile` and `manage_consent`. Not the clinical writes |
| **Doctor** | `doctor` role, profile **verified** by an admin, relationship **active** | Doctor permissions, **narrowed to the data categories** in the patient's active `care_delivery` consents |
| **Caregiver** | `caregiver` role, relationship **active** and **unexpired** | Exactly the granted scopes; `manage_caregivers` only for guardians; never the clinical writes |
| **Admin** | — | Nothing. The admin role grants platform permissions only |

Doctor permission → consent category: `view_profile`→demographics, `view_medical_history`→conditions, `view_medications`→medications, `view/manage_appointments`→appointments, `view/upload_reports`→tests_and_reports, `edit_clinical_records`→visits_and_notes, `change_doctor_prescription`→prescriptions.

**Caregiver scopes** (grantable): `view_profile`, `view_medical_history`, `view_medications`, `view_prescriptions`, `view_visits`, `view_adherence`, `log_doses`, `manage_reminders`, `report_health_info` (entries labelled as the caregiver's), `view_appointments`, `manage_appointments`, `view_reports`, `upload_reports`, `receive_alerts`, `use_ai_assistant`, `manage_emergency_info`, `manage_caregivers` (guardians only). A guardian of a dependant (a profile with no login) also gets `edit_profile` for that dependant.

**Never for caregivers:** `edit_clinical_records`, `change_doctor_prescription`, `delete_medical_records` (also `edit_profile`, `manage_consent`). This is enforced in three places:
1. The request schema, which accepts only grantable scopes (422).
2. The decision service, which subtracts them even if a grant existed.
3. A database CHECK constraint on `caregiver_permissions.scope`.

`delete_medical_records` is granted to **nobody** through the API. Records are corrected by supersession, and erasure is a separate audited DPDP workflow.

**Existence is not leaked.** No relationship with the patient (or an unknown ID) gives `404`. A relationship without the needed permission gives `403`. Both are audited as `access.denied`; when the patient exists, the event is linked to that patient, so it can appear in their "who tried to access my data" view.

## 6. Using the dependencies

Every route declares exactly one policy; `tests/test_route_policies.py` enforces this for all routes.

```python
from app.modules.access.dependencies import (
    authenticated, require_roles, require_permission,
    require_patient_permission, require_patient_relationship,
)

@router.get("/patients/{patient_id}/medications")
async def list_medications(
    patient_id: uuid.UUID,
    access: PatientAccess = Depends(require_patient_permission(Permission.VIEW_MEDICATIONS)),
    session: AsyncSession = Depends(get_session),
): ...

@router.post("/admin/things")
async def admin_only(principal: Principal = Depends(require_roles(Role.ADMIN))): ...
```

## 7. Audit events

`auth.register`, `auth.login` (allowed/denied with reason), `auth.lockout`, `auth.refresh_reuse_detected`, `auth.logout`, `auth.logout_all`, `auth.session_revoked`, `auth.email_verified`, `auth.password_reset_requested`, `auth.password_reset_completed`, `access.denied`, `caregiver.invited`, `caregiver.accepted`, `caregiver.declined`, `caregiver.scopes_changed`, `caregiver.revoked`, `caregiver.left`, `caregiver.dependant_created`, `caregiver.dashboard_view`, `doctor.verified`, `admin.created`. No passwords, tokens or email addresses are written to audit rows or logs; the tests and the smoke run check this.

## 8. Configuration

All settings use the `HIO_` prefix (`app/core/config.py`): `JWT_SIGNING_KEYS` (`{kid: base64 Ed25519 seed}`), `JWT_ACTIVE_KEY_ID`, `ACCESS_TOKEN_TTL_SECONDS`, `REFRESH_TOKEN_TTL_SECONDS`, `SESSION_ABSOLUTE_TTL_SECONDS`, `ARGON2_*`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`, `LOGIN_RATE_LIMIT_PER_MINUTE`, `MAIL_BACKEND`, `SMTP_HOST`, `SMTP_PORT`, `PUBLIC_WEB_URL`. The app refuses to start outside local/test with the built-in development keys.

**Key rotation:** add a new `kid`, switch `JWT_ACTIVE_KEY_ID`, and remove the old key after one access-token lifetime.

## 9. Not yet implemented

Phone OTP login, TOTP MFA (required for doctors and admins) with step-up for sensitive actions, the breached-password (k-anonymity) check, and invitation of caregivers who do not yet have an account. Inviting a caregiver currently requires an existing account, which tells a signed-in patient whether an email is registered; an emailed invitation flow will remove this. The web screens come with the Vite SPA (Phase 1b).
