# Threat model (v0, Phase 0)

This is a first pass using STRIDE, to be reviewed again in Phases 20 and 25.

## Assets
- Patient health data: conditions, medications, prescriptions, lab results, records, AI conversations
- Identifiers: phone, email, ABHA number or address, date of birth, location during SOS
- Credentials: passwords, OTPs, refresh tokens, TOTP secrets, API keys (Anthropic, SMS, AWS)
- Integrity of clinical actions: issued prescriptions, doses, safety overrides

## Actors
Patient, caregiver, doctor, admin, anonymous internet user, a person holding an emergency QR link, a compromised device, a malicious insider, and third parties (SMS, AI, OCR providers).

## Key threats and planned mitigations
| # | Threat | STRIDE | Mitigation | Phase |
|---|---|---|---|---|
| T1 | IDOR: a user reads another patient's data by changing an ID | I, E | Policy on every route; relationship check for every patient-scoped access; policy-matrix tests; IDOR pentest | 1, 3, 25 |
| T2 | Account takeover through OTP brute force or SIM swap | S | OTP rate limits and lockout; required TOTP for doctors and admins; new-device alerts | 3 |
| T3 | Refresh token theft | S | Rotation with reuse detection that revokes the session family; httpOnly cookies; device list | 3 |
| T4 | Fake doctor issues prescriptions | S, E | Registration-number verification by an admin before prescribing | 3, 21 |
| T5 | PHI leaks through logs, errors or analytics | I | Redaction processor; no input echo in errors; Sentry scrubbing; no third-party analytics on PHI pages | 1, 24 |
| T6 | Prompt injection through uploaded documents or chat hijacks the assistant's tools | T, I, E | Tools are read-only and policy-checked; untrusted content is delimited; output is advisory only; red-team eval suite | 8, 13 |
| T7 | Wrong OCR or AI output causes a medication error | T | Required human confirmation; deterministic safety rules; per-field confidence flags | 8, 14 |
| T8 | Missed reminders because of worker crash or duplicate sends | D | Acks-late idempotent tasks; delivery keys; local notifications on mobile as a fallback | 10 |
| T9 | Leaked or long-lived emergency QR link | I | Time-limited revocable tokens; minimal data shown; every access audited | 17 |
| T10 | Malicious file upload (malware, oversized, polyglot) | T, D | Presigned POST with type and size limits; ClamAV scan; files never served from the API origin | 1, 20 |
| T11 | Insider (admin) abuse | E, R | MFA step-up; hash-chained audit log; least-privilege admin roles | 20, 21 |
| T12 | Data leaves India, or processors are used without agreements | I | `ap-south-1` hosting; processor register; zero-retention AI settings where available | 20, 24 |
| T13 | Denial of service on the API, OTP or AI (cost exhaustion) | D | Redis rate limits; per-user AI budget; WAF | 1, 13, 24 |
| T14 | Tampering with audit history | R | Append-only table, hash chain and verification tool | 20 |

## Trust boundaries
Browser/mobile ↔ API (TLS, auth) · API ↔ Postgres/Redis/S3 (private network, IAM) · API ↔ AI, OCR and SMS providers (outbound only, minimal data) · public emergency view (unauthenticated, token-gated).
