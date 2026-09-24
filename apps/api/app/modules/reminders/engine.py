"""The reminder engine: create upcoming doses, send reminders when they fall due, and
record doses nobody answered as missed. Runs as Celery beat jobs (app/workers/tasks.py);
each step is safe to run repeatedly and on several workers at once:

- rows are claimed with SELECT … FOR UPDATE SKIP LOCKED, so two workers never take
  the same dose;
- every notification has an idempotency key (dose, reminder number, recipient,
  channel), so a retried job never sends the same reminder twice;
- dose rows are created with ON CONFLICT DO NOTHING (see doses.materialize).

What it may change on a dose: its status (scheduled → notified → missed) and the
reminder bookkeeping. The dose, unit, time and medicine are frozen by a database
trigger (migration 0010), so the engine can never change a prescribed dose.

It gives no medical advice. A missed dose is reported as missed, with the medicine's
verified instructions as written and a pointer to the doctor or pharmacist. It never
suggests taking it late, doubling up or skipping.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import VerificationStatus
from app.modules.caregivers.models import (
    CaregiverPermission,
    CaregiverPermissionScope,
    CaregiverRelationship,
    CaregiverStatus,
)
from app.modules.medications import doses, regimen
from app.modules.medications.models import (
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationOrigin,
    MedicationSchedule,
    MedicationStatus,
    ScheduleStatus,
)
from app.modules.notifications import service as notify
from app.modules.notifications.models import NotificationCategory, NotificationPriority
from app.modules.notifications.push import PushMessage, PushSender
from app.modules.patients.models import PatientProfile
from app.modules.prescriptions.models import Prescription, PrescriptionItem, PrescriptionSource
from app.modules.reminders import service as preferences
from app.modules.reminders.models import ReminderPreference

REMINDER_TITLE = "Time for your medication"
PRIVATE_BODY = "Open Health Io to see which medicine to take."
MISSED_TITLE = "A dose was missed"
MISSED_GUIDANCE = (
    "Health Io can't tell you whether to take a missed dose now. Follow what the "
    "prescription says about missed doses if it says anything, or ask your doctor or "
    "pharmacist. Do not take extra doses to make up unless a doctor or pharmacist tells you to."
)
STALE_AFTER = timedelta(hours=12)  # never send a reminder for a dose this old
DEFAULT_REPEAT = timedelta(minutes=15)
BATCH = 200

_MEAL = {
    "before_food": "before food",
    "after_food": "after food",
    "with_food": "with food",
    "empty_stomach": "on an empty stomach",
    "bedtime": "at bedtime",
}
ORIGIN_LABEL = {
    MedicationOrigin.DOCTOR_PRESCRIPTION: "Prescribed by your doctor",
    MedicationOrigin.CLINICIAN_RECORDED: "Recorded by your doctor",
    MedicationOrigin.UPLOADED_AI: "From a paper prescription (read by AI, checked)",
    MedicationOrigin.UPLOADED_TYPED: "From a paper prescription (typed in)",
    MedicationOrigin.SELF_REPORTED: "Added by you",
    MedicationOrigin.INTEGRATION: "From a connected record",
}


# --- what a reminder says ---------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderContent:
    medicine: str
    dose: str | None  # exactly as stored on the dose row (from the schedule)
    instructions: str | None  # as written on the prescription, or the patient's own note
    meal: str | None
    source_label: str
    instructions_verified: bool  # written by the prescriber or checked by a person


def dose_text(dose: MedicationDose) -> str | None:
    if dose.dose_amount is None:
        return None
    amount = format(dose.dose_amount.normalize(), "f")
    return f"{amount} {dose.dose_unit}".strip() if dose.dose_unit else amount


def content_for(
    dose: MedicationDose,
    med: Medication,
    schedule: MedicationSchedule | None,
    item: PrescriptionItem | None,
    rx: Prescription | None,
) -> ReminderContent:
    written = None
    verified = False
    if item is not None:
        written = "; ".join(p for p in [item.frequency_text, item.instructions] if p) or None
        verified = rx is not None and (
            rx.source == PrescriptionSource.DOCTOR_ISSUED
            or rx.verification_status
            in (VerificationStatus.PATIENT_VERIFIED, VerificationStatus.DOCTOR_VERIFIED)
        )
    meal = schedule.meal_relation.value if schedule and schedule.meal_relation else None
    return ReminderContent(
        medicine=" ".join(p for p in [med.name, med.strength] if p),
        dose=dose_text(dose),
        instructions=written or med.instructions,
        meal=_MEAL.get(meal) if meal else None,
        source_label=ORIGIN_LABEL.get(med.origin, "Medicine"),
        instructions_verified=verified,
    )


def reminder_body(c: ReminderContent, *, show_names: bool) -> str:
    """Lock-screen text. Without the patient's opt-in it does not name the medicine."""
    if not show_names:
        return PRIVATE_BODY
    return " · ".join(p for p in [c.medicine, c.dose, c.meal] if p)


def in_app_body(c: ReminderContent) -> str:
    return " · ".join(p for p in [c.medicine, c.dose, c.meal, c.instructions] if p)


def missed_guidance(c: ReminderContent) -> dict[str, str | bool | None]:
    """Deterministic text only; never advice."""
    return {
        "message": MISSED_GUIDANCE,
        "instructions_as_written": c.instructions,
        "instructions_verified": c.instructions_verified,
        "source_label": c.source_label,
    }


# --- loading context --------------------------------------------------------------------------


@dataclass
class Context:
    meds: dict[uuid.UUID, Medication] = field(default_factory=dict)
    schedules: dict[uuid.UUID, MedicationSchedule] = field(default_factory=dict)
    items: dict[uuid.UUID, PrescriptionItem] = field(default_factory=dict)
    rxs: dict[uuid.UUID, Prescription] = field(default_factory=dict)
    profiles: dict[uuid.UUID, PatientProfile] = field(default_factory=dict)
    prefs: dict[uuid.UUID, preferences.Preferences] = field(default_factory=dict)

    def content(self, dose: MedicationDose) -> ReminderContent:
        med = self.meds[dose.medication_id]
        item = self.items.get(med.prescription_item_id) if med.prescription_item_id else None
        return content_for(
            dose,
            med,
            self.schedules.get(dose.schedule_id) if dose.schedule_id else None,
            item,
            self.rxs.get(item.prescription_id) if item else None,
        )


async def load_context(session: AsyncSession, rows: list[MedicationDose]) -> Context:
    ctx = Context()
    if not rows:
        return ctx
    med_ids = {d.medication_id for d in rows}
    ctx.meds = {
        m.id: m
        for m in (await session.scalars(select(Medication).where(Medication.id.in_(med_ids)))).all()
    }
    sched_ids = {d.schedule_id for d in rows if d.schedule_id}
    if sched_ids:
        ctx.schedules = {
            s.id: s
            for s in (
                await session.scalars(
                    select(MedicationSchedule).where(MedicationSchedule.id.in_(sched_ids))
                )
            ).all()
        }
    item_ids = {m.prescription_item_id for m in ctx.meds.values() if m.prescription_item_id}
    if item_ids:
        ctx.items = {
            i.id: i
            for i in (
                await session.scalars(
                    select(PrescriptionItem).where(PrescriptionItem.id.in_(item_ids))
                )
            ).all()
        }
        rx_ids = {i.prescription_id for i in ctx.items.values()}
        ctx.rxs = {
            r.id: r
            for r in (
                await session.scalars(select(Prescription).where(Prescription.id.in_(rx_ids)))
            ).all()
        }
    patient_ids = {d.patient_id for d in rows}
    ctx.profiles = {
        p.id: p
        for p in (
            await session.scalars(select(PatientProfile).where(PatientProfile.id.in_(patient_ids)))
        ).all()
    }
    for pid in patient_ids:
        ctx.prefs[pid] = await preferences.get(session, pid)
    return ctx


async def caregivers_with(
    session: AsyncSession, patient_id: uuid.UUID, scope: CaregiverPermissionScope, now: datetime
) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(CaregiverRelationship.caregiver_user_id)
        .join(
            CaregiverPermission,
            (CaregiverPermission.relationship_id == CaregiverRelationship.id)
            & (CaregiverPermission.revoked_at.is_(None))
            & (CaregiverPermission.scope == scope),
        )
        .where(
            CaregiverRelationship.patient_id == patient_id,
            CaregiverRelationship.status == CaregiverStatus.ACTIVE,
            or_(CaregiverRelationship.expires_at.is_(None), CaregiverRelationship.expires_at > now),
        )
    )
    return set(rows.all())


async def reminder_recipients(
    session: AsyncSession, profile: PatientProfile, now: datetime
) -> set[uuid.UUID]:
    """The patient; for a dependant with no login, caregivers allowed to log doses."""
    if profile.user_id is not None:
        return {profile.user_id}
    return await caregivers_with(session, profile.id, CaregiverPermissionScope.LOG_DOSES, now)


# --- the jobs ---------------------------------------------------------------------------------


@dataclass
class Report:
    doses: int = 0
    in_app: int = 0
    push: int = 0
    suppressed: int = 0


async def materialize_all(session: AsyncSession, *, now: datetime, horizon: timedelta) -> int:
    """Create upcoming dose rows for every patient with an active schedule, and close
    courses whose end date has passed."""
    patient_ids = (
        await session.scalars(
            select(MedicationSchedule.patient_id)
            .where(MedicationSchedule.status == ScheduleStatus.ACTIVE)
            .distinct()
        )
    ).all()
    created = 0
    for pid in patient_ids:
        profile = await session.get(PatientProfile, pid)
        tz = ZoneInfo(profile.timezone if profile else "Asia/Kolkata")
        await regimen.complete_finished(session, pid, now.astimezone(tz).date())
        created += await doses.materialize(session, pid, now=now, horizon=horizon)
    await session.flush()
    return created


def _due_query(now: datetime):  # type: ignore[no-untyped-def]
    prefs = ReminderPreference
    repeat_due = or_(
        and_(prefs.id.is_(None), MedicationDose.notified_at <= now - DEFAULT_REPEAT),
        and_(
            prefs.remind_again_after_minutes.is_not(None),
            MedicationDose.notified_at
            + func.make_interval(0, 0, 0, 0, 0, prefs.remind_again_after_minutes)
            <= now,
        ),
    )
    return (
        select(MedicationDose)
        .join(Medication, Medication.id == MedicationDose.medication_id)
        .outerjoin(prefs, prefs.patient_id == MedicationDose.patient_id)
        .where(
            Medication.status == MedicationStatus.ACTIVE,
            MedicationDose.scheduled_at > now - STALE_AFTER,
            func.coalesce(prefs.reminders_enabled, True).is_(True),
            or_(
                and_(
                    MedicationDose.status == DoseStatus.SCHEDULED,
                    MedicationDose.scheduled_at <= now,
                ),
                and_(
                    MedicationDose.status == DoseStatus.SNOOZED, MedicationDose.snoozed_until <= now
                ),
                and_(
                    MedicationDose.status == DoseStatus.NOTIFIED,
                    MedicationDose.notify_count == 1,
                    repeat_due,
                ),
            ),
        )
        .order_by(MedicationDose.scheduled_at)
        .limit(BATCH)
        .with_for_update(of=MedicationDose, skip_locked=True)
    )


async def dispatch_due(
    session: AsyncSession, *, now: datetime, sender: PushSender, app_url: str = ""
) -> Report:
    """Send reminders for doses that are due (or whose snooze ended, or one repeat)."""
    rows = list((await session.scalars(_due_query(now))).all())
    ctx = await load_context(session, rows)
    report = Report()
    for dose in rows:
        profile = ctx.profiles.get(dose.patient_id)
        if profile is None:
            continue
        prefs = ctx.prefs[dose.patient_id]
        content = ctx.content(dose)
        seq = dose.notify_count + 1
        local = now.astimezone(ZoneInfo(profile.timezone))
        quiet = prefs.in_quiet_hours(local.time())
        data = {"dose_id": str(dose.id), "patient_id": str(dose.patient_id), "kind": "dose_due"}
        for user_id in await reminder_recipients(session, profile, now):
            own = user_id == profile.user_id
            path = "/patient" if own else f"/care/{dose.patient_id}"
            draft = notify.Draft(
                recipient_user_id=user_id,
                patient_id=dose.patient_id,
                category=NotificationCategory.MEDICATION_REMINDER,
                template_key="dose_due",
                title=REMINDER_TITLE,
                body=in_app_body(content),
                data=data,
                idempotency_key=f"dose:{dose.id}:{seq}:{user_id}",
                priority=NotificationPriority.HIGH,
            )
            if await notify.create_in_app(session, draft, now=now):
                report.in_app += 1
            message = PushMessage(
                title=REMINDER_TITLE,
                body=reminder_body(content, show_names=prefs.show_medicine_names),
                tag=f"dose-{dose.id}",
                url=f"{app_url}{path}?reminder={dose.id}",
                data=data,
                actions=[
                    {"action": "take", "title": "Taken"},
                    {"action": "snooze", "title": "Snooze"},
                    {"action": "skip", "title": "Skip"},
                ],
            )
            reason = None if prefs.channel_push else "push turned off"
            if quiet and reason is None:
                reason = "quiet hours"
            reached = await notify.deliver_push(
                session, sender, draft, message, now=now, suppressed_reason=reason
            )
            report.push += reached
            report.suppressed += 1 if reason else 0
        # Only the reminder bookkeeping changes; the dose itself is frozen by a trigger.
        dose.status = DoseStatus.NOTIFIED
        dose.notified_at = now
        dose.notify_count = seq
        dose.snoozed_until = None
        report.doses += 1
    await session.flush()
    return report


async def detect_missed(
    session: AsyncSession, *, now: datetime, sender: PushSender, app_url: str = ""
) -> Report:
    """Unanswered doses past the patient's missed-dose window become MISSED; the patient
    and caregivers who receive alerts are told, with no advice beyond the instructions."""
    prefs = ReminderPreference
    window = func.make_interval(0, 0, 0, 0, 0, func.coalesce(prefs.missed_after_minutes, 120))
    stale = list(
        (
            await session.scalars(
                select(MedicationDose)
                .outerjoin(prefs, prefs.patient_id == MedicationDose.patient_id)
                .where(
                    MedicationDose.status.in_(doses.OPEN),
                    MedicationDose.scheduled_at + window < now,
                    or_(
                        MedicationDose.snoozed_until.is_(None),
                        MedicationDose.snoozed_until + window < now,
                    ),
                )
                .order_by(MedicationDose.scheduled_at)
                .limit(BATCH * 5)
                .with_for_update(of=MedicationDose, skip_locked=True)
            )
        ).all()
    )
    for dose in stale:
        dose.status = DoseStatus.MISSED
        dose.snoozed_until = None
        dose.recorded_via = DoseRecordedVia.SYSTEM
    await session.flush()

    # Alert about recently missed doses (including ones the app marked when opened).
    # Idempotency keys make this safe to repeat every run.
    recent = list(
        (
            await session.scalars(
                select(MedicationDose).where(
                    MedicationDose.status == DoseStatus.MISSED,
                    MedicationDose.scheduled_at > now - timedelta(hours=24),
                )
            )
        ).all()
    )
    ctx = await load_context(session, recent)
    report = Report(doses=len(stale))
    for dose in recent:
        profile = ctx.profiles.get(dose.patient_id)
        if profile is None or dose.medication_id not in ctx.meds:
            continue
        p = ctx.prefs[dose.patient_id]
        content = ctx.content(dose)
        data = {"dose_id": str(dose.id), "patient_id": str(dose.patient_id), "kind": "dose_missed"}
        patients = await reminder_recipients(session, profile, now)
        carers = (
            await caregivers_with(
                session, dose.patient_id, CaregiverPermissionScope.RECEIVE_ALERTS, now
            )
            if p.notify_caregivers_on_missed
            else set()
        )
        for user_id in patients | carers:
            is_patient = user_id == profile.user_id
            name = profile.given_name if not is_patient else None
            draft = notify.Draft(
                recipient_user_id=user_id,
                patient_id=dose.patient_id,
                category=NotificationCategory.MISSED_DOSE,
                template_key="dose_missed",
                title=MISSED_TITLE if is_patient else f"{name} missed a dose",
                body=f"{in_app_body(content)}. {MISSED_GUIDANCE}",
                data=data,
                idempotency_key=f"missed:{dose.id}:{user_id}",
            )
            if await notify.create_in_app(session, draft, now=now):
                report.in_app += 1
                if user_id in carers and not is_patient:
                    message = PushMessage(
                        title=f"{name} missed a dose",
                        body=reminder_body(content, show_names=p.show_medicine_names),
                        tag=f"missed-{dose.id}",
                        url=f"{app_url}/care/{dose.patient_id}",
                        data=data,
                    )
                    report.push += await notify.deliver_push(
                        session, sender, draft, message, now=now
                    )
    await session.flush()
    return report
