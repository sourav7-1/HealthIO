# DPDP data inventory register (v0)

This register records what personal data Health Io processes under the Digital Personal Data Protection Act 2023, for which purpose, and on what basis. Update it whenever a phase adds a new data category, purpose or processor. It is a working engineering document, not legal advice. Legal review is required before launch (Phase 25).

## Roles
- **Data Fiduciary:** the Health Io operator.
- **Data Principals:** patients, caregivers, doctors, and admins (staff data).
- **Children:** users under 18 need verifiable parental consent (s.9). Caregiver-managed dependant profiles come under this rule (Phases 6 and 20).

## Data categories and purposes
| Category | Examples | Purpose | Basis | Retention (draft) | Introduced |
|---|---|---|---|---|---|
| Account identity | phone, email, name | Sign-in, contact | Consent | Life of account + 90 days | Phase 3 |
| Health profile | DOB, sex, blood group, conditions, allergies | Care coordination, safety checks | Consent (specific purpose) | Life of account; clinical records per the retention policy | Phase 2 |
| Prescriptions and medications | drugs, doses, schedules, dose logs | Reminders, adherence, safety | Consent | As above | Phases 7–10 |
| Records and labs | uploaded documents, lab values | Timeline, trends | Consent | As above | Phases 11–12 |
| AI conversations | chat messages | Health assistant | Consent (separate, optional) | 180 days, deletable by the user | Phase 13 |
| Location | SOS coordinates | Emergency alerting | Legitimate use: medical emergency (s.7) | 30 days | Phase 17 |
| Doctor professional data | registration number, specialty | Verification, prescribing | Consent + legitimate use | Life of account | Phase 3 |
| Device data | push tokens, device model | Notifications, security | Consent | Until logout or device removal | Phase 5 |
| Audit logs | actor, action, resource ID | Security, accountability | Legal obligation / legitimate use | 7 years (to confirm) | Phase 2 |

## Processors (to be contracted)
| Processor | Data shared | Region | Phase |
|---|---|---|---|
| AWS (hosting, S3, RDS) | All | ap-south-1 (Mumbai) | 24 |
| Anthropic (Claude API) | Prescription images, text, chat | To confirm; zero-retention settings required | 8, 13 |
| Google Cloud Vision (optional) | Prescription images | asia-south1 | 8 |
| MSG91 / Twilio | Phone numbers, message text (no PHI in SMS) | India | 3, 19 |
| Expo push / FCM | Push tokens, notification text (no PHI on lock screen) | Global | 5, 19 |

## Rights to support (s.11–14)
Access and summary, correction, erasure, grievance redress (named grievance officer, response time), and nomination. Workflows are built in Phases 5 and 20.

## Breach response
Notify the Data Protection Board and the affected principals. The runbook is written in Phase 20.
