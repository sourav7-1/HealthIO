# Phase 10: Medication reminders

## Dose states
`scheduled → notified → (snoozed ↔ notified) → taken | skipped | missed`

| State | Meaning |
|---|---|
| `scheduled` | Materialised from the schedule, not yet due |
| `notified` | A reminder was sent (`notified_at`, `notify_count`) |
| `snoozed` | The person asked to be reminded later (`snoozed_until`, at most 3 snoozes) |
| `taken` / `skipped` | Recorded by the patient or a caregiver with `log_doses` (skip reason optional) |
| `missed` | No response within the person's "missed after" setting (default 2 h). Recorded by the system |

A person can still record a missed dose as "I took it" or "Skip".

## The engine never changes the dose
- A reminder shows the dose row exactly as stored.
- Migration `0010` adds `trg_medication_doses_clinical_frozen`: the only columns that can change on a dose are its status, snooze, taken and notification fields. Changing the amount, unit, time or medicine raises `HI001`. A test checks this.

## Reminder content ("Time for your medication")
- **Content:** medicine, dose, food relation and instructions, plus the source label (Prescribed by your doctor, Paper prescription, Added by you…).
- **Instructions:** marked "from the prescription" only when the prescription was doctor-issued or verified. Otherwise they are shown as "not from a prescription".
- **Lock screen:** push text is private by default ("Open Health Io to see which medicine to take."). Medicine names appear only if the person turns on "Show medicine names in reminders".

## Missed doses: no advice
- **What is shown:**
  - the instructions as written (and whether they are verified);
  - a fixed message (`MISSED_GUIDANCE` in `app/modules/reminders/engine.py`): Health Io can't say whether to take the dose now; follow the prescription, or ask a doctor or pharmacist; don't take extra doses unless told to.
- **Where:** on the Today page under each missed dose, and in missed-dose notifications.
- **Tests:** a test makes sure the text contains no instruction to take or skip.

## Scheduling
- **Time zones and DST:** fixed times of day use the patient's IANA time zone.
  - The wall-clock time is kept across DST.
  - A time that doesn't exist in spring (the clock skips it) moves to after the gap.
  - A time that happens twice in autumn is reminded once.
- **Local calendar:** weekdays and every-N-days count local days.
- **Interval schedules:** use elapsed time.
- **Materialising:** doses are materialised 48 h ahead (`HIO_REMINDER_HORIZON_HOURS`).

## Preferences (per patient)
- **Basics:** reminders on/off; channels; show names.
- **Timing:** quiet hours (wrap past midnight); snooze length; **remind again after** (5–60 min, or never; one repeat); missed-after.
- **Caregivers:** alert caregivers on missed doses.
- **Quiet hours:** they hold back push notifications only. The in-app reminder still appears when the app is opened.

## Who is reminded
- **Adult patient:** the patient.
- **Dependant (no login):** caregivers with the `log_doses` scope.
- **Missed doses:** the patient, plus caregivers with `receive_alerts` when the patient allows it.

## Background jobs (Celery beat, `app/workers`)
| Task | Every | Does |
|---|---|---|
| `reminders.materialize` | 15 min | Completes finished courses, materialises the next 48 h |
| `reminders.dispatch_due` | 60 s | Sends due, snoozed-and-due and repeat reminders |
| `reminders.detect_missed` | 5 min | Marks overdue doses missed and sends missed-dose notices |

- **Concurrency:** rows are claimed with `FOR UPDATE … SKIP LOCKED`, so several workers can run.
- **No duplicates:** each delivery has an idempotency key (`dose:{id}:{seq}:{user}:in_app|push`) behind a unique index, so a restarted worker never sends twice.
- **Stale doses:** doses more than 12 h old are never reminded.
- **Fallback:** the API also materialises and marks missed doses lazily when the due list is read. Reminders therefore work in development without a worker.

## Notifications
- **In-app inbox:** `GET /me/notifications`, mark one or all read.
- **Web Push:** VAPID via `pywebpush` behind the `PushSender` interface.
  - Without keys, `NullPushSender` is used and push is off.
  - A subscription is revoked when the push service returns 404/410, or after 5 failures.
- **Setup:** generate keys with `uv run python scripts/generate_vapid_keys.py`.
- **PWA:** `apps/web/public/manifest.webmanifest`, `sw.js` and `icon.svg`.
  - The service worker only shows notifications and opens the app. It caches no health data.
  - Its Take/Snooze actions open the app at `?reminder=<dose>&action=…`. Nothing is recorded until the person confirms in the app.
- **Web app:**
  - `ReminderPrompt` (patient portal and each caregiver person view) polls `/reminders/due` every minute and shows the dialog with Taken, Snooze N min (hidden at the limit) and Skip (optional reason).
  - Settings → "Notifications on this device" turns push on or off.

## Tests
- **`tests/test_reminders.py`** (pure):
  - India's fixed offset; New York DST end and spring gap; ambiguous autumn time;
  - Auckland weekdays by local day; every other day across London DST; course dates;
  - quiet hours; lock-screen privacy; missed guidance is not advice.
- **`tests/db/test_reminder_engine.py`** (simulated clock):
  - one reminder then one repeat; idempotent re-runs; snooze returns;
  - quiet hours suppress push only; disabled reminders;
  - missed + caregiver alert with no duplicates; dependant → guardian;
  - the dose cannot be changed; due endpoint; gone device revoked; inbox.
- **Web:**
  - `features/reminders/reminders.test.tsx`: content, Taken / Snooze / Skip, snooze limit, notification deep link never records by itself, unverified label;
  - missed-dose guidance and `notified` in `patient.test.tsx`.
- **Totals:** 231 API tests and 42 web tests pass.

## Not yet
- SMS and email channels (Phase 19). The preferences are stored already.
- Refill reminders from inventory, and "smart" timing suggestions.
- Native mobile local notifications (Expo app).
- Load test on the reminders queue (Phase 23).
