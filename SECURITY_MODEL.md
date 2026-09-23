# Security model

This document defines how the platform protects health data. It covers data classification, identity, access control, cryptography, application and infrastructure security, monitoring, incident response, and privacy compliance (India DPDP Act 2023; ABDM when integrated).

Related: [ARCHITECTURE.md](ARCHITECTURE.md) §6–12 · [docs/threat-model.md](docs/threat-model.md) (STRIDE table) · [docs/dpdp-register.md](docs/dpdp-register.md) · [AI_SAFETY.md](AI_SAFETY.md)

> This is an engineering security model, not legal advice. A legal and compliance review is required before launch (roadmap Phase 25).

---

## 1. Security objectives
1. **Confidentiality:** a user sees only data they are entitled to by role, relationship and consent.
2. **Integrity:** clinical data (prescriptions, notes, verified extractions) cannot be changed without a versioned, attributed record. AI can never silently change clinical data.
3. **Availability:** reminders and emergency features keep working through partial failures (for example, the AI provider being down must not affect reminders).
4. **Accountability:** every access to and change of patient data can be traced to a person, role and reason.
5. **Privacy by design:** collect the minimum, process for a stated purpose, keep only as long as needed.

## 2. Data classification
| Class | Examples | Controls |
|---|---|---|
| **C4: Highly sensitive health data** | Clinical notes, diagnoses and conditions, prescriptions, lab results, mental or sexual health content, AI conversations, uploaded documents | Relationship + consent + audit on read; envelope encryption for free text; never in external notifications; never sent to AI without a purpose-bound consent |
| **C3: Identifiers** | Name, phone, email, DOB, address, ABHA number or address, Aadhaar-like numbers, location during SOS | Encrypted or blind-indexed where searchable; masked in UI lists; redacted from logs |
| **C2: Account and security data** | Password hashes, TOTP secrets, refresh-token hashes, sessions | Never returned by the API; TOTP secrets envelope-encrypted; hashes only for tokens |
| **C1: Operational** | IDs, timestamps, metrics, feature flags | Standard controls |
| **C0: Public** | Drug catalogue, knowledge-base content, privacy notices | Integrity controls (reviewed imports) |

Aggregated analytics and exports for anyone other than the patient must be de-identified (no C3; C4 only aggregated with small-cell suppression, k ≥ 10).

## 3. Threat model summary
Main threats (full STRIDE table in [docs/threat-model.md](docs/threat-model.md)):
- **Broken object-level authorisation (IDOR)** across patients: the top risk for any health API.
- Account takeover (OTP brute force, SIM swap, credential stuffing, token theft).
- **Fake or unverified doctors** issuing prescriptions.
- **Caregiver overreach** or stale access (for example, after a relationship ends).
- **Insider misuse** (admins, support staff, curious clinicians).
- PHI leakage through logs, notifications, error messages, analytics, AI providers, or file metadata (EXIF GPS).
- **Prompt injection** through uploaded documents or chat, aimed at leaking data or producing unsafe advice.
- Malicious uploads (malware, polyglots, decompression bombs).
- **Integrity attacks on clinical data** (forged prescriptions, altered doses, tampered audit history).
- Denial of service and cost exhaustion (OTP pumping or SMS fraud, AI token abuse).
- Supply-chain compromise (dependencies, images, CI).

## 4. Identity and authentication
See ARCHITECTURE §6 for the flows. Security requirements:
- **Passwords:** Argon2id (memory ≥ 64 MiB, time cost ≥ 3); minimum 10 characters; breached-password check (k-anonymity); no composition rules; no forced periodic rotation.
- **MFA:** TOTP is **required** for doctors and admins, and optional for patients and caregivers (encouraged for guardians). WebAuthn/passkeys are planned. Recovery codes are shown once, stored hashed, and single-use.
- **OTP:** 6 digits, 5-minute TTL, stored hashed, 5 attempts, then cooldown. Rate-limited per phone number, per IP and per device fingerprint. Daily SMS caps per number and per country prefix protect against SMS pumping fraud.
- **Tokens:** EdDSA-signed access JWT (10 minutes, `kid` header, keys rotated every 90 days with overlap). Opaque refresh token (hashed at rest), rotated on every use, **reuse detection revokes the session family**.
- **Browser storage:** access token in memory; refresh token in an `httpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie. The refresh endpoint also needs a custom header (`X-Requested-With`) and an `Origin` check (CSRF defence).
- **Step-up:** prescription issue, break-glass, data export, caregiver grants, MFA or password change, and every admin write need MFA within the last 5 minutes.
- **Sessions:** visible and revocable by the user. Idle timeout: 30 minutes for doctors and admins, 7 days for patients (refresh-based). Password or MFA changes revoke all other sessions.
- **Enumeration resistance:** login, OTP and password-reset responses are identical whether or not the account exists; response timing is equalised.

## 5. Authorisation
See ARCHITECTURE §7. Invariants:
- **Decision = role permission ∧ relationship ∧ consent ∧ state.** Evaluated centrally by the `access` policy engine, never inline in routers.
- **Deny by default**; a CI test fails any route without exactly one policy.
- Patient-scoped queries always filter on the authorised `patient_id` (no fetch-then-check).
- **Doctors must be verified** before any patient relationship or prescribing.
- **Caregiver scopes are least-privilege**, can expire, and only a patient or guardian can grant them.
- **Admins have no clinical read access by default.**
- **Break-glass:** reason required, 60-minute limit, emergency dataset only, immediate notification to the patient and guardians, and a mandatory after-the-fact review.
- **Defence in depth (Phase 20):** PostgreSQL Row-Level Security on patient-scoped tables, keyed on a per-transaction `app.patient_ids` setting, as a backstop against a missed filter.
- A **policy matrix test suite** covers every patient-scoped route (see PROJECT_RULES §9).

## 6. Consent and privacy (DPDP Act 2023)
| Requirement | Control |
|---|---|
| Notice (s.5) | Versioned `privacy_notices` in English and Hindi; consent tied to the notice version shown |
| Consent (s.6): free, specific, informed, unambiguous; withdrawable as easily as given | Purpose- and category-specific `consents`; optional purposes are unticked by default; one-click withdrawal; enforced by the policy engine within 60 s |
| Legitimate uses (s.7) | Medical emergency (SOS, break-glass) recorded as legitimate-use events |
| Purpose limitation and minimisation | Purposes defined in code; AI processing is a separate purpose; PHI minimised before external AI calls |
| Retention and erasure (s.8(7)) | Retention schedule per data class; erasure workflow (keeping only what the law requires to be kept); erasure also covers derivatives (thumbnails, OCR text, embeddings, AI conversations) |
| Security safeguards (s.8(5)) | This document |
| Breach notification (s.8(6)) | Incident runbook (§12) with Data Protection Board and data-principal notification |
| Children (s.9) | Verifiable parental consent through the guardian flow; no tracking or targeted advertising; no behavioural monitoring |
| Rights (s.11–14) | Access, correction, erasure, grievance, nomination; tracked in `dpdp_requests` with SLA timers |
| Grievance officer | Named contact, shown in the app and the notice |
| Processors | Contracts with each processor listed in the DPDP register; data residency in India where required |

**ABDM (when integrated):** ABHA linking only with explicit patient action; HIP/HIU consent artefacts mapped to `consents`; FHIR bundles exchanged only under an active artefact; follow the ABDM Health Data Management Policy.

## 7. Cryptography and key management
| Use | Algorithm / mechanism |
|---|---|
| Transport | TLS 1.2+ (1.3 preferred), HSTS preload, modern cipher suites only |
| Data at rest (DB, backups, disks) | Provider-managed AES-256 |
| Object storage | SSE-S3 or SSE-KMS, required on every upload (enforced by the presigned policy) |
| Field-level (C4 free text, C3 identifiers, TOTP secrets) | Envelope encryption: AES-256-GCM data keys per table or column family, wrapped by a KMS master key; a key ID stored with the ciphertext |
| Searchable identifiers | HMAC-SHA-256 blind index with a separate key |
| Passwords | Argon2id |
| Tokens (refresh, OTP, recovery codes, invitations, emergency links) | Hashed (SHA-256) at rest; 128+ bits of entropy |
| JWT signing | Ed25519, rotated every 90 days |
| Audit chain | SHA-256 hash chain; daily head anchored to write-once storage |

**Key management:** keys live in a KMS or secret manager, never in code or images. Application roles can use keys but not export them. Rotation: data keys re-wrapped when the master key rotates; re-encryption jobs for compromised keys. Development uses static local keys that are clearly marked as dev-only.

## 8. Application security controls
Mapped to the OWASP ASVS 4 Level 2 target and the OWASP API Top 10.
- **Input:** Pydantic v2 strict models on every request; size limits on bodies (1 MB JSON) and uploads; enum and range validation for clinical values (dose, frequency, units).
- **Output:** explicit response schemas (no ORM leakage, no extra fields); no rejected input echoed in errors; 500s carry no internals (implemented).
- **Injection:** SQLAlchemy expressions only; no raw SQL string building; no `eval` or shell calls with user data; templates auto-escape.
- **Browser:** strict CSP (`default-src 'self'`, no inline script, `frame-ancestors 'none'`), `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `Permissions-Policy`; API responses `Cache-Control: no-store` (implemented); the SPA sanitises any rich text and never uses `dangerouslySetInnerHTML` on user or AI content.
- **CORS:** the production SPA and API share an origin, so CORS is off. Development allows `localhost` only.
- **Rate limiting:** Redis-backed limits per IP and per user on auth, OTP, search, uploads and AI (implemented in `core/rate_limit.py`); AI has token budgets per user per day.
- **Uploads:** presigned POST with type and size constraints; magic-byte check; ClamAV; EXIF stripping; PDF active-content check; files served only through short-lived presigned GETs with `attachment` disposition.
- **SSRF:** the server never fetches user-supplied URLs; outbound calls go only to allow-listed provider hosts.
- **Mass assignment:** separate create, update and response schemas; server-controlled fields (owner, status, verification) are never accepted from clients.
- **Concurrency:** optimistic locking on clinical edits; idempotency keys on side-effecting endpoints.
- **Emergency QR view:** unguessable, revocable, time-limited token; minimal dataset; `noindex`; rate-limited; every view audited and notified.

## 9. AI-specific security
See [AI_SAFETY.md](AI_SAFETY.md). Security points:
- **Prompt injection:** uploaded content and retrieved text are treated as untrusted data, wrapped in delimiters and never followed as instructions. Assistants use **read-only**, policy-checked tools scoped to the current patient, so no tool can reach another patient's data.
- **Data sent to AI providers:** only with the `ai_processing` consent; minimised (names and identifiers replaced when not needed); zero-data-retention settings; no provider training on our data; the provider is listed as a processor.
- **Cost and abuse:** per-user and global budgets; circuit breaker when the provider errors.
- **Output handling:** model output is data, never code; it is rendered as text; structured output is validated against a JSON schema.

## 10. Infrastructure security
- **Network:** a private network for all services; only the TLS proxy is public; the database, Redis and storage are reachable only from application hosts; egress allow-list for provider APIs.
- **Containers:** non-root, minimal pinned base images, read-only root filesystem where possible, dropped Linux capabilities, `no-new-privileges`, resource limits, Trivy scans in CI (block on critical).
- **Hosts:** CIS-hardened images, automatic security patching, no password SSH (SSM or bastion with MFA), disk encryption.
- **Secrets:** a secret manager delivers them as files or Docker secrets. `.env` is only for local development and is git-ignored. Gitleaks runs in pre-commit and CI (implemented).
- **Least privilege:** separate DB roles for migrations (DDL), the app (DML; no UPDATE or DELETE on `audit_events`) and read-only analytics; storage credentials limited to one bucket and prefix.
- **Backups:** encrypted, stored in a separate account or project, restore-tested monthly, retained according to policy.
- **Environments:** staging and development never contain real patient data.

## 11. Logging, monitoring and detection
- **Application logs:** structured, redacted (implemented), no request bodies, no query strings; kept 30 to 90 days.
- **Audit trail:** see ARCHITECTURE §11. Tamper-evident and patient-visible.
- **Alerts (examples):**
  - A user reads more than N distinct patients in M minutes.
  - Repeated authorisation denials from one principal.
  - Any break-glass use.
  - Admin activity outside business hours.
  - A spike in refresh-token reuse.
  - An OTP send spike for one number prefix.
  - AI output-validator blocks above baseline.
  - The audit hash-chain check fails.
- **Security telemetry** goes to a central store with restricted access; the alert runbooks are linked from each alert.

## 12. Incident response
1. **Detect and triage** (on-call; severity SEV1–4).
2. **Contain:** revoke sessions and tokens, rotate keys, disable a feature flag, block an IP or account.
3. **Assess the data impact:** use the audit trail to find the affected patients and data categories.
4. **Notify:** the Data Protection Board and affected data principals as required by the DPDP Act and Rules, plus CERT-In within its required timeline for reportable incidents. Keep a notification log.
5. **Recover, then run a blameless post-mortem** within 5 working days, with tracked actions.

Runbooks live in `docs/runbooks/` (written in roadmap Phase 20).

## 13. Secure development lifecycle
- **Design:** threat-model delta for every new trust boundary or data flow; a "Safety & privacy impact" section in every PR.
- **Build:** pre-commit hooks (ruff, mypy, eslint, gitleaks); required second review for security-critical modules.
- **CI:** SAST (Semgrep), dependency audit (pip-audit, pnpm audit), container scan (Trivy), secret scan, OpenAPI breaking-change diff, policy-matrix tests, AI safety evals.
- **Release:** signed images, SBOM, staged rollout, rollback plan.
- **Verify:** external penetration test before launch and yearly after; an IDOR sweep of every patient-scoped route before each major release; a vulnerability disclosure policy (`/.well-known/security.txt`).

## 14. Security acceptance criteria (before launch)
- [ ] Policy-matrix tests cover 100% of patient-scoped routes
- [ ] External pentest: no open critical or high findings
- [ ] OWASP ASVS L2 checklist reviewed and signed off
- [ ] Backup restore drill passed within RTO 4 h / RPO 15 min
- [ ] Audit hash chain verified end to end
- [ ] DPDP legal review complete; processor contracts signed; grievance officer named
- [ ] Incident runbooks rehearsed (tabletop exercise)
