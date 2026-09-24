"""Reminder engine against the database with a simulated clock: dose creation, reminders,
repeats, snooze, quiet hours, privacy, missed-dose detection and caregiver alerts,
idempotency, and the rule that the engine never changes a prescribed dose."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.medications import doses as dose_service
from app.modules.medications.models import DoseRecordedVia, DoseStatus, MedicationDose
from app.modules.notifications import service as notify
from app.modules.notifications.models import Notification
from app.modules.notifications.push import PushMessage, PushResult, Target
from app.modules.reminders import engine
from tests.db.conftest import Builder, expect_db_error, login
from tests.db.test_authorization import patient_id_of

pytestmark = pytest.mark.integration

API = "/api/v1"
TZ = "Asia/Kolkata"
HI001 = "HI001"


@dataclass
class FakePush:
    configured: bool = True
    sent: list[tuple[str, PushMessage]] = field(default_factory=list)
    gone: bool = False

    async def send(self, target: Target, message: PushMessage) -> PushResult:
        if self.gone:
            return PushResult(ok=False, gone=True, error="push 410")
        self.sent.append((target.endpoint, message))
        return PushResult(ok=True)


def next_local(at: time, tz: str = TZ) -> datetime:
    """The next occurrence of local `at` (at least a minute away), in UTC."""
    zone = ZoneInfo(tz)
    now = datetime.now(UTC).astimezone(zone)
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= now + timedelta(minutes=1):
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


async def patient_with_medicine(
    api: Any, make_account: Builder, session: AsyncSession, **body: Any
) -> tuple[Any, uuid.UUID, dict[str, str], str]:
    user = await make_account(Role.PATIENT)
    pid = await patient_id_of(session, user)
    h = await login(api, user.test_email)
    resp = await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={
            "name": "Tab. Samplemycin",
            "strength": "500 mg",
            "times_of_day": ["08:00"],
            "dose_amount": "1",
            "dose_unit": "tablet",
            "meal_relation": "after_food",
            "timezone": TZ,
            **body,
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    return user, pid, h, resp.json()["id"]


async def subscribe(session: AsyncSession, user_id: uuid.UUID) -> None:
    await notify.subscribe(
        session,
        user_id=user_id,
        endpoint=f"https://push.example.test/{uuid.uuid4().hex}",
        p256dh="p256dh-placeholder-key",
        auth="auth-placeholder",
        user_agent="test",
    )


async def the_dose(session: AsyncSession, mid: str) -> MedicationDose:
    row = await session.scalar(
        select(MedicationDose)
        .where(MedicationDose.medication_id == uuid.UUID(mid))
        .order_by(MedicationDose.scheduled_at)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def in_app(session: AsyncSession, user_id: uuid.UUID, kind: str) -> list[Notification]:
    rows = await session.scalars(
        select(Notification).where(
            Notification.recipient_user_id == user_id,
            Notification.channel == "in_app",
            Notification.template_key == kind,
        )
    )
    return list(rows.all())


async def test_due_dose_is_reminded_once_then_repeated_once(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _, _, mid = await patient_with_medicine(api, make_account, session)
    await subscribe(session, user.id)
    due = next_local(time(8, 0))
    push = FakePush()

    # Doses are created ahead, in local time (08:00 IST = 02:30 UTC).
    assert (
        await engine.materialize_all(
            session, now=due - timedelta(hours=1), horizon=timedelta(hours=48)
        )
        >= 1
    )
    dose = await the_dose(session, mid)
    assert dose.scheduled_at == due
    assert dose.status == DoseStatus.SCHEDULED

    # Not due yet: nothing is sent.
    early = await engine.dispatch_due(session, now=due - timedelta(minutes=1), sender=push)
    assert early.doses == 0

    report = await engine.dispatch_due(session, now=due + timedelta(minutes=1), sender=push)
    assert report.doses == 1
    dose = await the_dose(session, mid)
    assert dose.status == DoseStatus.NOTIFIED
    assert dose.notify_count == 1
    assert len(push.sent) == 1
    message = push.sent[0][1]
    assert message.title == "Time for your medication"
    assert message.body == engine.PRIVATE_BODY  # medicine not named on the lock screen
    assert message.url.endswith(f"/patient?reminder={dose.id}")
    [note] = await in_app(session, user.id, "dose_due")
    assert "Tab. Samplemycin 500 mg" in (note.body or "")
    assert "1 tablet" in (note.body or "")

    # Running again (or on another worker) sends nothing more.
    again = await engine.dispatch_due(session, now=due + timedelta(minutes=2), sender=push)
    assert again.doses == 0
    assert len(push.sent) == 1

    # One repeat after 15 minutes without an answer, then no more.
    repeat = await engine.dispatch_due(session, now=due + timedelta(minutes=17), sender=push)
    assert repeat.doses == 1
    assert (await the_dose(session, mid)).notify_count == 2
    assert (
        await engine.dispatch_due(session, now=due + timedelta(minutes=40), sender=push)
        == engine.Report()
    )
    assert len(push.sent) == 2


async def test_snooze_brings_the_reminder_back(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _, _, mid = await patient_with_medicine(api, make_account, session)
    due = next_local(time(8, 0))
    push = FakePush()
    await subscribe(session, user.id)
    await engine.materialize_all(session, now=due - timedelta(hours=1), horizon=timedelta(hours=48))
    await engine.dispatch_due(session, now=due, sender=push)
    dose = await the_dose(session, mid)
    await dose_service.record_dose(
        session,
        patient_id=dose.patient_id,
        dose_id=dose.id,
        actor=user.id,
        via=DoseRecordedVia.PATIENT_APP,
        action="snooze",
        snooze_minutes=10,
        now=due + timedelta(minutes=1),
    )
    assert (await the_dose(session, mid)).status == DoseStatus.SNOOZED
    assert (
        await engine.dispatch_due(session, now=due + timedelta(minutes=5), sender=push)
    ).doses == 0
    back = await engine.dispatch_due(session, now=due + timedelta(minutes=12), sender=push)
    assert back.doses == 1
    after = await the_dose(session, mid)
    assert after.status == DoseStatus.NOTIFIED
    assert after.snoozed_until is None
    assert len(push.sent) == 2


async def test_quiet_hours_privacy_and_disabled_reminders(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, pid, h, _ = await patient_with_medicine(api, make_account, session)
    await subscribe(session, user.id)
    prefs = (await api.get(f"{API}/patients/{pid}/reminder-preferences", headers=h)).json()
    await api.put(
        f"{API}/patients/{pid}/reminder-preferences",
        json={
            **prefs,
            "quiet_hours_start": "07:00",
            "quiet_hours_end": "09:00",
            "show_medicine_names": True,
        },
        headers=h,
    )
    due = next_local(time(8, 0))
    push = FakePush()
    await engine.materialize_all(session, now=due - timedelta(hours=1), horizon=timedelta(hours=48))
    report = await engine.dispatch_due(session, now=due, sender=push)
    assert report.doses == 1
    assert report.suppressed == 1
    assert push.sent == []  # quiet hours: no push…
    assert len(await in_app(session, user.id, "dose_due")) == 1  # …but the app still shows it

    # With reminders turned off nothing is sent at all.
    await api.put(
        f"{API}/patients/{pid}/reminder-preferences",
        json={**prefs, "reminders_enabled": False},
        headers=h,
    )
    tomorrow = due + timedelta(days=1)
    await engine.materialize_all(
        session, now=tomorrow - timedelta(hours=1), horizon=timedelta(hours=48)
    )
    assert (await engine.dispatch_due(session, now=tomorrow, sender=push)).doses == 0


async def test_missed_doses_are_recorded_and_caregivers_alerted_without_advice(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, pid, h, mid = await patient_with_medicine(api, make_account, session)
    carer = await make_account()
    rel = await api.post(
        f"{API}/patients/{pid}/caregivers",
        json={
            "caregiver_email": carer.test_email,
            "relationship_type": "child",
            "scopes": ["view_medications", "receive_alerts"],
        },
        headers=h,
    )
    ch = await login(api, carer.test_email)
    await api.post(
        f"{API}/caregiver-invitations/{rel.json()['relationship_id']}/accept", headers=ch
    )
    await subscribe(session, carer.id)

    due = next_local(time(8, 0))
    push = FakePush()
    await engine.materialize_all(session, now=due - timedelta(hours=1), horizon=timedelta(hours=48))
    await engine.dispatch_due(session, now=due, sender=push)
    # Default missed window: 2 hours.
    assert (
        await engine.detect_missed(session, now=due + timedelta(minutes=90), sender=push)
    ).doses == 0
    report = await engine.detect_missed(
        session, now=due + timedelta(hours=2, minutes=5), sender=push
    )
    assert report.doses == 1
    assert (await the_dose(session, mid)).status == DoseStatus.MISSED

    [mine] = await in_app(session, user.id, "dose_missed")
    [theirs] = await in_app(session, carer.id, "dose_missed")
    for note in (mine, theirs):
        assert engine.MISSED_GUIDANCE in (note.body or "")
        assert "take it now" not in (note.body or "").lower()
    assert any("missed" in m.title for _, m in push.sent)

    # Repeating the job does not alert again.
    await engine.detect_missed(session, now=due + timedelta(hours=3), sender=push)
    assert len(await in_app(session, carer.id, "dose_missed")) == 1


async def test_dependant_reminders_go_to_caregivers_who_log_doses(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    parent = await make_account()
    ph = await login(api, parent.test_email)
    child = (
        await api.post(
            f"{API}/me/dependants",
            json={
                "given_name": "Dependant",
                "date_of_birth": "2019-05-05",
                "sex_at_birth": "unknown",
                "relationship_type": "parent",
                "basis": "parent_of_minor",
                "declaration_accepted": True,
                "timezone": TZ,
            },
            headers=ph,
        )
    ).json()["patient_id"]
    add = await api.post(
        f"{API}/patients/{child}/medications/self-reported",
        json={"name": "Syp. Exampledryl", "times_of_day": ["08:00"], "timezone": TZ},
        headers=ph,
    )
    assert add.status_code == 201, add.text
    due = next_local(time(8, 0))
    push = FakePush()
    await engine.materialize_all(session, now=due - timedelta(hours=1), horizon=timedelta(hours=48))
    await engine.dispatch_due(session, now=due, sender=push)
    [note] = await in_app(session, parent.id, "dose_due")
    assert note.data["patient_id"] == child


async def test_the_engine_never_changes_the_prescribed_dose(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, _, _, mid = await patient_with_medicine(api, make_account, session)
    due = next_local(time(8, 0))
    await engine.materialize_all(session, now=due - timedelta(hours=1), horizon=timedelta(hours=48))
    before = await the_dose(session, mid)
    amount, unit, at = before.dose_amount, before.dose_unit, before.scheduled_at
    await engine.dispatch_due(session, now=due, sender=FakePush())
    await engine.detect_missed(session, now=due + timedelta(hours=3), sender=FakePush())
    after = await the_dose(session, mid)
    assert (after.dose_amount, after.dose_unit, after.scheduled_at) == (amount, unit, at)
    # The database refuses any change to a dose's clinical content, whatever the code does.
    for column, value in [("dose_amount", "2"), ("dose_unit", "'ml'"), ("scheduled_at", "now()")]:
        async with expect_db_error(session, HI001):
            await session.execute(
                text(f"UPDATE medication_doses SET {column} = {value} WHERE id = :d"),  # noqa: S608
                {"d": after.id},
            )


async def test_due_reminders_endpoint_and_dead_push_devices(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, pid, h, mid = await patient_with_medicine(
        api, make_account, session, times_of_day=["23:59"]
    )
    # Make "now" fall on a dose: backdate the schedule and move the dose time to the past.
    now = datetime.now(UTC)
    await session.execute(
        text("UPDATE medication_schedules SET effective_from = :t WHERE medication_id = :m"),
        {"t": now - timedelta(days=2), "m": mid},
    )
    local = (
        (now - timedelta(minutes=5))
        .astimezone(ZoneInfo(TZ))
        .time()
        .replace(second=0, microsecond=0)
    )
    await session.execute(
        text(
            "UPDATE medication_schedules SET times_of_day = ARRAY[CAST(:t AS time)] "
            "WHERE medication_id = :m"
        ),
        {"t": local, "m": mid},
    )
    due = (await api.get(f"{API}/patients/{pid}/reminders/due", headers=h)).json()
    assert len(due) == 1
    r = due[0]
    assert r["medicine"] == "Tab. Samplemycin 500 mg"
    assert r["dose"] == "1 tablet"
    assert r["meal"] == "after food"
    assert r["source_label"] == "Added by you"
    assert r["can_snooze"] is True

    taken = await api.post(f"{API}/patients/{pid}/doses/{r['dose_id']}/take", json={}, headers=h)
    assert taken.status_code == 200
    assert (await api.get(f"{API}/patients/{pid}/reminders/due", headers=h)).json() == []

    # A device the push service says is gone is dropped.
    await subscribe(session, user.id)
    sender = FakePush(gone=True)
    draft = notify.Draft(
        recipient_user_id=user.id,
        patient_id=pid,
        category=engine.NotificationCategory.MEDICATION_REMINDER,
        template_key="dose_due",
        title="t",
        body="b",
        data={},
        idempotency_key="test:gone",
    )
    msg = PushMessage(title="t", body="b", tag="x", url="/")
    assert await notify.deliver_push(session, sender, draft, msg, now=now) == 0
    assert await notify.active_subscriptions(session, {user.id}) == []

    inbox = (await api.get(f"{API}/me/notifications", headers=h)).json()
    assert inbox["unread"] >= 0
