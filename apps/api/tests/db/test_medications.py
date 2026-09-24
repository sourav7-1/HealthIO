"""Medication management end to end: origins, schedules, clinically relevant changes,
pause/resume/discontinue, completion, history, change requests and duplicates."""

import uuid
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.medications.models import MedicationDose
from app.modules.patients.models import PatientProfile
from tests.db.conftest import CHECK_VIOLATION, Builder, expect_db_error, login
from tests.db.test_doctor_portal import make_doctor

pytestmark = pytest.mark.integration

API = "/api/v1"
HI001 = "HI001"


async def setup(
    api: Any, make_account: Builder, session: AsyncSession, **item: Any
) -> tuple[str, dict[str, str], dict[str, str], dict[str, Any]]:
    """Patient linked to a doctor who issued one prescription line; returns the medicine."""
    doctor, _ = await make_doctor(make_account, session)
    patient = await make_account(Role.PATIENT)
    pid = str(
        await session.scalar(select(PatientProfile.id).where(PatientProfile.user_id == patient.id))
    )
    dh, ph = await login(api, doctor.test_email), await login(api, patient.test_email)
    await api.post(
        f"{API}/doctor/patients/connect", json={"patient_email": patient.test_email}, headers=dh
    )
    rel = (await api.get(f"{API}/me/doctor-requests", headers=ph)).json()[0]["relationship_id"]
    await api.post(
        f"{API}/me/doctor-requests/{rel}/accept",
        json={"data_categories": ["medications", "prescriptions", "adherence"]},
        headers=ph,
    )
    line = {
        "drug_name": "Tab. Samplemycin",
        "strength": "500 mg",
        "dose_amount": "1",
        "dose_unit": "tablet",
        "frequency_text": "1-0-1",
        "times_per_day": 2,
        "meal_relation": "after_food",
        "duration_days": 30,
        **item,
    }
    rx = (
        await api.post(f"{API}/patients/{pid}/prescriptions", json={"items": [line]}, headers=dh)
    ).json()
    assert (
        await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/issue", headers=dh)
    ).status_code == 200
    med = (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()[0]
    return pid, ph, dh, med


async def confirm(
    api: Any, pid: str, ph: dict[str, str], mid: str, times: list[str]
) -> dict[str, Any]:
    resp = await api.post(
        f"{API}/patients/{pid}/medications/{mid}/confirm", json={"times_of_day": times}, headers=ph
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def schedule(times: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "schedule_type": "fixed_times",
        "times_of_day": times,
        "dose_amount": "1",
        "dose_unit": "tablet",
        "meal_relation": "after_food",
        **kw,
    }


async def events(api: Any, pid: str, h: dict[str, str], mid: str) -> list[str]:
    resp = await api.get(f"{API}/patients/{pid}/medications/{mid}/history", headers=h)
    return [e["event_type"] for e in resp.json()]


async def test_origin_labels_and_prescribed_regimen(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session)
    assert med["origin"] == "doctor_prescription"
    assert med["status"] == "pending_confirmation"
    assert med["prescribed"]["times_per_day"] == 2
    active = await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    # The dose and food relation come from the prescription.
    assert active["schedule"]["dose_amount"] == "1.000"
    assert active["schedule"]["dose_unit"] == "tablet"
    assert active["schedule"]["meal_relation"] == "after_food"
    assert active["end_date"] is not None
    own = await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={"name": "Syp. Exampledryl", "times_of_day": ["21:00"]},
        headers=ph,
    )
    assert own.json()["origin"] == "self_reported"
    assert await events(api, pid, ph, med["id"]) == ["created", "confirmed"]


async def test_timing_change_warnings_and_clinically_relevant_changes(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session)
    await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    base = f"{API}/patients/{pid}/medications/{med['id']}"

    # Moving the times within the written pattern is the patient's choice.
    moved = await api.put(f"{base}/schedule", json=schedule(["07:30", "21:00"]), headers=ph)
    assert moved.status_code == 200, moved.text

    # Doses close together: warn first, apply once acknowledged.
    check = (
        await api.post(f"{base}/schedule/check", json=schedule(["08:00", "09:00"]), headers=ph)
    ).json()
    assert check["requires"] == "acknowledgement"
    assert {f["code"] for f in check["findings"]} >= {"doses_very_close", "times_outside_pattern"}
    assert (
        await api.put(f"{base}/schedule", json=schedule(["08:00", "09:00"]), headers=ph)
    ).status_code == 409
    ok = await api.put(
        f"{base}/schedule", json={**schedule(["08:00", "09:00"]), "acknowledged": True}, headers=ph
    )
    assert ok.status_code == 200

    # A different number of doses a day or a different dose needs a clinician.
    three = schedule(["08:00", "14:00", "20:00"])
    check = (await api.post(f"{base}/schedule/check", json=three, headers=ph)).json()
    assert check["requires"] == "clinician"
    assert (
        await api.put(f"{base}/schedule", json={**three, "acknowledged": True}, headers=ph)
    ).status_code == 409
    double = schedule(["08:00", "20:00"], dose_amount="2")
    assert (
        await api.put(f"{base}/schedule", json={**double, "acknowledged": True}, headers=ph)
    ).status_code == 409

    # With the advising pharmacist recorded, the patient can apply it; it is labelled.
    advised = await api.put(
        f"{base}/schedule",
        json={
            **three,
            "acknowledged": True,
            "advice": {"role": "pharmacist", "name": "Placeholder Pharmacist"},
            "reason": "Pharmacist said to take it three times a day",
        },
        headers=ph,
    )
    assert advised.status_code == 200, advised.text
    assert advised.json()["schedule"]["times_of_day"] == ["08:00:00", "14:00:00", "20:00:00"]
    # The prescription is unchanged.
    assert advised.json()["prescribed"]["times_per_day"] == 2
    history = (await api.get(f"{base}/history", headers=ph)).json()
    last = history[-1]
    assert last["event_type"] == "schedule_changed"
    assert last["advised_by_role"] == "pharmacist"
    assert last["advised_by_name"] == "Placeholder Pharmacist"
    assert "frequency_differs" in last["details"]["findings"]
    assert last["actor_role"] == "patient"


async def test_doctor_confirms_a_change_request(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, dh, med = await setup(api, make_account, session)
    await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    req = await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/change-requests",
        json={
            "kind": "schedule",
            "schedule": schedule(["08:00", "20:00"], dose_amount="2"),
            "message": "Can I take 2?",
        },
        headers=ph,
    )
    assert req.status_code == 201, req.text
    rid = req.json()["id"]
    again = await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/change-requests",
        json={"kind": "stop", "message": "or stop"},
        headers=ph,
    )
    assert again.status_code == 409
    # The patient cannot approve their own request; nothing has changed yet.
    assert (
        await api.post(
            f"{API}/patients/{pid}/medication-change-requests/{rid}/approve", json={}, headers=ph
        )
    ).status_code == 403
    current = (await api.get(f"{API}/patients/{pid}/medications/{med['id']}", headers=ph)).json()
    assert current["schedule"]["dose_amount"] == "1.000"
    assert current["pending_request"]["id"] == rid

    approved = await api.post(
        f"{API}/patients/{pid}/medication-change-requests/{rid}/approve",
        json={"note": "Yes, 2 tablets"},
        headers=dh,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    after = (await api.get(f"{API}/patients/{pid}/medications/{med['id']}", headers=ph)).json()
    assert after["schedule"]["dose_amount"] == "2.000"
    assert after["pending_request"] is None
    history = (
        await api.get(f"{API}/patients/{pid}/medications/{med['id']}/history", headers=ph)
    ).json()
    kinds = [e["event_type"] for e in history]
    assert kinds[-3:] == ["change_requested", "schedule_changed", "change_approved"]
    assert history[-2]["actor_role"] == "doctor"
    assert history[-2]["details"]["confirmed_by"] == "doctor"


async def test_pause_resume_and_discontinue(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session)
    await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    base = f"{API}/patients/{pid}/medications/{med['id']}"
    await api.get(f"{API}/patients/{pid}/doses", headers=ph)  # materialise upcoming doses

    blocked = await api.post(f"{base}/pause", json={"reason": "feeling better"}, headers=ph)
    assert blocked.status_code == 409
    assert "doctor" in blocked.json()["detail"]
    paused = await api.post(
        f"{base}/pause",
        json={"reason": "feeling better", "acknowledged": True, "own_decision": True},
        headers=ph,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    open_doses = await session.scalar(
        select(text("count(*)"))
        .select_from(MedicationDose)
        .where(
            MedicationDose.medication_id == uuid.UUID(med["id"]),
            MedicationDose.status.in_(["scheduled", "snoozed"]),
            MedicationDose.scheduled_at > datetime.now(UTC),
        )
    )
    assert open_doses == 0  # no reminders while paused

    resumed = await api.post(f"{base}/resume", headers=ph)
    assert resumed.json()["status"] == "active"
    assert resumed.json()["schedule"]["times_of_day"] == ["08:00:00", "20:00:00"]

    stopped = await api.post(
        f"{base}/stop",
        json={
            "reason": "side effects",
            "acknowledged": True,
            "advice": {"role": "doctor", "name": "Dr Placeholder Elsewhere"},
        },
        headers=ph,
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"
    assert await events(api, pid, ph, med["id"]) == [
        "created",
        "confirmed",
        "paused",
        "resumed",
        "stopped",
    ]
    # The prescription itself still says what the doctor wrote.
    rx = (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ph)).json()[0]
    assert rx["status"] == "issued"


async def test_course_completes_after_its_end_date(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session, duration_days=3)
    await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    await session.execute(
        text("UPDATE medications SET start_date = :s, end_date = :e WHERE id = :m"),
        {
            "s": date.today() - timedelta(days=5),
            "e": date.today() - timedelta(days=3),
            "m": med["id"],
        },
    )
    listed = (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()
    assert listed[0]["status"] == "completed"
    history = (
        await api.get(f"{API}/patients/{pid}/medications/{med['id']}/history", headers=ph)
    ).json()
    assert history[-1]["event_type"] == "completed"
    assert history[-1]["actor_role"] == "system"


async def test_own_medicines_interval_and_duplicates(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session, generic_name="Samplemycinum")
    add = f"{API}/patients/{pid}/medications/self-reported"

    first = await api.post(
        add,
        json={"name": "Cap. Exampledol", "strength": "500 mg", "times_of_day": ["09:00"]},
        headers=ph,
    )
    assert first.status_code == 201
    # The same medicine (form prefix and punctuation ignored) cannot be added twice.
    dup = await api.post(
        add, json={"name": "exampledol", "strength": "500MG", "times_of_day": ["21:00"]}, headers=ph
    )
    assert dup.status_code == 409
    assert "already on the list" in dup.json()["detail"]
    other_strength = await api.post(
        add,
        json={"name": "Exampledol", "strength": "650 mg", "times_of_day": ["21:00"]},
        headers=ph,
    )
    assert other_strength.status_code == 201
    assert {d["kind"] for d in other_strength.json()["duplicates"]} == {"same_name"}

    # A self-added entry of a prescribed medicine is flagged on both.
    same_as_rx = await api.post(
        add, json={"name": "Samplemycin", "times_of_day": ["10:00"]}, headers=ph
    )
    assert same_as_rx.status_code == 201
    listed = {
        m["id"]: m for m in (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()
    }
    assert [d["medication_id"] for d in listed[med["id"]]["duplicates"]] == [
        same_as_rx.json()["id"]
    ]
    assert "duplicate_noted" in await events(api, pid, ph, same_as_rx.json()["id"])
    by_generic = await api.post(
        add, json={"name": "Samplemycinum", "times_of_day": ["11:00"]}, headers=ph
    )
    assert "same_generic" in {d["kind"] for d in by_generic.json()["duplicates"]}

    # The database itself refuses a duplicate own entry, whatever the code does.
    async with expect_db_error(session, "23505"):
        await session.execute(
            text(
                "INSERT INTO medications (id, patient_id, source, origin, name, strength, status, "
                "confirmed_at, confirmed_by) SELECT gen_random_uuid(), patient_id, source, origin, "
                "'TAB. EXAMPLEDOL', '500 mg', 'active', now(), confirmed_by FROM medications "
                "WHERE id = :m"
            ),
            {"m": first.json()["id"]},
        )

    # Every 8 hours, in elapsed time.
    interval = await api.post(
        add,
        json={
            "name": "Placeholder Drops",
            "schedule_type": "interval",
            "interval_hours": 8,
            "times_of_day": ["06:00"],
        },
        headers=ph,
    )
    assert interval.status_code == 201, interval.text
    doses = [
        d
        for d in (await api.get(f"{API}/patients/{pid}/doses?days=2", headers=ph)).json()
        if d["medication_name"] == "Placeholder Drops"
    ]
    times = sorted(datetime.fromisoformat(d["scheduled_at"]) for d in doses)
    assert times
    assert all(b - a == timedelta(hours=8) for a, b in pairwise(times))


async def test_regimen_changes_need_a_person_and_history_is_append_only(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    pid, ph, _, med = await setup(api, make_account, session)
    await confirm(api, pid, ph, med["id"], ["08:00", "20:00"])
    # No automated process (including AI) may change a regimen: a system actor may only
    # record a completed course or a duplicate note.
    async with expect_db_error(session, CHECK_VIOLATION):
        await session.execute(
            text(
                "INSERT INTO medication_events (id, patient_id, medication_id, event_type, "
                "actor_role) VALUES (gen_random_uuid(), :p, :m, 'schedule_changed', 'system')"
            ),
            {"p": pid, "m": med["id"]},
        )
    async with expect_db_error(session, HI001):
        await session.execute(
            text("UPDATE medication_events SET reason = 'x' WHERE medication_id = :m"),
            {"m": med["id"]},
        )
