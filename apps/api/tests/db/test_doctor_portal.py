"""Doctor portal API: workflows, and the boundary that doctors only reach their patients."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.care_team.models import DoctorProfile, DoctorVerificationStatus
from tests.db.conftest import Builder, login

pytestmark = pytest.mark.integration

API = "/api/v1"
ALL_CATEGORIES = [
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
PDF = b"%PDF-1.7\n% placeholder test document\n%%EOF\n"


class FakeStorage:
    """In-memory stand-in for S3: presign returns a fake URL; tests 'upload' by key."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> dict[str, Any]:
        return {
            "url": "https://storage.test/bucket",
            "fields": {"key": key, "Content-Type": content_type},
        }

    def presign_download(self, key: str, *, filename: str | None = None, ttl: int = 60) -> str:
        return f"https://storage.test/bucket/{key}?sig=test"

    async def read(self, key: str, max_bytes: int) -> bytes | None:
        body = self.objects.get(key)
        return body if body is not None and len(body) <= max_bytes else None

    async def ping(self) -> None:
        pass


@pytest.fixture
def storage(api_app: Any) -> FakeStorage:
    fake = FakeStorage()
    api_app.state.storage = fake
    return fake


async def make_doctor(
    make_account: Builder, session: AsyncSession, *, verified: bool = True
) -> tuple[Any, DoctorProfile]:
    user = await make_account(Role.DOCTOR)
    profile = DoctorProfile(
        user_id=user.id,
        display_name=f"Dr Placeholder {uuid.uuid4().hex[:4]}",
        registration_council="Test Council",
        registration_number=uuid.uuid4().hex[:10],
        verification_status=DoctorVerificationStatus.VERIFIED
        if verified
        else DoctorVerificationStatus.PENDING,
        verified_at=datetime.now(UTC) if verified else None,
        verified_by=user.id if verified else None,
    )
    session.add(profile)
    await session.flush()
    return user, profile


async def add_patient(
    api: Any, headers: dict[str, str], categories: list[str] | None = None
) -> str:
    resp = await api.post(
        f"{API}/doctor/patients",
        json={
            "given_name": "Placeholder",
            "family_name": "Person",
            "date_of_birth": "1980-06-15",
            "sex_at_birth": "female",
            "consent": {"confirmed": True, "data_categories": categories or ALL_CATEGORIES},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# --- adding and connecting patients ----------------------------------------------------


async def test_add_patient_in_person_records_consent_and_link(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _ = await make_doctor(make_account, session)
    headers = await login(api, user.test_email)
    pid = await add_patient(api, headers, ["demographics", "medications"])

    access = (await api.get(f"{API}/patients/{pid}/access", headers=headers)).json()
    assert access["via"] == ["doctor"]
    assert set(access["permissions"]) == {"view_profile", "view_medications"}

    listed = (await api.get(f"{API}/doctor/patients", headers=headers)).json()
    assert listed[0]["display_name"] == "Placeholder Person"
    assert listed[0]["shared_categories"] == ["demographics", "medications"]

    missing_consent = await api.post(
        f"{API}/doctor/patients",
        json={
            "given_name": "X",
            "consent": {"confirmed": False, "data_categories": ["demographics"]},
        },
        headers=headers,
    )
    assert missing_consent.status_code == 422


async def test_unverified_doctor_cannot_add_patients(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    user, _ = await make_doctor(make_account, session, verified=False)
    headers = await login(api, user.test_email)
    resp = await api.post(
        f"{API}/doctor/patients",
        json={
            "given_name": "X",
            "consent": {"confirmed": True, "data_categories": ["demographics"]},
        },
        headers=headers,
    )
    assert resp.status_code == 403
    dash = (await api.get(f"{API}/doctor/dashboard", headers=headers)).json()
    assert dash["verification_status"] == "pending"
    assert dash["total_patients"] == 0


async def test_connect_existing_patient_requires_their_acceptance(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    patient = await make_account(Role.PATIENT)
    dh, ph = await login(api, doctor.test_email), await login(api, patient.test_email)

    real = await api.post(
        f"{API}/doctor/patients/connect", json={"patient_email": patient.test_email}, headers=dh
    )
    fake = await api.post(
        f"{API}/doctor/patients/connect", json={"patient_email": "ghost@example.com"}, headers=dh
    )
    assert real.status_code == fake.status_code == 202
    assert real.json() == fake.json()  # no account enumeration

    assert (await api.get(f"{API}/doctor/patients", headers=dh)).json() == []  # pending ≠ linked
    requests = (await api.get(f"{API}/me/doctor-requests", headers=ph)).json()
    assert len(requests) == 1
    rel = requests[0]["relationship_id"]

    no_cats = await api.post(
        f"{API}/me/doctor-requests/{rel}/accept", json={"data_categories": []}, headers=ph
    )
    assert no_cats.status_code == 422
    ok = await api.post(
        f"{API}/me/doctor-requests/{rel}/accept",
        json={"data_categories": ["medications"]},
        headers=ph,
    )
    assert ok.status_code == 204
    listed = (await api.get(f"{API}/doctor/patients", headers=dh)).json()
    assert len(listed) == 1
    assert listed[0]["display_name"] is None  # demographics not shared


# --- clinical workflow -----------------------------------------------------------------


async def test_visit_notes_diagnosis_orders_prescription_follow_up(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    h = await login(api, doctor.test_email)
    pid = await add_patient(api, h)

    visit = await api.post(
        f"{API}/patients/{pid}/visits",
        json={"visit_type": "in_person", "chief_complaint": "placeholder complaint"},
        headers=h,
    )
    assert visit.status_code == 201
    vid = visit.json()["id"]

    note = (
        await api.post(
            f"{API}/patients/{pid}/visits/{vid}/notes",
            json={"note_type": "soap", "body": "draft text"},
            headers=h,
        )
    ).json()
    edited = await api.put(
        f"{API}/patients/{pid}/notes/{note['id']}", json={"body": "final text"}, headers=h
    )
    assert edited.status_code == 200
    signed = await api.post(f"{API}/patients/{pid}/notes/{note['id']}/sign", headers=h)
    assert signed.json()["status"] == "signed"
    locked = await api.put(
        f"{API}/patients/{pid}/notes/{note['id']}", json={"body": "rewrite"}, headers=h
    )
    assert locked.status_code == 409

    amendment = await api.post(
        f"{API}/patients/{pid}/notes/{note['id']}/amendments",
        json={"body": "corrected text", "reason": "typo in dose"},
        headers=h,
    )
    assert amendment.status_code == 201
    await api.post(f"{API}/patients/{pid}/notes/{amendment.json()['id']}/sign", headers=h)
    notes = {n["id"]: n for n in (await api.get(f"{API}/patients/{pid}/notes", headers=h)).json()}
    assert notes[note["id"]]["status"] == "superseded"
    assert notes[amendment.json()["id"]]["status"] == "signed"

    diagnosis = await api.post(
        f"{API}/patients/{pid}/conditions",
        json={
            "name": "Placeholder condition",
            "verification_status": "provisional",
            "visit_id": vid,
            "icd10_code": "z00.0",
        },
        headers=h,
    )
    assert diagnosis.status_code == 201
    assert diagnosis.json()["source"] == "doctor"
    assert diagnosis.json()["icd10_code"] == "Z00.0"

    order = await api.post(
        f"{API}/patients/{pid}/test-orders",
        json={"tests": ["Test A", "Test B", "Test A"], "visit_id": vid},
        headers=h,
    )
    assert order.status_code == 201
    assert order.json()["tests"] == ["Test A", "Test B"]

    rx = (
        await api.post(
            f"{API}/patients/{pid}/prescriptions",
            json={
                "visit_id": vid,
                "items": [
                    {
                        "drug_name": "Medicine A",
                        "strength": "1 unit",
                        "frequency_text": "1-0-1",
                        "duration_days": 5,
                    },
                    {"drug_name": "Medicine B", "is_prn": True, "prn_reason": "if needed"},
                ],
            },
            headers=h,
        )
    ).json()
    assert rx["status"] == "draft"
    issued = await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/issue", headers=h)
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    meds = (await api.get(f"{API}/patients/{pid}/medications", headers=h)).json()
    assert {m["status"] for m in meds} == {"pending_confirmation"}  # patient confirms first
    frozen = await api.put(
        f"{API}/patients/{pid}/prescriptions/{rx['id']}",
        json={"items": [{"drug_name": "Changed"}]},
        headers=h,
    )
    assert frozen.status_code == 409

    existing = await api.post(
        f"{API}/patients/{pid}/medications",
        json={"name": "Medicine C", "strength": "2 units"},
        headers=h,
    )
    assert existing.status_code == 201
    assert existing.json()["source"] == "clinician_recorded"

    due = (date.today() + timedelta(days=7)).isoformat()
    fu = await api.post(
        f"{API}/patients/{pid}/follow-ups",
        json={"due_date": due, "source_visit_id": vid},
        headers=h,
    )
    assert fu.status_code == 201
    done = await api.post(f"{API}/patients/{pid}/visits/{vid}/complete", headers=h)
    assert done.json()["status"] == "completed"

    page = (await api.get(f"{API}/patients/{pid}/timeline", headers=h)).json()
    kinds = {e["kind"] for e in page["items"]}
    assert {
        "visit",
        "note",
        "assessment",
        "test_order",
        "prescription",
        "medication",
        "follow_up",
    } <= kinds

    adherence = (await api.get(f"{API}/patients/{pid}/adherence", headers=h)).json()
    assert adherence == {"days": 30, "has_schedules": False, "total_recorded": 0, "lines": []}

    dash = (await api.get(f"{API}/doctor/dashboard", headers=h)).json()
    assert dash["total_patients"] == 1
    assert dash["active_treatments"] == 2  # the two issued lines, awaiting confirmation
    assert dash["upcoming_follow_ups"][0]["patient_name"] == "Placeholder Person"
    assert dash["recent_patients"][0]["patient_id"] == pid


async def test_colleague_cannot_see_or_touch_another_doctors_drafts(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    colleague, colleague_profile = await make_doctor(make_account, session)
    h = await login(api, doctor.test_email)
    pid = await add_patient(api, h)
    # The colleague is linked to the same patient with full consent.
    from app.modules.care_team.models import DoctorPatientRelationship, RelationshipStatus
    from app.modules.consent.models import (
        ConsentPurpose,
        ConsentRecord,
        GranteeType,
        GrantorCapacity,
    )

    session.add(
        DoctorPatientRelationship(
            doctor_id=colleague_profile.id,
            patient_id=uuid.UUID(pid),
            status=RelationshipStatus.ACTIVE,
            initiated_by=colleague.id,
            started_at=datetime.now(UTC),
        )
    )
    session.add(
        ConsentRecord(
            patient_id=uuid.UUID(pid),
            granted_by=colleague.id,
            grantor_capacity=GrantorCapacity.CLINICIAN_RECORDED,
            grantee_type=GranteeType.DOCTOR,
            grantee_user_id=colleague.id,
            purpose=ConsentPurpose.CARE_DELIVERY,
            data_categories=ALL_CATEGORIES,
            notice_version="test-1",
        )
    )
    await session.flush()
    ch = await login(api, colleague.test_email)

    vid = (
        await api.post(
            f"{API}/patients/{pid}/visits", json={"visit_type": "teleconsult"}, headers=h
        )
    ).json()["id"]
    note = (
        await api.post(
            f"{API}/patients/{pid}/visits/{vid}/notes", json={"body": "private draft"}, headers=h
        )
    ).json()
    rx = (
        await api.post(
            f"{API}/patients/{pid}/prescriptions",
            json={"items": [{"drug_name": "Medicine A"}]},
            headers=h,
        )
    ).json()

    assert (await api.get(f"{API}/patients/{pid}/notes", headers=ch)).json() == []
    assert (await api.get(f"{API}/patients/{pid}/prescriptions", headers=ch)).json() == []
    assert (
        await api.post(f"{API}/patients/{pid}/notes/{note['id']}/sign", headers=ch)
    ).status_code == 404
    assert (
        await api.post(f"{API}/patients/{pid}/prescriptions/{rx['id']}/issue", headers=ch)
    ).status_code == 404
    assert (
        await api.post(f"{API}/patients/{pid}/visits/{vid}/complete", headers=ch)
    ).status_code == 403


async def test_upload_and_record_report(
    api: Any, make_account: Builder, session: AsyncSession, storage: FakeStorage
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    h = await login(api, doctor.test_email)
    pid = await add_patient(api, h)

    start = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": "lab_report",
            "content_type": "application/pdf",
            "size_bytes": len(PDF),
            "filename": "r.pdf",
        },
        headers=h,
    )
    assert start.status_code == 201
    doc_id = start.json()["document_id"]
    key = start.json()["fields"]["key"]
    assert "Placeholder" not in key  # keys carry no personal data
    not_yet = await api.post(f"{API}/patients/{pid}/documents/{doc_id}/complete", headers=h)
    assert not_yet.status_code == 422
    storage.objects[key] = PDF
    done = await api.post(f"{API}/patients/{pid}/documents/{doc_id}/complete", headers=h)
    assert done.status_code == 200
    assert done.json()["scan_status"] == "clean"

    report = await api.post(
        f"{API}/patients/{pid}/reports",
        json={
            "document_id": doc_id,
            "lab_name": "Placeholder Lab",
            "results": [
                {
                    "analyte_name": "Analyte A",
                    "value_numeric": "5.2",
                    "unit": "unit",
                    "flag": "normal",
                },
            ],
        },
        headers=h,
    )
    assert report.status_code == 201
    assert report.json()["status"] == "verified"
    link = (await api.get(f"{API}/patients/{pid}/documents/{doc_id}/download", headers=h)).json()
    assert link["expires_in"] == 60

    # Content that does not match its declared type is quarantined.
    bad = (
        await api.post(
            f"{API}/patients/{pid}/documents/uploads",
            json={"document_type": "other", "content_type": "application/pdf", "size_bytes": 10},
            headers=h,
        )
    ).json()
    storage.objects[bad["fields"]["key"]] = b"MZ\x90\x00not a pdf"
    rejected = await api.post(
        f"{API}/patients/{pid}/documents/{bad['document_id']}/complete", headers=h
    )
    assert rejected.status_code == 422
    docs = (await api.get(f"{API}/patients/{pid}/documents", headers=h)).json()
    assert [d["id"] for d in docs] == [doc_id]

    too_big = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": "other",
            "content_type": "application/pdf",
            "size_bytes": 50 * 1024 * 1024,
        },
        headers=h,
    )
    assert too_big.status_code == 422
    exe = await api.post(
        f"{API}/patients/{pid}/documents/uploads",
        json={
            "document_type": "other",
            "content_type": "application/x-msdownload",
            "size_bytes": 10,
        },
        headers=h,
    )
    assert exe.status_code == 422


async def test_double_booking_is_rejected(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    h = await login(api, doctor.test_email)
    p1, p2 = await add_patient(api, h), await add_patient(api, h)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    first = await api.post(
        f"{API}/patients/{p1}/appointments",
        json={"starts_at": start, "duration_minutes": 30},
        headers=h,
    )
    assert first.status_code == 201
    clash = await api.post(
        f"{API}/patients/{p2}/appointments", json={"starts_at": start}, headers=h
    )
    assert clash.status_code == 409
    assert clash.json()["type"].endswith("/conflict")


# --- boundaries --------------------------------------------------------------------------

PATIENT_GETS = [
    "profile",
    "overview",
    "timeline",
    "visits",
    "notes",
    "medical-history",
    "medications",
    "prescriptions",
    "test-orders",
    "reports",
    "documents",
    "appointments",
    "follow-ups",
    "adherence",
]


async def test_unrelated_doctor_reaches_nothing(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    stranger, _ = await make_doctor(make_account, session)
    pid = await add_patient(api, await login(api, doctor.test_email))
    sh = await login(api, stranger.test_email)
    for path in PATIENT_GETS:
        resp = await api.get(f"{API}/patients/{pid}/{path}", headers=sh)
        assert resp.status_code == 404, path
    writes = [
        ("visits", {"visit_type": "in_person"}),
        ("conditions", {"name": "x", "verification_status": "provisional"}),
        ("test-orders", {"tests": ["x"]}),
        ("prescriptions", {"items": [{"drug_name": "x"}]}),
        ("medications", {"name": "x"}),
        ("follow-ups", {"due_date": date.today().isoformat()}),
        (
            "documents/uploads",
            {"document_type": "other", "content_type": "application/pdf", "size_bytes": 5},
        ),
    ]
    for path, body in writes:
        resp = await api.post(f"{API}/patients/{pid}/{path}", json=body, headers=sh)
        assert resp.status_code == 404, path
    assert (await api.get(f"{API}/doctor/patients", headers=sh)).json() == []
    assert (await api.get(f"{API}/doctor/patients?q=Placeholder", headers=sh)).json() == []


async def test_consent_narrows_the_chart(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor, _ = await make_doctor(make_account, session)
    h = await login(api, doctor.test_email)
    pid = await add_patient(api, h, ["medications", "prescriptions"])
    assert (await api.get(f"{API}/patients/{pid}/medications", headers=h)).status_code == 200
    for path in ["profile", "visits", "medical-history", "appointments", "adherence"]:
        assert (await api.get(f"{API}/patients/{pid}/{path}", headers=h)).status_code == 403, path
    # Reports: only those the patient shares one by one (none yet).
    assert (await api.get(f"{API}/patients/{pid}/reports", headers=h)).json() == []
    assert (await api.get(f"{API}/patients/{pid}/test-orders", headers=h)).status_code == 403
    kinds = {
        e["kind"]
        for e in (await api.get(f"{API}/patients/{pid}/timeline", headers=h)).json()["items"]
    }
    assert kinds <= {"prescription", "medication"}
    # Cannot write where consent does not reach.
    assert (
        await api.post(f"{API}/patients/{pid}/visits", json={"visit_type": "in_person"}, headers=h)
    ).status_code == 403
    overview = (await api.get(f"{API}/patients/{pid}/overview", headers=h)).json()
    assert overview["profile"] is None
    assert overview["active_conditions"] is None
    assert overview["active_medications"] == 0
    assert (await api.get(f"{API}/doctor/patients?q=Placeholder", headers=h)).json() == []


async def test_patients_can_read_their_own_chart_but_not_write_clinical_records(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient = await make_account(Role.PATIENT)
    ph = await login(api, patient.test_email)
    me = (await api.get(f"{API}/me", headers=ph)).json()
    pid = me["patient_profile_id"]
    for path in ["profile", "timeline", "visits", "medications", "prescriptions", "adherence"]:
        assert (await api.get(f"{API}/patients/{pid}/{path}", headers=ph)).status_code == 200, path
    assert (
        await api.post(f"{API}/patients/{pid}/visits", json={"visit_type": "in_person"}, headers=ph)
    ).status_code == 403
    assert (
        await api.post(
            f"{API}/patients/{pid}/prescriptions", json={"items": [{"drug_name": "x"}]}, headers=ph
        )
    ).status_code == 403
