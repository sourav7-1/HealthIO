"""Medication safety through the API, with a synthetic placeholder dataset (no real drug
facts): automatic checks after changes, stored warnings with source/severity/timestamps/
review status, doctor review and patient acknowledgement, resolution, prescription
preview, consent-aware wording, and that nothing is reported without a dataset."""

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.safety.reference import import_dataset
from app.modules.timeline.models import RecordVersion
from tests.db.conftest import Builder
from tests.db.test_doctor_portal import make_doctor
from tests.db.test_records_timeline import ALL, _link, _patient

pytestmark = pytest.mark.integration

API = "/api/v1"
HEADLINE = "Potential issue detected. Please confirm with a doctor/pharmacist."
DATASET: dict[str, Any] = {
    "dataset": {
        "key": "placeholder-set",
        "publisher": "nlm_rxnorm",
        "name": "Placeholder Reference Set",
        "version": "2026.09",
        "url": "https://www.nlm.nih.gov/placeholder",
        "license": "Placeholder licence for tests",
        "reviewed_by": "Placeholder Reviewer",
        "reviewed_on": "2026-09-01",
    },
    "products": [
        {"name": "Placeholder Alpha", "ingredients": ["ingredient alpha"]},
        {"name": "Placeholder Beta", "ingredients": ["ingredient beta"]},
    ],
    "interactions": [
        {
            "a": "ingredient alpha",
            "b": "ingredient beta",
            "severity": "major",
            "description": "Placeholder source wording.",
            "ref": "PH-1",
        }
    ],
    "allergy_classes": [],
}


async def _self_med(
    api: Any, h: dict[str, str], pid: str, name: str, strength: str | None = None
) -> str:
    resp = await api.post(
        f"{API}/patients/{pid}/medications/self-reported",
        json={"name": name, "strength": strength, "times_of_day": ["08:00"]},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _warnings(api: Any, h: dict[str, str], pid: str, **params: Any) -> list[dict[str, Any]]:
    resp = await api.get(f"{API}/patients/{pid}/safety-warnings", params=params, headers=h)
    assert resp.status_code == 200, resp.text
    return list(resp.json())


async def test_nothing_is_invented_without_a_dataset(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    _, pid, ph = await _patient(api, make_account, session)
    await _self_med(api, ph, pid, "Placeholder Alpha")
    await _self_med(api, ph, pid, "Placeholder Beta")
    assert await _warnings(api, ph, pid) == []
    assert (await api.get(f"{API}/safety/reference-datasets", headers=ph)).json() == []


async def test_interaction_found_stored_reviewed_and_resolved(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    await import_dataset(session, DATASET)
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    await _self_med(api, ph, pid, "Placeholder Alpha")
    beta = await _self_med(api, ph, pid, "Placeholder Beta")  # triggers the check

    (w,) = await _warnings(api, ph, pid)
    assert w["headline"] == HEADLINE
    assert w["kind"] == "interaction"
    assert w["severity"] == "serious"
    assert w["source_severity"] == "major"
    assert w["source_type"] == "reference_dataset"
    assert w["source_name"] == "Placeholder Reference Set"
    assert w["source_version"] == "2026.09"
    assert w["status"] == "open"
    assert w["review_status"] == "unreviewed"
    assert w["detected_at"]
    assert "Placeholder source wording" not in w["detail"]  # patients get plain words
    assert "don't stop" in w["detail"].lower()
    (dw,) = await _warnings(api, dh, pid)
    assert "Placeholder source wording." in dw["detail"]  # doctors see the source's wording

    # Re-checking the same situation doesn't duplicate it.
    again = await api.post(f"{API}/patients/{pid}/safety-warnings/recheck", headers=ph)
    assert [x["id"] for x in again.json()] == [w["id"]]

    ack = await api.post(
        f"{API}/patients/{pid}/safety-warnings/{w['id']}/review", json={}, headers=ph
    )
    assert ack.json()["review_status"] == "acknowledged"
    no_note = await api.post(
        f"{API}/patients/{pid}/safety-warnings/{w['id']}/review", json={}, headers=dh
    )
    assert no_note.status_code == 422
    reviewed = await api.post(
        f"{API}/patients/{pid}/safety-warnings/{w['id']}/review",
        json={"note": "Aware; discussed with patient"},
        headers=dh,
    )
    assert reviewed.json()["review_status"] == "reviewed"
    assert reviewed.json()["reviewer_role"] == "doctor"
    downgrade = await api.post(
        f"{API}/patients/{pid}/safety-warnings/{w['id']}/review", json={}, headers=ph
    )
    assert downgrade.status_code == 409
    versions = (
        await session.scalars(
            select(RecordVersion).where(RecordVersion.record_id == uuid.UUID(w["id"]))
        )
    ).all()
    assert len(versions) == 2  # acknowledged, then reviewed

    # Stopping one medicine resolves the warning; it stays as history.
    stopped = await api.post(
        f"{API}/patients/{pid}/medications/{beta}/stop", json={"reason": "finished"}, headers=ph
    )
    assert stopped.status_code == 200, stopped.text
    assert await _warnings(api, ph, pid) == []
    (hist,) = await _warnings(api, ph, pid, include_resolved=True)
    assert hist["status"] == "resolved"
    assert hist["resolved_at"]


async def test_duplicates_allergies_and_consent_aware_wording(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    await import_dataset(session, DATASET)
    patient, pid, ph = await _patient(api, make_account, session)
    narrow, _ = await make_doctor(make_account, session)
    nh = await _link(api, session, patient, narrow, ["medications"])
    await _self_med(api, ph, pid, "Placeholder Gamma", "1 unit")
    await _self_med(api, ph, pid, "Placeholder-Gamma", "2 units")
    allergy = await api.post(
        f"{API}/patients/{pid}/self-reported/allergies",
        json={"substance": "Ingredient Alpha", "category": "medication"},
        headers=ph,
    )
    assert allergy.status_code == 201
    await _self_med(api, ph, pid, "Placeholder Alpha")
    kinds = {w["kind"]: w for w in await _warnings(api, ph, pid)}
    assert set(kinds) == {"duplicate_medication", "allergy"}
    assert kinds["allergy"]["severity"] == "serious"
    assert "Ingredient Alpha" in kinds["allergy"]["detail"]
    # A doctor without consent for medical history sees that something conflicts, not what.
    narrow_view = {w["kind"]: w for w in await _warnings(api, nh, pid)}
    assert "Ingredient Alpha" not in narrow_view["allergy"]["detail"]
    assert "isn't shared with you" in narrow_view["allergy"]["detail"]
    assert all(not s.startswith("allergy:") for s in narrow_view["allergy"]["subjects"])


async def test_prescription_preview_before_issue_then_stored_after(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    await import_dataset(session, DATASET)
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    await _self_med(api, ph, pid, "Placeholder Alpha")
    rx = (
        await api.post(
            f"{API}/patients/{pid}/prescriptions",
            json={
                "items": [
                    {
                        "drug_name": "Placeholder Beta",
                        "frequency_text": "1-0-1",
                        "times_per_day": 3,
                        "dose_amount": "1",
                        "dose_unit": "tablet",
                        "duration_days": 5,
                        "quantity": "6",
                    }
                ]
            },
            headers=dh,
        )
    ).json()
    preview = await api.post(
        f"{API}/patients/{pid}/prescriptions/{rx['id']}/safety-check", headers=dh
    )
    assert preview.status_code == 200, preview.text
    found = {f["kind"] for f in preview.json()["findings"]}
    assert found == {"interaction", "inconsistent_prescription"}
    assert preview.json()["datasets"] == ["Placeholder Reference Set"]
    assert all(f["headline"] == HEADLINE for f in preview.json()["findings"])
    assert await _warnings(api, dh, pid) == []  # a preview stores nothing
    assert (
        await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/safety-check", headers=ph)
    ).status_code == 403

    issued = await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/issue", headers=dh)
    assert issued.status_code == 200, issued.text
    stored = {w["kind"] for w in await _warnings(api, dh, pid)}
    assert {"interaction", "inconsistent_prescription"} <= stored


async def test_dataset_versions_replace_each_other(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    await import_dataset(session, DATASET)
    newer = DATASET | {"dataset": DATASET["dataset"] | {"version": "2026.10"}}
    await import_dataset(session, newer)
    _, _, ph = await _patient(api, make_account, session)
    listed = (await api.get(f"{API}/safety/reference-datasets", headers=ph)).json()
    assert [(d["key"], d["version"]) for d in listed] == [("placeholder-set", "2026.10")]
    assert listed[0]["reviewed_on"] == date(2026, 9, 1).isoformat()
