"""Prescription management: fields, immutable versions, corrections, document and PDF."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.audit.models import AuditLog
from app.modules.prescriptions.models import Prescription
from tests.db.conftest import CHECK_VIOLATION, Builder, expect_db_error, login
from tests.db.test_authorization import link_doctor
from tests.db.test_doctor_portal import add_patient, make_doctor
from tests.db.test_patient_portal import patient_with_prescription

pytestmark = pytest.mark.integration

API = "/api/v1"
HI001 = "HI001"  # frozen row


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "diagnosis_as_written": "Placeholder assessment as documented",
        "advice": "Placeholder notes and advice",
        "follow_up_on": (date.today() + timedelta(days=14)).isoformat(),
        "follow_up_instructions": "Review after the course",
        "items": [
            {
                "drug_name": "Medicine A",
                "generic_name": "Generic A",
                "strength": "500 mg",
                "dosage_form": "tablet",
                "dose_amount": "1",
                "dose_unit": "tablet",
                "frequency_text": "1-0-1",
                "meal_relation": "after_food",
                "duration_days": 5,
                "instructions": "Placeholder instruction",
            }
        ],
    }
    body.update(overrides)
    return body


async def _issued(api: Any, h: dict[str, str], pid: str, **overrides: Any) -> dict[str, Any]:
    draft = await api.post(
        f"{API}/patients/{pid}/prescriptions", json=_body(**overrides), headers=h
    )
    assert draft.status_code == 201, draft.text
    issued = await api.post(
        f"{API}/patients/{pid}/prescriptions/{draft.json()['id']}/issue", headers=h
    )
    assert issued.status_code == 200, issued.text
    return dict(issued.json())


async def _actions(session: AsyncSession, resource_id: str) -> list[str]:
    rows = await session.scalars(
        select(AuditLog.action).where(AuditLog.resource_id == resource_id).order_by(AuditLog.seq)
    )
    return list(rows.all())


async def test_doctor_creates_a_complete_prescription(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _ = await make_doctor(make_account, session)
    h = await login(api, user.test_email)
    pid = await add_patient(api, h)
    rx = await _issued(api, h, pid)

    assert rx["status"] == "issued"
    assert rx["revision"] == 1
    assert rx["follow_up_on"] == _body()["follow_up_on"]
    assert len(rx["content_sha256"]) == 64
    item = rx["items"][0]
    assert item["generic_name"] == "Generic A"
    assert item["meal_relation"] == "after_food"
    assert item["duration_days"] == 5

    # The follow-up is created from the prescription.
    follow_ups = (await api.get(f"{API}/patients/{pid}/follow-ups", headers=h)).json()
    assert [f["due_date"] for f in follow_ups] == [rx["follow_up_on"]]

    # The document view has prescriber registration details and the patient line.
    doc = (await api.get(f"{API}/patients/{pid}/prescriptions/{rx['id']}", headers=h)).json()
    assert doc["prescriber"]["registration_number"]
    assert doc["patient"]["name"] == "Placeholder Person"
    assert doc["items"][0]["dose"] == "1 tablet"
    assert [v["revision"] for v in doc["versions"]] == [1]
    assert {"prescription.create_draft", "prescription.issue", "prescription.read"} <= set(
        await _actions(session, rx["id"])
    )


async def test_follow_up_needs_a_valid_date(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _ = await make_doctor(make_account, session)
    h = await login(api, user.test_email)
    pid = await add_patient(api, h)
    past = await api.post(
        f"{API}/patients/{pid}/prescriptions",
        json=_body(follow_up_on=(date.today() - timedelta(days=3)).isoformat()),
        headers=h,
    )
    assert past.status_code == 422
    no_date = await api.post(
        f"{API}/patients/{pid}/prescriptions", json=_body(follow_up_on=None), headers=h
    )
    assert no_date.status_code == 422  # instructions without a date


async def test_issued_prescription_is_never_modified_in_place(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _ = await make_doctor(make_account, session)
    h = await login(api, user.test_email)
    pid = await add_patient(api, h)
    rx = await _issued(api, h, pid)

    edit = await api.put(f"{API}/patients/{pid}/prescriptions/{rx['id']}", json=_body(), headers=h)
    assert edit.status_code == 409
    # The database refuses too, whatever the code does.
    async with expect_db_error(session, HI001):
        await session.execute(
            text("UPDATE prescriptions SET advice = 'changed' WHERE id = :id"), {"id": rx["id"]}
        )
    async with expect_db_error(session, HI001):
        await session.execute(
            text("UPDATE prescription_items SET strength = '1 g' WHERE prescription_id = :id"),
            {"id": rx["id"]},
        )


async def test_correction_creates_a_new_version_and_keeps_the_old_one(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph, dh = await patient_with_prescription(api, make_account, session)
    original = (await api.get(f"{API}/patients/{pid}/prescriptions", headers=dh)).json()[0]
    # The patient set up reminders for the original medicine.
    med = (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()[0]
    await api.post(
        f"{API}/patients/{pid}/medications/{med['id']}/confirm",
        json={"times_of_day": ["08:00", "20:00"]},
        headers=ph,
    )

    no_reason = await api.post(
        f"{API}/patients/{pid}/prescriptions/{original['id']}/revisions",
        json={**_body(), "reason": ""},
        headers=dh,
    )
    assert no_reason.status_code == 422
    corrected_body = _body(
        items=[
            {
                "drug_name": "Medicine A",
                "strength": "250 mg",
                "frequency_text": "1-0-1",
                "duration_days": 5,
            }
        ],
        follow_up_on=None,
        follow_up_instructions=None,
    )
    draft = await api.post(
        f"{API}/patients/{pid}/prescriptions/{original['id']}/revisions",
        json={**corrected_body, "reason": "Strength written incorrectly"},
        headers=dh,
    )
    assert draft.status_code == 201, draft.text
    v2 = draft.json()
    assert v2["status"] == "draft"
    assert v2["revision"] == 2
    assert v2["supersedes_prescription_id"] == original["id"]

    # Only one correction at a time; the patient does not see the draft.
    again = await api.post(
        f"{API}/patients/{pid}/prescriptions/{original['id']}/revisions",
        json={**corrected_body, "reason": "Another attempt"},
        headers=dh,
    )
    assert again.status_code == 409
    seen = [
        r["id"] for r in (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ph)).json()
    ]
    assert v2["id"] not in seen

    issued = await api.post(f"{API}/patients/{pid}/prescriptions/{v2['id']}/issue", headers=dh)
    assert issued.status_code == 200, issued.text

    listed = {
        r["id"]: r
        for r in (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ph)).json()
    }
    old, new = listed[original["id"]], listed[v2["id"]]
    assert old["status"] == "superseded"
    assert old["superseded_by_id"] == new["id"]
    # The old version's content is unchanged.
    assert old["items"][0]["drug_name"] == original["items"][0]["drug_name"]
    assert old["content_sha256"] == original["content_sha256"]
    assert new["items"][0]["strength"] == "250 mg"
    assert new["revision_reason"] == "Strength written incorrectly"

    # The old medicine stops (recorded as the doctor's action); the new one awaits the patient.
    meds = {
        m["prescription_item_id"]: m
        for m in (await api.get(f"{API}/patients/{pid}/medications", headers=ph)).json()
    }
    assert meds[original["items"][0]["id"]]["status"] == "stopped"
    assert meds[new["items"][0]["id"]]["status"] == "pending_confirmation"

    # The document shows both versions, and a superseded version cannot be corrected again.
    doc = (await api.get(f"{API}/patients/{pid}/prescriptions/{original['id']}", headers=ph)).json()
    assert [(v["revision"], v["status"]) for v in doc["versions"]] == [
        (1, "superseded"),
        (2, "issued"),
    ]
    assert doc["superseded_by_id"] == new["id"]
    stale = await api.post(
        f"{API}/patients/{pid}/prescriptions/{original['id']}/revisions",
        json={**corrected_body, "reason": "Late correction"},
        headers=dh,
    )
    assert stale.status_code == 409

    assert "prescription.superseded" in await _actions(session, original["id"])
    assert "prescription.revision_started" in await _actions(session, v2["id"])

    # The chain is enforced by the database: a later revision needs a predecessor.
    async with expect_db_error(session, CHECK_VIOLATION):
        await session.execute(
            text(
                "INSERT INTO prescriptions (id, patient_id, source, status, revision, "
                "verification_status, prescriber_doctor_id, revision_reason) "
                "SELECT gen_random_uuid(), :p, 'doctor_issued', 'draft', 2, 'unverified', "
                "prescriber_doctor_id, 'orphan' FROM prescriptions WHERE id = :id"
            ),
            {"p": pid, "id": original["id"]},
        )


async def test_patient_and_caregiver_can_view_and_export_but_not_change(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph, _ = await patient_with_prescription(api, make_account, session)
    rx = (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ph)).json()[0]

    pdf = await api.get(f"{API}/patients/{pid}/prescriptions/{rx['id']}/pdf", headers=ph)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["content-disposition"]
    assert pdf.headers["cache-control"] == "no-store"
    assert "prescription.export" in await _actions(session, rx["id"])

    for method, suffix, body in [
        ("post", "/revisions", {**_body(), "reason": "Patient tries to correct"}),
        ("post", "/cancel", {"reason": "Patient tries to cancel"}),
        ("put", "", _body()),
    ]:
        resp = await api.request(
            method.upper(),
            f"{API}/patients/{pid}/prescriptions/{rx['id']}{suffix}",
            json=body,
            headers=ph,
        )
        assert resp.status_code == 403, suffix

    # A caregiver with view_prescriptions reads and exports; nothing more.
    carer = await make_account()
    invited = await api.post(
        f"{API}/patients/{pid}/caregivers",
        json={
            "caregiver_email": carer.test_email,
            "relationship_type": "child",
            "scopes": ["view_prescriptions"],
        },
        headers=ph,
    )
    ch = await login(api, carer.test_email)
    await api.post(
        f"{API}/caregiver-invitations/{invited.json()['relationship_id']}/accept", headers=ch
    )
    doc = await api.get(f"{API}/patients/{pid}/prescriptions/{rx['id']}", headers=ch)
    assert doc.status_code == 200
    assert doc.json()["patient"] is None  # demographics were not shared with this caregiver
    assert (
        await api.get(f"{API}/patients/{pid}/prescriptions/{rx['id']}/pdf", headers=ch)
    ).status_code == 200
    denied = await api.post(
        f"{API}/patients/{pid}/prescriptions/{rx['id']}/revisions",
        json={**_body(), "reason": "Caregiver tries to correct"},
        headers=ch,
    )
    assert denied.status_code == 403


async def test_only_the_prescriber_can_correct(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, _, _ = await patient_with_prescription(api, make_account, session)
    colleague = await make_account(Role.DOCTOR)
    await link_doctor(
        session, colleague, uuid.UUID(pid), categories=["prescriptions", "demographics"]
    )
    ch = await login(api, colleague.test_email)
    rx = (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ch)).json()[0]
    assert rx["prescribed_by_me"] is False
    resp = await api.post(
        f"{API}/patients/{pid}/prescriptions/{rx['id']}/revisions",
        json={**_body(), "reason": "Not my prescription"},
        headers=ch,
    )
    assert resp.status_code == 403
    row = await session.get(Prescription, uuid.UUID(rx["id"]))
    assert row is not None
    assert row.status.value == "issued"
