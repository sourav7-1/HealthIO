"""Prescription scans end to end, on synthetic images with a scripted model.

Covers: consent gating, the pipeline and its metadata, low-confidence handling, review
and correction trail, conversion without invented values, verifier roles, manual entry,
failures, rejection, immutability of the AI result, and the audit trail.
"""

import hashlib
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import ExtractionFailedError
from app.core.enums import Role
from app.modules.audit.models import AuditLog
from app.modules.extraction.models import PrescriptionScan
from tests.ai_samples import ScriptedExtractor, absent, f, item, reading, synthetic_image
from tests.db.conftest import Builder, expect_db_error, login
from tests.db.test_authorization import link_doctor, patient_id_of
from tests.db.test_doctor_portal import FakeStorage

pytestmark = pytest.mark.integration

API = "/api/v1"
HI001 = "HI001"


@pytest.fixture
def storage(api_app: Any) -> FakeStorage:
    fake = FakeStorage()
    api_app.state.storage = fake
    return fake


@pytest.fixture
def model(api_app: Any) -> ScriptedExtractor:
    scripted = ScriptedExtractor()
    api_app.state.ai_extractor = scripted
    return scripted


async def upload_photo(
    api: Any,
    storage: FakeStorage,
    pid: uuid.UUID | str,
    h: dict[str, str],
    image: bytes | None = None,
) -> str:
    body = image or synthetic_image()
    start = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": "prescription",
            "content_type": "image/png",
            "size_bytes": len(body),
        },
        headers=h,
    )
    assert start.status_code == 201, start.text
    storage.objects[start.json()["fields"]["key"]] = body
    done = await api.post(
        f"{API}/patients/{pid}/documents/{start.json()['document_id']}/complete", headers=h
    )
    assert done.status_code == 200, done.text
    return str(start.json()["document_id"])


async def patient(
    api: Any, make_account: Builder, session: AsyncSession
) -> tuple[Any, uuid.UUID, dict[str, str]]:
    user = await make_account(Role.PATIENT)
    return user, await patient_id_of(session, user), await login(api, user.test_email)


async def allow_ai(api: Any, pid: uuid.UUID, h: dict[str, str]) -> None:
    resp = await api.put(f"{API}/patients/{pid}/ai-consent", json={"granted": True}, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["granted"] is True


def set_op(key: str | None, field: str, action: str, value: str | None = None) -> dict[str, Any]:
    return {"op": "set_field", "item_key": key, "field": field, "action": action, "value": value}


async def actions(session: AsyncSession, resource_id: str) -> list[str]:
    rows = await session.scalars(
        select(AuditLog.action)
        .where(AuditLog.resource_id == uuid.UUID(resource_id))
        .order_by(AuditLog.seq)
    )
    return list(rows.all())


async def test_ai_reading_needs_consent_and_can_be_withdrawn(
    api: Any,
    make_account: Builder,
    session: AsyncSession,
    storage: FakeStorage,
    model: ScriptedExtractor,
) -> None:
    _, pid, h = await patient(api, make_account, session)
    doc = await upload_photo(api, storage, pid, h)
    status = (await api.get(f"{API}/patients/{pid}/ai-consent", headers=h)).json()
    assert status["granted"] is False
    assert status["ai_available"] is True
    denied = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert denied.status_code == 403
    assert model.calls == []  # nothing was sent anywhere

    await allow_ai(api, pid, h)
    withdrawn = await api.put(
        f"{API}/patients/{pid}/ai-consent", json={"granted": False}, headers=h
    )
    assert withdrawn.json()["granted"] is False
    again = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert again.status_code == 403
    audit = set(
        (await session.scalars(select(AuditLog.action).where(AuditLog.patient_id == pid))).all()
    )
    assert {"consent.ai_processing_granted", "consent.ai_processing_withdrawn"} <= audit


async def test_full_flow_low_confidence_correction_and_conversion(
    api: Any,
    make_account: Builder,
    session: AsyncSession,
    storage: FakeStorage,
    model: ScriptedExtractor,
) -> None:
    _, pid, h = await patient(api, make_account, session)
    image = synthetic_image()
    doc = await upload_photo(api, storage, pid, h, image)
    await allow_ai(api, pid, h)
    answer = reading(
        [
            item(
                strength=f("500 mg", 0.45),  # low: must not be pre-filled
                dose=absent(),  # not written: must never be filled in
                instructions=f("take with water", 0.7),  # medium
            ),
            item(
                medicine_name=f("Syp. Exampledryl"),
                strength=absent(),
                dose=f("5 ml"),
                frequency=f("SOS"),
                duration=absent(),
                meal_relation=absent(),
                instructions=f("for cough"),
            ),
        ]
    )
    answer["diagnosis"] = "never kept"
    model.answer = answer

    resp = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert resp.status_code == 201, resp.text
    scan = resp.json()
    assert scan["status"] == "needs_review"
    assert scan["discarded_keys"] == ["diagnosis"]
    assert scan["model_metadata"]["model"] == "scripted-model-1"
    assert scan["model_metadata"]["prompt_version"] == "prescription_extraction_v1"
    assert "reencoded_jpeg_without_metadata" in scan["model_metadata"]["preprocessing"]

    first = scan["items"][0]["fields"]
    assert first["strength"]["ai"]["band"] == "low"
    assert first["strength"]["ai"]["message"] == "Could not confidently read this field."
    assert first["strength"]["review"]["value"] is None
    assert first["dose"]["ai"]["band"] == "absent"
    assert first["dose"]["review"]["value"] is None
    assert first["frequency"]["interpretation"] == "1 in the morning, 1 at night"
    # Every field reports value, confidence, source region and verification status.
    for fld in first.values():
        assert {"value", "confidence", "region"} <= set(fld["ai"])
        assert fld["review"]["status"] == "unverified"

    # Nothing can be saved while critical fields are undecided.
    early = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{scan['id']}/confirm", headers=h
    )
    assert early.status_code == 422
    assert any(i["path"] == "items.i1.strength" for i in scan["issues"])
    empty = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{scan['id']}/review",
        json={"ops": [set_op("i1", "strength", "confirm")]},
        headers=h,
    )
    assert empty.status_code == 422  # nothing to confirm: the low reading is not pre-filled

    reviewed = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{scan['id']}/review",
        json={
            "ops": [
                set_op("i1", "medicine_name", "confirm"),
                set_op("i1", "strength", "set", "250 mg"),  # the photo says 250
                set_op("i1", "dose", "not_on_prescription"),
                set_op("i1", "frequency", "confirm"),
                set_op("i1", "instructions", "confirm"),
                set_op("i2", "medicine_name", "confirm"),
                set_op("i2", "strength", "not_on_prescription"),
                set_op("i2", "dose", "confirm"),
                set_op("i2", "frequency", "confirm"),
            ]
        },
        headers=h,
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["items"][0]["fields"]["strength"]["review"]["status"] == "corrected"
    assert body["items"][0]["fields"]["strength"]["ai"]["value"] == "500 mg"  # AI reading kept
    assert body["items"][0]["fields"]["medicine_name"]["review"]["status"] == "confirmed"
    assert body["issues"] == []

    confirmed = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{scan['id']}/confirm", headers=h
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["scan"]["status"] == "verified"
    assert confirmed.json()["scan"]["verifier_role"] == "patient"
    rx_id = confirmed.json()["prescription_id"]

    rxs = {
        r["id"]: r for r in (await api.get(f"{API}/patients/{pid}/prescriptions", headers=h)).json()
    }
    rx = rxs[rx_id]
    assert rx["source"] == "uploaded"
    assert rx["status"] == "recorded"
    assert rx["verification_status"] == "patient_verified"
    assert rx["external_prescriber_name"] == "Dr. Sample Doctor"
    assert rx["prescribed_on"] == "2026-09-01"
    assert rx["diagnosis_as_written"] is None  # never invented
    a, b = rx["items"]
    assert (a["drug_name"], a["strength"]) == ("Tab. Samplemycin", "250 mg")
    assert a["dose_amount"] is None  # missing dose stays missing
    assert a["dose_unit"] is None
    assert (a["frequency_text"], a["times_per_day"], a["duration_days"]) == ("1-0-1", 2, 5)
    assert a["meal_relation"] == "after_food"
    assert (b["is_prn"], b["prn_reason"], b["strength"]) == (True, "as written: SOS", None)
    meds = (await api.get(f"{API}/patients/{pid}/medications", headers=h)).json()
    assert {m["status"] for m in meds} == {"pending_confirmation"}
    assert {m["origin"] for m in meds} == {"uploaded_prescription_ai"}

    # Stored: original image reference, extracted result, model metadata, status, trail.
    row = await session.get(PrescriptionScan, uuid.UUID(scan["id"]))
    assert row is not None
    assert row.image_sha256 == hashlib.sha256(image).hexdigest()
    stored = json.loads(row.result or "{}")
    assert stored["reading"]["items"][0]["strength"]["value"] == "500 mg"
    assert "diagnosis" not in stored["reading"]
    raw = await session.scalar(
        text("SELECT result FROM prescription_scans WHERE id = :i"), {"i": row.id}
    )
    assert raw is not None
    assert "Samplemycin" not in raw  # encrypted at rest
    trail = json.loads(row.review or "{}")["items"][0]["fields"]["strength"]["history"]
    assert trail[-1]["previous"] is None
    assert trail[-1]["status"] == "corrected"
    assert await actions(session, scan["id"]) == [
        "prescription_scan.created",
        "prescription_scan.read_by_ai",
        "prescription_scan.reviewed",
        "prescription_scan.verified",
    ]
    reviewed_event = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "prescription_scan.reviewed", AuditLog.resource_id == row.id
        )
    )
    assert reviewed_event is not None
    assert "items.i1.strength:corrected" in reviewed_event.changed_fields
    assert "250" not in json.dumps(reviewed_event.context)  # values stay out of the audit log

    # The AI result can never be edited, and a verified scan is final.
    async with expect_db_error(session, HI001):
        await session.execute(
            text("UPDATE prescription_scans SET model = 'other' WHERE id = :i"), {"i": row.id}
        )
    late = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{scan['id']}/review",
        json={"ops": [set_op("i1", "strength", "set", "1 g")]},
        headers=h,
    )
    assert late.status_code == 409


async def test_manual_entry_needs_no_ai_and_no_consent(
    api: Any,
    make_account: Builder,
    session: AsyncSession,
    storage: FakeStorage,
    model: ScriptedExtractor,
) -> None:
    _, pid, h = await patient(api, make_account, session)
    doc = await upload_photo(api, storage, pid, h)
    resp = await api.post(
        f"{API}/patients/{pid}/prescription-scans",
        json={"document_id": doc, "mode": "manual"},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    scan = resp.json()
    assert scan["status"] == "needs_review"
    assert scan["items"] == []
    assert model.calls == []
    base = f"{API}/patients/{pid}/prescription-scans/{scan['id']}"
    added = (await api.post(f"{base}/review", json={"ops": [{"op": "add_item"}]}, headers=h)).json()
    key = added["items"][0]["key"]
    assert added["items"][0]["from_ai"] is False
    await api.post(
        f"{base}/review",
        json={
            "ops": [
                set_op(key, "medicine_name", "set", "Tab. Samplemycin"),
                set_op(key, "strength", "set", "500 mg"),
                set_op(key, "dose", "set", "1 tab"),
                set_op(key, "frequency", "set", "BD"),
            ]
        },
        headers=h,
    )
    done = await api.post(f"{base}/confirm", headers=h)
    assert done.status_code == 200, done.text
    rx = next(
        r
        for r in (await api.get(f"{API}/patients/{pid}/prescriptions", headers=h)).json()
        if r["id"] == done.json()["prescription_id"]
    )
    item_ = rx["items"][0]
    assert (item_["times_per_day"], item_["dose_unit"], item_["duration_days"]) == (
        2,
        "tablet",
        None,
    )
    assert rx["external_prescriber_name"] is None
    meds = (await api.get(f"{API}/patients/{pid}/medications", headers=h)).json()
    assert {m["origin"] for m in meds} == {"uploaded_prescription_typed"}


async def test_failures_are_recorded_and_can_be_retried(
    api: Any,
    make_account: Builder,
    session: AsyncSession,
    storage: FakeStorage,
    model: ScriptedExtractor,
) -> None:
    _, pid, h = await patient(api, make_account, session)
    doc = await upload_photo(api, storage, pid, h)
    await allow_ai(api, pid, h)
    model.error = ExtractionFailedError("invalid_model_output", "The AI answer did not match.")
    failed = (
        await api.post(
            f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
        )
    ).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "invalid_model_output"
    model.error = None
    retried = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert retried.json()["status"] == "needs_review"
    # Only one open scan per photo.
    dup = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert dup.status_code == 409
    rejected = await api.post(
        f"{API}/patients/{pid}/prescription-scans/{retried.json()['id']}/reject",
        json={"reason": "Wrong photo"},
        headers=h,
    )
    assert rejected.json()["status"] == "rejected"
    assert "prescription_scan.failed" in await actions(session, failed["id"])


async def test_ai_disabled_and_non_image_uploads(
    api: Any, api_app: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    api_app.state.ai_extractor = None
    _, pid, h = await patient(api, make_account, session)
    doc = await upload_photo(api, storage, pid, h)
    await allow_ai(api, pid, h)
    off = await api.post(
        f"{API}/patients/{pid}/prescription-scans", json={"document_id": doc}, headers=h
    )
    assert off.status_code == 409
    assert (await api.get(f"{API}/patients/{pid}/ai-consent", headers=h)).json()[
        "ai_available"
    ] is False


async def test_who_may_verify(
    api: Any,
    make_account: Builder,
    session: AsyncSession,
    storage: FakeStorage,
    model: ScriptedExtractor,
) -> None:
    owner, pid, h = await patient(api, make_account, session)
    await allow_ai(api, pid, h)

    async def invite(scopes: list[str]) -> dict[str, str]:
        carer = await make_account()
        rel = await api.post(
            f"{API}/patients/{pid}/caregivers",
            json={
                "caregiver_email": carer.test_email,
                "relationship_type": "child",
                "scopes": scopes,
            },
            headers=h,
        )
        ch = await login(api, carer.test_email)
        await api.post(
            f"{API}/caregiver-invitations/{rel.json()['relationship_id']}/accept", headers=ch
        )
        return ch

    viewer = await invite(["view_reports", "upload_reports"])
    helper = await invite(["view_reports", "upload_reports", "report_health_info"])
    scan = (
        await api.post(
            f"{API}/patients/{pid}/prescription-scans",
            json={"document_id": await upload_photo(api, storage, pid, helper)},
            headers=helper,
        )
    ).json()
    base = f"{API}/patients/{pid}/prescription-scans/{scan['id']}"
    assert (await api.get(base, headers=viewer)).status_code == 200
    blocked = await api.post(
        f"{base}/review", json={"ops": [set_op("i1", "medicine_name", "confirm")]}, headers=viewer
    )
    assert blocked.status_code == 403

    ops = [set_op("i1", f, "confirm") for f in ("medicine_name", "strength", "dose", "frequency")]
    assert (await api.post(f"{base}/review", json={"ops": ops}, headers=helper)).status_code == 200
    done = await api.post(f"{base}/confirm", headers=helper)
    assert done.json()["scan"]["verifier_role"] == "caregiver"
    assert done.json()["scan"]["verification_level"] == "patient_verified"

    # A linked doctor who may edit the chart verifies as doctor-verified.
    doctor = await make_account(Role.DOCTOR)
    await link_doctor(
        session, doctor, pid, categories=["tests_and_reports", "visits_and_notes", "prescriptions"]
    )
    dh = await login(api, doctor.test_email)
    manual = (
        await api.post(
            f"{API}/patients/{pid}/prescription-scans",
            json={"document_id": await upload_photo(api, storage, pid, h), "mode": "manual"},
            headers=dh,
        )
    ).json()
    mbase = f"{API}/patients/{pid}/prescription-scans/{manual['id']}"
    key = (
        await api.post(f"{mbase}/review", json={"ops": [{"op": "add_item"}]}, headers=dh)
    ).json()["items"][0]["key"]
    await api.post(
        f"{mbase}/review",
        json={
            "ops": [
                set_op(key, "medicine_name", "set", "Tab. Samplemycin"),
                set_op(key, "strength", "not_on_prescription"),
                set_op(key, "dose", "not_on_prescription"),
                set_op(key, "frequency", "set", "OD"),
            ]
        },
        headers=dh,
    )
    doc_done = await api.post(f"{mbase}/confirm", headers=dh)
    assert doc_done.status_code == 200, doc_done.text
    assert doc_done.json()["scan"]["verification_level"] == "doctor_verified"

    # Unrelated people see nothing.
    stranger = await make_account(Role.PATIENT)
    sh = await login(api, stranger.test_email)
    assert (await api.get(base, headers=sh)).status_code == 404
    assert owner is not None
