"""Patient portal API: medicines, doses, self-reported information, settings, boundaries."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordSource, Role
from app.modules.clinical.models import (
    ConditionClinicalStatus,
    ConditionVerificationStatus,
    MedicalCondition,
)
from app.modules.patients.models import PatientProfile
from tests.db.conftest import TEST_PASSWORD, Builder, login
from tests.db.test_doctor_portal import make_doctor

pytestmark = pytest.mark.integration

API = "/api/v1"


async def patient_with_prescription(
    api: Any, make_account: Builder, session: AsyncSession
) -> tuple[Any, str, dict[str, str], dict[str, str]]:
    """A patient with an account, linked to a doctor who issued one prescription."""
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
        json={"data_categories": ["medications", "prescriptions", "adherence", "conditions"]},
        headers=ph,
    )
    rx = (
        await api.post(
            f"{API}/patients/{pid}/prescriptions",
            json={
                "items": [
                    {"drug_name": "Medicine A", "frequency_text": "1-0-1", "duration_days": 5}
                ]
            },
            headers=dh,
        )
    ).json()
    assert (
        await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/issue", headers=dh)
    ).status_code == 200
    return patient, pid, ph, dh


async def test_confirm_prescribed_medicine_and_record_doses(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph, _ = await patient_with_prescription(api, make_account, session)
    meds = (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()
    med = meds[0]
    assert med["source"] == "prescription"
    assert med["status"] == "pending_confirmation"
    assert med["prescribed_directions"] == "1-0-1 · for 5 days"  # as the doctor wrote it

    # No doses exist until the patient confirms and chooses times.
    assert (await api.get(f"{API}/patients/{pid}/doses", headers=ph)).json() == []
    no_times = await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/confirm", json={}, headers=ph
    )
    assert no_times.status_code == 422

    now = datetime.now(UTC)
    soon = (now + timedelta(minutes=30)).astimezone().time().replace(second=0, microsecond=0)
    later = (now + timedelta(hours=5)).astimezone().time().replace(second=0, microsecond=0)
    confirmed = await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/confirm",
        json={"times_of_day": [soon.isoformat(), later.isoformat()], "timezone": "UTC"},
        headers=ph,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active"
    assert confirmed.json()["schedule"]["timezone"] == "UTC"

    listed = (await api.get(f"{API}/patients/{pid}/doses?days=2", headers=ph)).json()
    assert len(listed) >= 2
    again = (await api.get(f"{API}/patients/{pid}/doses?days=2", headers=ph)).json()
    assert len(again) == len(listed)  # materialisation is idempotent
    dose = next(d for d in listed if d["status"] == "scheduled")
    assert dose["source"] == "prescription"

    snoozed = await api.post(
        f"{API}/patients/{pid}/doses/{dose['id']}/snooze", json={"minutes": 15}, headers=ph
    )
    assert snoozed.json()["status"] == "snoozed"
    assert snoozed.json()["snoozed_until"] is not None
    taken = await api.post(f"{API}/patients/{pid}/doses/{dose['id']}/take", json={}, headers=ph)
    assert taken.json()["status"] == "taken"
    assert taken.json()["snoozed_until"] is None
    twice = await api.post(f"{API}/patients/{pid}/doses/{dose['id']}/take", json={}, headers=ph)
    assert twice.status_code == 409

    other = next(d for d in listed if d["id"] != dose["id"] and d["status"] == "scheduled")
    skipped = await api.post(
        f"{API}/patients/{pid}/doses/{other['id']}/skip", json={"reason": "felt unwell"}, headers=ph
    )
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["skip_reason"] == "felt unwell"

    adherence = (await api.get(f"{API}/patients/{pid}/adherence", headers=ph)).json()
    assert adherence["total_recorded"] == 2


async def test_patient_cannot_change_or_stop_doctor_prescribed_medicine(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph, _ = await patient_with_prescription(api, make_account, session)
    med = (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()[0]
    await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/confirm",
        json={"times_of_day": ["08:00"]},
        headers=ph,
    )
    # Stopping a prescribed medicine is never silent: it needs the warning acknowledged
    # and a doctor/pharmacist or the patient's own recorded decision (test_medications.py).
    stop = await api.post(f"{API}/patients/{pid}/medications/{med['id']}/stop", json={}, headers=ph)
    assert stop.status_code == 409
    assert "doctor" in stop.json()["detail"]
    rx = (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ph)).json()[0]
    edit = await api.put(
        f"{API}/patients/{pid}/prescriptions/{rx['id']}",
        json={"items": [{"drug_name": "Changed"}]},
        headers=ph,
    )
    assert edit.status_code == 403
    assert (
        await api.post(f"{API}/patients/{pid}/medications", json={"name": "x"}, headers=ph)
    ).status_code == 403

    # Reminder times are the patient's own choice and can change.
    changed = await api.put(
        f"{API}/patients/{pid}/medications/{med['id']}/reminder-times",
        json={"times_of_day": ["07:30", "19:30"]},
        headers=ph,
    )
    assert changed.status_code == 200
    assert changed.json()["schedule"]["times_of_day"] == ["07:30:00", "19:30:00"]
    assert changed.json()["prescribed_directions"] == med["prescribed_directions"]  # untouched
    history = await session.scalar(
        text("SELECT count(*) FROM medication_schedules WHERE medication_id = :m"), {"m": med["id"]}
    )
    assert history == 2  # the old schedule is kept, ended


async def test_self_reported_medicines_are_labelled_and_manageable(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient = await make_account(Role.PATIENT)
    ph = await login(api, patient.test_email)
    pid = (await api.get(f"{API}/me", headers=ph)).json()["patient_profile_id"]
    added = await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={"name": "Vitamin placeholder", "times_of_day": ["09:00"]},
        headers=ph,
    )
    assert added.status_code == 201
    assert added.json()["source"] == "self_reported"
    prn = await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={"name": "As-needed placeholder", "is_prn": True},
        headers=ph,
    )
    assert prn.json()["schedule"]["type"] == "as_needed"
    logged = await api.post(
        f"{API}/patients/{pid}/medications/{prn.json()['id']}/as-needed-dose", json={}, headers=ph
    )
    assert logged.status_code == 201
    assert logged.json()["as_needed"] is True

    stopped = await api.post(
        f"{API}/patients/{pid}/medications/{added.json()['id']}/stop",
        json={"reason": "finished"},
        headers=ph,
    )
    assert stopped.json()["status"] == "stopped"


async def test_self_reported_history_cannot_touch_doctor_entries(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph, _ = await patient_with_prescription(api, make_account, session)
    doctor_condition = MedicalCondition(
        patient_id=uuid.UUID(pid),
        name="Doctor documented",
        clinical_status=ConditionClinicalStatus.ACTIVE,
        verification_status=ConditionVerificationStatus.CONFIRMED,
        source=RecordSource.DOCTOR,
    )
    session.add(doctor_condition)
    await session.flush()

    mine = await api.post(
        f"{API}/patients/{pid}/self-reported/allergies",
        json={"substance": "Placeholder allergen", "reaction": "rash"},
        headers=ph,
    )
    assert mine.status_code == 201
    assert mine.json()["source"] == "patient"
    assert mine.json()["verification_status"] == "unconfirmed"
    assert (
        await api.delete(
            f"{API}/patients/{pid}/self-reported/allergies/{mine.json()['id']}", headers=ph
        )
    ).status_code == 204

    # A doctor-documented condition cannot be removed or edited by the patient.
    blocked = await api.delete(
        f"{API}/patients/{pid}/self-reported/conditions/{doctor_condition.id}", headers=ph
    )
    assert blocked.status_code == 403
    assert (
        await api.post(
            f"{API}/patients/{pid}/conditions",
            json={"name": "x", "verification_status": "confirmed"},
            headers=ph,
        )
    ).status_code == 403


async def test_caregiver_dose_logging_follows_scopes(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient = await make_account(Role.PATIENT)
    ph = await login(api, patient.test_email)
    pid = (await api.get(f"{API}/me", headers=ph)).json()["patient_profile_id"]
    await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={
            "name": "Placeholder",
            "times_of_day": [(datetime.now(UTC) + timedelta(minutes=20)).strftime("%H:%M")],
            "timezone": "UTC",
        },
        headers=ph,
    )
    dose_id = next(
        d["id"]
        for d in (await api.get(f"{API}/patients/{pid}/doses?days=2", headers=ph)).json()
        if d["status"] == "scheduled"
    )
    for scopes, expected in ((["view_medications"], 403), (["view_medications", "log_doses"], 200)):
        carer = await make_account()
        rel = (
            await api.post(
                f"{API}/patients/{pid}/caregivers",
                json={
                    "caregiver_email": carer.test_email,
                    "relationship_type": "child",
                    "scopes": scopes,
                },
                headers=ph,
            )
        ).json()["relationship_id"]
        ch = await login(api, carer.test_email)
        await api.post(f"{API}/caregiver-invitations/{rel}/accept", headers=ch)
        resp = await api.post(f"{API}/patients/{pid}/doses/{dose_id}/take", json={}, headers=ch)
        assert resp.status_code == expected, scopes
    # Caregivers cannot add self-reported medicines on the patient's behalf.
    assert (
        await api.post(
            f"{API}/patients/{pid}/medications/self-reported",
            json={"name": "x", "times_of_day": ["08:00"]},
            headers=ch,
        )
    ).status_code == 403


async def test_reminder_preferences_emergency_profile_and_settings(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient = await make_account(Role.PATIENT)
    ph = await login(api, patient.test_email)
    pid = (await api.get(f"{API}/me", headers=ph)).json()["patient_profile_id"]

    prefs = (await api.get(f"{API}/patients/{pid}/reminder-preferences", headers=ph)).json()
    assert prefs["show_medicine_names"] is False  # private by default
    bad = await api.put(
        f"{API}/patients/{pid}/reminder-preferences",
        json={**prefs, "channel_push": False, "channel_sms": False, "channel_email": False},
        headers=ph,
    )
    assert bad.status_code == 422
    saved = await api.put(
        f"{API}/patients/{pid}/reminder-preferences",
        json={
            **prefs,
            "quiet_hours_start": "22:00:00",
            "quiet_hours_end": "07:00:00",
            "default_snooze_minutes": 15,
        },
        headers=ph,
    )
    assert saved.json()["default_snooze_minutes"] == 15

    ep = await api.put(
        f"{API}/patients/{pid}/emergency-profile",
        json={"critical_information": "placeholder note", "show_medications": True},
        headers=ph,
    )
    assert ep.status_code == 200
    assert ep.json()["last_reviewed_at"] is not None
    c = await api.post(
        f"{API}/patients/{pid}/emergency-contacts",
        json={"name": "Placeholder Contact", "phone": "+91 90000 00000"},
        headers=ph,
    )
    assert c.json()["priority"] == 1
    assert (
        await api.post(
            f"{API}/patients/{pid}/emergency-contacts",
            json={"name": "X", "phone": "call me"},
            headers=ph,
        )
    ).status_code == 422
    raw_phone = await session.scalar(
        text("SELECT phone FROM emergency_contacts WHERE id = :i"), {"i": c.json()["id"]}
    )
    assert "90000" not in raw_phone  # encrypted at rest

    profile = await api.patch(
        f"{API}/patients/{pid}/profile",
        json={"blood_group": "B+", "timezone": "Asia/Kolkata"},
        headers=ph,
    )
    assert profile.json()["blood_group"] == "B+"
    assert (
        await api.patch(f"{API}/patients/{pid}/profile", json={"timezone": "Mars/Base"}, headers=ph)
    ).status_code == 422

    updated = await api.patch(f"{API}/me", json={"display_name": "Renamed Placeholder"}, headers=ph)
    assert updated.json()["display_name"] == "Renamed Placeholder"


async def test_password_change_keeps_this_device_and_signs_out_others(
    api: Any, make_account: Builder
) -> None:
    patient = await make_account(Role.PATIENT)
    other_device = await login(api, patient.test_email)
    this_device = await login(api, patient.test_email)
    wrong = await api.post(
        f"{API}/auth/password/change",
        json={"current_password": "not it at all", "new_password": "a brand new passphrase"},
        headers=this_device,
    )
    assert wrong.status_code == 401
    ok = await api.post(
        f"{API}/auth/password/change",
        json={"current_password": TEST_PASSWORD, "new_password": "a brand new passphrase"},
        headers=this_device,
    )
    assert ok.json() == {"other_sessions_signed_out": 1}
    assert (await api.get(f"{API}/me", headers=this_device)).status_code == 200
    assert (await api.get(f"{API}/me", headers=other_device)).status_code == 401


def test_schedule_validation_rejects_empty_schedule() -> None:
    from app.core.errors import ValidationFailedError
    from app.modules.medications.models import ScheduleType
    from app.modules.medications.regimen import ScheduleSpec, validate_spec

    with pytest.raises(ValidationFailedError):
        validate_spec(ScheduleSpec(ScheduleType.FIXED_TIMES, times=()))
    clean = validate_spec(
        ScheduleSpec(ScheduleType.FIXED_TIMES, times=(time(8, 0, 30), time(8, 0)))
    )
    assert clean.times == (time(8, 0),)
