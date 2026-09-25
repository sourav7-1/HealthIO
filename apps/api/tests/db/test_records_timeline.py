"""Medical record timeline (Phase 11) and tests & reports (Phase 12), through the API.

Covers: chronological order, filters and pagination; permissions and consent per record
type; versioned corrections (and that the database versions even raw SQL edits); file
type, size, active-content and malware checks; report upload, review, status and
per-report sharing; audit events. Placeholder values only.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.core.malware import ScanResult, Verdict
from app.modules.audit.models import AuditLog
from app.modules.patients.models import PatientProfile
from app.modules.records import service as records
from app.modules.timeline.models import RecordVersion
from tests.db.conftest import Builder, expect_db_error, login
from tests.db.test_caregivers import _invite_and_accept
from tests.db.test_doctor_portal import PDF, FakeStorage, make_doctor

pytestmark = pytest.mark.integration

API = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ALL = [
    "demographics",
    "conditions",
    "allergies",
    "medications",
    "prescriptions",
    "visits_and_notes",
    "tests_and_reports",
    "documents",
    "adherence",
    "appointments",
]


@pytest.fixture
def storage(api_app: Any) -> FakeStorage:
    fake = FakeStorage()
    api_app.state.storage = fake
    return fake


async def _link(
    api: Any,
    session: AsyncSession,
    patient: Any,
    doctor: Any,
    categories: list[str],
) -> dict[str, str]:
    dh, ph = await login(api, doctor.test_email), await login(api, patient.test_email)
    resp = await api.post(
        f"{API}/doctor/patients/connect", json={"patient_email": patient.test_email}, headers=dh
    )
    assert resp.status_code == 202, resp.text
    rel = (await api.get(f"{API}/me/doctor-requests", headers=ph)).json()[0]["relationship_id"]
    ok = await api.post(
        f"{API}/me/doctor-requests/{rel}/accept", json={"data_categories": categories}, headers=ph
    )
    assert ok.status_code == 204, ok.text
    return dh


async def _patient(
    api: Any, make_account: Builder, session: AsyncSession
) -> tuple[Any, str, dict[str, str]]:
    patient = await make_account(Role.PATIENT)
    pid = str(
        await session.scalar(select(PatientProfile.id).where(PatientProfile.user_id == patient.id))
    )
    return patient, pid, await login(api, patient.test_email)


async def _upload(
    api: Any,
    storage: FakeStorage,
    h: dict[str, str],
    pid: str,
    body: bytes = PDF,
    *,
    content_type: str = "application/pdf",
    document_type: str = "lab_report",
    **extra: Any,
) -> Any:
    started = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": document_type,
            "content_type": content_type,
            "size_bytes": len(body),
            **extra,
        },
        headers=h,
    )
    assert started.status_code == 201, started.text
    doc_id = started.json()["document_id"]
    storage.objects[started.json()["fields"]["key"]] = body
    return await api.post(f"{API}/patients/{pid}/documents/{doc_id}/complete", headers=h)


async def _timeline(api: Any, h: dict[str, str], pid: str, **params: Any) -> dict[str, Any]:
    resp = await api.get(f"{API}/patients/{pid}/timeline", params=params, headers=h)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def _actions(session: AsyncSession, pid: str) -> list[str]:
    rows = await session.scalars(
        select(AuditLog.action).where(AuditLog.patient_id == uuid.UUID(pid)).order_by(AuditLog.seq)
    )
    return list(rows.all())


# --- timeline -----------------------------------------------------------------------------


async def test_timeline_is_chronological_filterable_and_paginated(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, dprof = await make_doctor(make_account, session)
    other, oprof = await make_doctor(make_account, session)
    dprof.primary_specialty = "Specialty A"
    oprof.primary_specialty = "Specialty B"
    await session.flush()
    dh = await _link(api, session, patient, doctor, ALL)
    oh = await _link(api, session, patient, other, ALL)

    now = datetime.now(UTC)
    visit = (
        await api.post(
            f"{API}/patients/{pid}/visits",
            json={"visit_type": "in_person", "started_at": (now - timedelta(days=30)).isoformat()},
            headers=dh,
        )
    ).json()
    await api.post(
        f"{API}/patients/{pid}/symptoms/documented",
        json={"symptom": "Placeholder symptom A", "visit_id": visit["id"], "severity": "mild"},
        headers=dh,
    )
    await api.post(
        f"{API}/patients/{pid}/conditions",
        json={
            "name": "Placeholder assessment",
            "verification_status": "provisional",
            "visit_id": visit["id"],
        },
        headers=dh,
    )
    await api.post(
        f"{API}/patients/{pid}/test-orders",
        json={"tests": ["Test A"], "clinical_indication": "Placeholder reason"},
        headers=oh,
    )
    reported = await api.post(
        f"{API}/patients/{pid}/symptoms",
        json={
            "symptom": "Placeholder symptom B",
            "onset_date": (date.today() - timedelta(days=2)).isoformat(),
        },
        headers=ph,
    )
    assert reported.status_code == 201, reported.text
    assert reported.json()["source"] == "patient"
    doc = await _upload(
        api,
        storage,
        ph,
        pid,
        document_type="vaccination_record",
        title="Placeholder card",
        document_date=(date.today() - timedelta(days=400)).isoformat(),
    )
    assert doc.status_code == 200, doc.text

    page = await _timeline(api, ph, pid)
    items = page["items"]
    kinds = {e["kind"] for e in items}
    assert {"visit", "symptom", "assessment", "test_order", "document"} <= kinds
    stamps = [e["at"] for e in items]
    assert stamps == sorted(stamps, reverse=True), "newest first"
    assert items[-1]["kind"] == "document"
    assert items[-1]["date_only"] is True
    oldest = await _timeline(api, ph, pid, order="oldest")
    assert [e["key"] for e in oldest["items"]] == [e["key"] for e in reversed(items)]

    # Facets describe what the caller may see.
    facet_kinds = {f["value"]: f["count"] for f in page["facets"]["kinds"]}
    assert facet_kinds["symptom"] == 2
    assert {d["name"] for d in page["facets"]["doctors"]} == {
        dprof.display_name,
        oprof.display_name,
    }
    assert {s["value"] for s in page["facets"]["specialties"]} == {"Specialty A", "Specialty B"}

    # Filters: record type, doctor, specialty, date.
    only_symptoms = await _timeline(api, ph, pid, kind="symptom")
    assert {e["kind"] for e in only_symptoms["items"]} == {"symptom"}
    assert only_symptoms["total"] == 2
    by_doctor = await _timeline(api, ph, pid, doctor_id=str(oprof.id))
    assert {e["kind"] for e in by_doctor["items"]} == {"test_order"}
    by_specialty = await _timeline(api, ph, pid, specialty="specialty a")
    assert by_specialty["items"]
    assert all(e["doctor"]["specialty"] == "Specialty A" for e in by_specialty["items"])
    recent = await _timeline(api, ph, pid, date_from=(date.today() - timedelta(days=7)).isoformat())
    assert all(
        e["at"][:10] >= (date.today() - timedelta(days=7)).isoformat() for e in recent["items"]
    )
    assert "visit" not in {e["kind"] for e in recent["items"]}
    bad = await api.get(
        f"{API}/patients/{pid}/timeline",
        params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
        headers=ph,
    )
    assert bad.status_code == 422

    # Pagination: a cursor chain returns every item once, in order.
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        chunk = await _timeline(api, ph, pid, **params)
        seen += [e["key"] for e in chunk["items"]]
        cursor = chunk["next_cursor"]
        if not cursor:
            break
    assert seen == [e["key"] for e in items]
    assert "patient.timeline_view" in await _actions(session, pid)


async def test_timeline_respects_consent_and_relationships(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    full, _ = await make_doctor(make_account, session)
    narrow, _ = await make_doctor(make_account, session)
    stranger, _ = await make_doctor(make_account, session)
    fh = await _link(api, session, patient, full, ALL)
    nh = await _link(api, session, patient, narrow, ["medications"])
    visit = (
        await api.post(
            f"{API}/patients/{pid}/visits", json={"visit_type": "teleconsult"}, headers=fh
        )
    ).json()
    await api.post(f"{API}/patients/{pid}/symptoms", json={"symptom": "Placeholder"}, headers=ph)
    await _upload(api, storage, fh, pid, document_type="discharge_summary", visit_id=visit["id"])

    narrow_kinds = {e["kind"] for e in (await _timeline(api, nh, pid))["items"]}
    assert narrow_kinds.isdisjoint({"visit", "symptom", "document", "note", "report"})
    # History of records the caller cannot see is hidden.
    hidden = await api.get(f"{API}/patients/{pid}/timeline/visit/{visit['id']}/history", headers=nh)
    assert hidden.status_code == 404
    sh = await login(api, stranger.test_email)
    assert (await api.get(f"{API}/patients/{pid}/timeline", headers=sh)).status_code == 404
    patient_kinds = {e["kind"] for e in (await _timeline(api, ph, pid))["items"]}
    assert {"visit", "symptom", "document"} <= patient_kinds


# --- versioning ---------------------------------------------------------------------------


async def test_symptom_corrections_are_versioned_and_side_restricted(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    s = (
        await api.post(
            f"{API}/patients/{pid}/symptoms",
            json={"symptom": "Placeholder wording", "severity": "mild"},
            headers=ph,
        )
    ).json()

    no_reason = await api.patch(
        f"{API}/patients/{pid}/symptoms/{s['id']}",
        json={"version": 1, "severity": "severe"},
        headers=ph,
    )
    assert no_reason.status_code == 422
    fixed = await api.patch(
        f"{API}/patients/{pid}/symptoms/{s['id']}",
        json={
            "version": 1,
            "reason": "picked the wrong level",
            "severity": "moderate",
            "symptom": "Placeholder wording fixed",
        },
        headers=ph,
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["version"] == 2
    assert fixed.json()["corrected"] is True
    stale = await api.patch(
        f"{API}/patients/{pid}/symptoms/{s['id']}",
        json={"version": 1, "reason": "another change", "severity": "mild"},
        headers=ph,
    )
    assert stale.status_code == 409
    # A doctor cannot rewrite what the patient reported.
    by_doctor = await api.patch(
        f"{API}/patients/{pid}/symptoms/{s['id']}",
        json={"version": 2, "reason": "doctor edit", "severity": "mild"},
        headers=dh,
    )
    assert by_doctor.status_code == 403

    history = (
        await api.get(f"{API}/patients/{pid}/timeline/symptom/{s['id']}/history", headers=dh)
    ).json()["entries"]
    assert [e["action"] for e in history] == ["created", "changed"]
    assert history[0]["actor"]["role"] == "patient"
    change = history[1]
    assert change["reason"] == "picked the wrong level"
    fields = {c["field"]: (c["before"], c["after"]) for c in change["changes"]}
    assert fields["severity"] == ("mild", "moderate")
    # Encrypted text is decrypted for the history view (stored encrypted in the version).
    assert fields["symptom"] == ("Placeholder wording", "Placeholder wording fixed")
    raw = await session.scalar(
        select(RecordVersion.snapshot).where(RecordVersion.record_id == uuid.UUID(s["id"]))
    )
    assert raw is not None
    assert "Placeholder wording" not in str(raw)

    item = next(
        e
        for e in (await _timeline(api, ph, pid, kind="symptom"))["items"]
        if e["resource_id"] == s["id"]
    )
    assert item["amended"] is True
    assert item["has_history"] is True
    assert "symptom.correct" in await _actions(session, pid)


async def test_database_versions_even_raw_edits_and_versions_are_append_only(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    visit = (
        await api.post(f"{API}/patients/{pid}/visits", json={"visit_type": "in_person"}, headers=dh)
    ).json()
    cond = (
        await api.post(
            f"{API}/patients/{pid}/conditions",
            json={
                "name": "Placeholder",
                "verification_status": "provisional",
                "visit_id": visit["id"],
            },
            headers=dh,
        )
    ).json()
    # Bypass the API entirely: the change is still recorded.
    await session.execute(
        text("UPDATE medical_conditions SET clinical_status = 'resolved' WHERE id = :id"),
        {"id": cond["id"]},
    )
    versions = (
        await session.scalars(
            select(RecordVersion).where(RecordVersion.record_id == uuid.UUID(cond["id"]))
        )
    ).all()
    assert len(versions) == 1
    assert versions[0].changed_columns == ["clinical_status"]
    history = (
        await api.get(f"{API}/patients/{pid}/timeline/assessment/{cond['id']}/history", headers=ph)
    ).json()["entries"]
    assert history[-1]["changes"][0] == {
        "field": "clinical_status",
        "before": "active",
        "after": "resolved",
    }
    async with expect_db_error(session, "HI001"):
        await session.execute(text("UPDATE record_versions SET reason = 'x'"))
    async with expect_db_error(session, "HI001"):
        await session.execute(text("DELETE FROM record_versions"))


# --- files --------------------------------------------------------------------------------


class FakeScanner:
    name = "fake"

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        self.scanned = 0

    async def scan(self, data: bytes) -> ScanResult:
        self.scanned += 1
        return ScanResult(
            self.verdict,
            self.name,
            signature="Test-Signature" if self.verdict == Verdict.INFECTED else None,
        )


async def test_uploads_are_type_checked_scanned_and_audited(
    api: Any, api_app: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    _, pid, ph = await _patient(api, make_account, session)
    # Reports accept PDF, JPEG and PNG only.
    webp = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={"document_type": "lab_report", "content_type": "image/webp", "size_bytes": 10},
        headers=ph,
    )
    assert webp.status_code == 422
    too_big = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": "lab_report",
            "content_type": "application/pdf",
            "size_bytes": 10**9,
        },
        headers=ph,
    )
    assert too_big.status_code == 422
    assert (await _upload(api, storage, ph, pid, PNG, content_type="image/png")).status_code == 200
    # Declared PDF that is not one; PDF with scripts.
    assert (await _upload(api, storage, ph, pid, PNG)).status_code == 422
    scripted = PDF.replace(b"%%EOF", b"<< /OpenAction << /S /JavaScript >> >>\n%%EOF")
    assert (await _upload(api, storage, ph, pid, scripted)).status_code == 422

    api_app.state.scanner = FakeScanner(Verdict.INFECTED)
    infected = await _upload(api, storage, ph, pid)
    assert infected.status_code == 422
    assert "malware" in infected.json()["detail"]

    api_app.state.scanner = FakeScanner(Verdict.ERROR)
    waiting = await _upload(api, storage, ph, pid)
    assert waiting.json()["scan_status"] == "pending_scan"
    blocked = await api.get(
        f"{API}/patients/{pid}/documents/{waiting.json()['id']}/download", headers=ph
    )
    assert blocked.status_code == 409
    clean = FakeScanner(Verdict.CLEAN)
    done = await records.scan_pending(session, storage, clean, api_app.state.settings)
    assert [d.scan_status.value for d, _ in done] == ["clean"]
    assert clean.scanned == 1
    await session.commit()
    ok = await api.get(
        f"{API}/patients/{pid}/documents/{waiting.json()['id']}/download", headers=ph
    )
    assert ok.status_code == 200

    rows = (
        await session.execute(
            select(AuditLog.action, AuditLog.context)
            .where(AuditLog.patient_id == uuid.UUID(pid))
            .order_by(AuditLog.seq)
        )
    ).all()
    actions = [a for a, _ in rows]
    assert actions.count("document.upload_rejected") == 3
    verdicts = [c.get("verdict") for a, c in rows if a == "document.upload_completed"]
    assert verdicts == ["not_scanned", "error"]


async def test_documents_follow_type_permissions_and_corrections_are_versioned(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ["tests_and_reports"])
    summary = (
        await _upload(api, storage, ph, pid, document_type="discharge_summary", title="Placeholder")
    ).json()
    lab = (await _upload(api, storage, ph, pid, title="Placeholder lab")).json()
    visible = {
        d["id"] for d in (await api.get(f"{API}/patients/{pid}/documents", headers=dh)).json()
    }
    assert visible == {lab["id"]}
    hidden = await api.get(f"{API}/patients/{pid}/documents/{summary['id']}/download", headers=dh)
    assert hidden.status_code == 404

    fixed = await api.patch(
        f"{API}/patients/{pid}/documents/{lab['id']}",
        json={
            "reason": "wrong date",
            "document_date": (date.today() - timedelta(days=3)).isoformat(),
        },
        headers=ph,
    )
    assert fixed.status_code == 200, fixed.text
    # The doctor cannot change a document the patient added.
    assert (
        await api.patch(
            f"{API}/patients/{pid}/documents/{lab['id']}",
            json={"reason": "x" * 5, "title": "New"},
            headers=dh,
        )
    ).status_code == 403
    history = (
        await api.get(f"{API}/patients/{pid}/timeline/document/{lab['id']}/history", headers=ph)
    ).json()["entries"]
    assert history[-1]["reason"] == "wrong date"
    assert history[-1]["changes"][0]["field"] == "document_date"


# --- tests and reports --------------------------------------------------------------------


async def test_order_upload_review_and_status_flow(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, dprof = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    order = (
        await api.post(
            f"{API}/patients/{pid}/test-orders",
            json={"tests": ["Test B", "Test A"], "clinical_indication": "Placeholder reason"},
            headers=dh,
        )
    ).json()
    mine = (await api.get(f"{API}/patients/{pid}/test-orders", headers=ph)).json()
    assert mine[0]["clinical_indication"] == "Placeholder reason"
    assert mine[0]["ordering_doctor_name"] == dprof.display_name
    assert "sample_collected" in mine[0]["next_statuses"]
    # Patients cannot order tests or change order status.
    assert (
        await api.post(
            f"{API}/patients/{pid}/test-orders/{order['id']}/status",
            json={"status": "completed"},
            headers=ph,
        )
    ).status_code == 403

    file = (await _upload(api, storage, ph, pid, content_type="image/png", body=PNG)).json()
    up = await api.post(
        f"{API}/patients/{pid}/reports/uploaded",
        json={
            "document_id": file["id"],
            "order_id": order["id"],
            "report_date": date.today().isoformat(),
            "notes": "Placeholder note",
        },
        headers=ph,
    )
    assert up.status_code == 201, up.text
    report = up.json()
    assert report["status"] == "pending_review"
    assert report["source"] == "patient"
    assert report["test_name"] == "Test A, Test B"
    assert report["ordering_doctor_name"] == dprof.display_name
    assert report["file"]["available"] is True
    again = await api.post(
        f"{API}/patients/{pid}/reports/uploaded",
        json={"document_id": file["id"], "test_name": "x"},
        headers=ph,
    )
    assert again.status_code == 409
    future = await api.post(
        f"{API}/patients/{pid}/reports/uploaded",
        json={
            "document_id": file["id"],
            "test_name": "x",
            "report_date": (date.today() + timedelta(days=2)).isoformat(),
        },
        headers=ph,
    )
    assert future.status_code == 422

    for step in ("sample_collected", "completed"):
        moved = await api.post(
            f"{API}/patients/{pid}/test-orders/{order['id']}/status",
            json={"status": step},
            headers=dh,
        )
        assert moved.status_code == 200
        assert moved.json()["status"] == step
    back = await api.post(
        f"{API}/patients/{pid}/test-orders/{order['id']}/status",
        json={"status": "sample_collected"},
        headers=dh,
    )
    assert back.status_code == 422 or back.status_code == 409
    order_history = (
        await api.get(f"{API}/patients/{pid}/timeline/test_order/{order['id']}/history", headers=ph)
    ).json()["entries"]
    assert [c["changes"][0]["after"] for c in order_history[1:]] == [
        "sample_collected",
        "completed",
    ]

    no_note = await api.post(
        f"{API}/patients/{pid}/reports/{report['id']}/review",
        json={"decision": "reject"},
        headers=dh,
    )
    assert no_note.status_code == 422
    assert (
        await api.post(
            f"{API}/patients/{pid}/reports/{report['id']}/review",
            json={"decision": "verify"},
            headers=ph,
        )
    ).status_code == 403
    verified = await api.post(
        f"{API}/patients/{pid}/reports/{report['id']}/review",
        json={"decision": "verify"},
        headers=dh,
    )
    assert verified.json()["status"] == "verified"
    # Verified reports are frozen; the patient can no longer withdraw it.
    async with expect_db_error(session, "HI001"):
        await session.execute(
            text("UPDATE test_reports SET test_name = 'changed' WHERE id = :id"),
            {"id": report["id"]},
        )
    withdraw = await api.post(
        f"{API}/patients/{pid}/reports/{report['id']}/entered-in-error",
        json={"reason": "wrong file"},
        headers=ph,
    )
    assert withdraw.status_code == 403
    in_error = await api.post(
        f"{API}/patients/{pid}/reports/{report['id']}/entered-in-error",
        json={"reason": "wrong patient"},
        headers=dh,
    )
    assert in_error.json()["status"] == "entered_in_error"
    assert in_error.json()["review_note"] == "wrong patient"
    actions = await _actions(session, pid)
    assert {
        "test_report.upload",
        "test_report.review",
        "test_order.status",
        "test_report.entered_in_error",
    } <= set(actions)


async def test_doctor_records_a_referenced_report_with_metadata(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    doctor, _ = await make_doctor(make_account, session)
    dh = await _link(api, session, patient, doctor, ALL)
    doc = (await _upload(api, storage, dh, pid)).json()
    rec = await api.post(
        f"{API}/patients/{pid}/reports",
        json={
            "document_id": doc["id"],
            "test_name": "Test C",
            "report_date": date.today().isoformat(),
            "lab_reference": "REF-0001",
            "notes": "Placeholder",
            "results": [{"analyte_name": "Analyte", "value_numeric": "1.5", "unit": "u"}],
        },
        headers=dh,
    )
    assert rec.status_code == 201, rec.text
    assert rec.json()["status"] == "verified"
    assert rec.json()["lab_reference"] == "REF-0001"
    # Patients use the upload endpoint, doctors the recording one.
    assert (
        await api.post(f"{API}/patients/{pid}/reports", json={"document_id": doc["id"]}, headers=ph)
    ).status_code == 403
    mine = (await api.get(f"{API}/patients/{pid}/reports/{rec.json()['id']}", headers=ph)).json()
    assert mine["access"] == "full"
    assert mine["results"][0]["analyte_name"] == "Analyte"
    link = await api.get(f"{API}/patients/{pid}/reports/{rec.json()['id']}/file", headers=ph)
    assert link.status_code == 200
    assert link.json()["expires_in"] == 60


async def test_patient_shares_one_report_with_one_doctor(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    patient, pid, ph = await _patient(api, make_account, session)
    narrow, nprof = await make_doctor(make_account, session)
    outsider, oprof = await make_doctor(make_account, session)
    nh = await _link(api, session, patient, narrow, ["medications"])
    files = [(await _upload(api, storage, ph, pid)).json() for _ in range(2)]
    reports = [
        (
            await api.post(
                f"{API}/patients/{pid}/reports/uploaded",
                json={"document_id": f["id"], "test_name": f"Test {i}"},
                headers=ph,
            )
        ).json()
        for i, f in enumerate(files)
    ]
    assert (await api.get(f"{API}/patients/{pid}/reports", headers=nh)).json() == []
    assert (
        await api.get(f"{API}/patients/{pid}/reports/{reports[0]['id']}", headers=nh)
    ).status_code == 404

    detail = (await api.get(f"{API}/patients/{pid}/reports/{reports[0]['id']}", headers=ph)).json()
    assert {t["doctor_id"] for t in detail["share_targets"]} == {str(nprof.id)}
    not_linked = await api.post(
        f"{API}/patients/{pid}/reports/{reports[0]['id']}/shares",
        json={"doctor_id": str(oprof.id)},
        headers=ph,
    )
    assert not_linked.status_code == 422
    past = await api.post(
        f"{API}/patients/{pid}/reports/{reports[0]['id']}/shares",
        json={
            "doctor_id": str(nprof.id),
            "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
        headers=ph,
    )
    assert past.status_code == 422
    share = await api.post(
        f"{API}/patients/{pid}/reports/{reports[0]['id']}/shares",
        json={"doctor_id": str(nprof.id)},
        headers=ph,
    )
    assert share.status_code == 201, share.text
    # A doctor cannot share on the patient's behalf.
    assert (
        await api.post(
            f"{API}/patients/{pid}/reports/{reports[1]['id']}/shares",
            json={"doctor_id": str(nprof.id)},
            headers=nh,
        )
    ).status_code == 403

    seen = (await api.get(f"{API}/patients/{pid}/reports", headers=nh)).json()
    assert [r["id"] for r in seen] == [reports[0]["id"]]
    assert seen[0]["access"] == "shared"
    shared_detail = (
        await api.get(f"{API}/patients/{pid}/reports/{reports[0]['id']}", headers=nh)
    ).json()
    assert shared_detail["shares"] is None  # only the patient manages sharing
    assert (
        await api.get(f"{API}/patients/{pid}/reports/{reports[0]['id']}/file", headers=nh)
    ).status_code == 200
    assert (
        await api.get(f"{API}/patients/{pid}/reports/{reports[1]['id']}/file", headers=nh)
    ).status_code == 404
    assert (await api.get(f"{API}/patients/{pid}/test-orders", headers=nh)).status_code == 403
    timeline_reports = [
        e for e in (await _timeline(api, nh, pid))["items"] if e["kind"] == "report"
    ]
    assert [e["resource_id"] for e in timeline_reports] == [reports[0]["id"]]

    revoked = await api.delete(
        f"{API}/patients/{pid}/report-shares/{share.json()['id']}", headers=ph
    )
    assert revoked.status_code == 204
    assert (await api.get(f"{API}/patients/{pid}/reports", headers=nh)).json() == []
    actions = await _actions(session, pid)
    assert {"test_report.share", "test_report.share_revoked", "test_report.file_download"} <= set(
        actions
    )
    assert outsider  # linked to nobody


async def test_caregiver_report_access_follows_scopes(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    _, pid, ph = await _patient(api, make_account, session)
    viewer = await make_account(Role.CAREGIVER)
    uploader = await make_account(Role.CAREGIVER)
    _, vh = await _invite_and_accept(api, ph, uuid.UUID(pid), viewer, ["view_medications"])
    _, uh = await _invite_and_accept(
        api, ph, uuid.UUID(pid), uploader, ["view_reports", "upload_reports"]
    )
    assert (await api.get(f"{API}/patients/{pid}/reports", headers=vh)).status_code == 403
    f = (await _upload(api, storage, uh, pid)).json()
    rep = await api.post(
        f"{API}/patients/{pid}/reports/uploaded",
        json={"document_id": f["id"], "test_name": "Test"},
        headers=uh,
    )
    assert rep.status_code == 201
    assert rep.json()["source"] == "caregiver"
    # Caregivers never manage sharing.
    assert (
        await api.post(
            f"{API}/patients/{pid}/reports/{rep.json()['id']}/shares",
            json={"doctor_id": str(uuid.uuid4())},
            headers=uh,
        )
    ).status_code == 403
