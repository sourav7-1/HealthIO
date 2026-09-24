"""Caregivers and dependants: permission boundaries, lifecycle and audit.

Use cases covered: a parent managing a child (dependant profile), an adult child helping
an elderly parent who has their own account (invitation), and an authorised
representative managing a dependent adult (dependant profile with a stated basis).
"""

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordSource, Role
from app.modules.audit.models import AuditLog
from app.modules.clinical.models import AllergenCategory, Allergy, ConditionVerificationStatus
from tests.db.conftest import Builder, login
from tests.db.test_authorization import patient_id_of

pytestmark = pytest.mark.integration

API = "/api/v1"


def _years_ago(years: int) -> str:
    today = date.today()
    return today.replace(year=today.year - years, day=min(today.day, 28)).isoformat()


async def _add_dependant(api: Any, headers: dict[str, str], **overrides: Any) -> Any:
    body = {
        "given_name": "Dependant",
        "family_name": "Placeholder",
        "date_of_birth": _years_ago(7),
        "sex_at_birth": "unknown",
        "relationship_type": "parent",
        "basis": "parent_of_minor",
        "declaration_accepted": True,
        **overrides,
    }
    return await api.post(f"{API}/me/dependants", json=body, headers=headers)


async def _invite_and_accept(
    api: Any, owner: dict[str, str], pid: uuid.UUID, carer: Any, scopes: list[str]
) -> tuple[str, dict[str, str]]:
    resp = await api.post(
        f"{API}/patients/{pid}/caregivers",
        json={"caregiver_email": carer.test_email, "relationship_type": "child", "scopes": scopes},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    rel_id = resp.json()["relationship_id"]
    carer_headers = await login(api, carer.test_email)
    accepted = await api.post(f"{API}/caregiver-invitations/{rel_id}/accept", headers=carer_headers)
    assert accepted.status_code == 200, accepted.text
    return rel_id, carer_headers


async def _actions(session: AsyncSession, pid: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(AuditLog.action).where(AuditLog.patient_id == pid).order_by(AuditLog.seq)
    )
    return list(rows.all())


async def _doctor_allergy(session: AsyncSession, pid: uuid.UUID) -> uuid.UUID:
    allergy = Allergy(
        patient_id=pid,
        substance="Placeholder substance",
        category=AllergenCategory.MEDICATION,
        source=RecordSource.DOCTOR,
        verification_status=ConditionVerificationStatus.CONFIRMED,
    )
    session.add(allergy)
    await session.flush()
    return allergy.id


# --- parent managing a child ------------------------------------------------------------------


async def test_parent_creates_and_manages_a_child_but_never_clinical_records(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    parent = await make_account()
    headers = await login(api, parent.test_email)
    created = await _add_dependant(api, headers)
    assert created.status_code == 201, created.text
    link = created.json()
    child = uuid.UUID(link["patient_id"])
    assert link["is_dependant"] is True
    assert link["is_guardian"] is True
    assert link["status"] == "active"
    assert link["guardian_basis"] == "parent_of_minor"
    assert "caregiver" in (await api.get(f"{API}/me", headers=headers)).json()["roles"]

    access = (await api.get(f"{API}/patients/{child}/access", headers=headers)).json()
    assert access["via"] == ["caregiver"]
    perms = set(access["permissions"])
    assert {"view_prescriptions", "view_visits", "manage_caregivers", "edit_profile"} <= perms
    assert not perms & {
        "edit_clinical_records",
        "change_doctor_prescription",
        "delete_medical_records",
        "manage_consent",
    }

    # Reads work; the child's demographics can be corrected by the guardian.
    for path in ["medications", "prescriptions", "visits", "medical-history", "doses"]:
        assert (await api.get(f"{API}/patients/{child}/{path}", headers=headers)).status_code == 200
    patched = await api.patch(
        f"{API}/patients/{child}/profile", json={"blood_group": "O+"}, headers=headers
    )
    assert patched.status_code == 200, patched.text

    # Clinical writes are refused.
    visit = await api.post(
        f"{API}/patients/{child}/visits", json={"visit_type": "in_person"}, headers=headers
    )
    assert visit.status_code == 403
    doctor_entry = await _doctor_allergy(session, child)
    removed = await api.delete(
        f"{API}/patients/{child}/self-reported/allergies/{doctor_entry}", headers=headers
    )
    assert removed.status_code == 403

    # What the guardian reports is labelled as the caregiver's.
    reported = await api.post(
        f"{API}/patients/{child}/self-reported/allergies",
        json={"substance": "Placeholder", "category": "food"},
        headers=headers,
    )
    assert reported.status_code == 201, reported.text
    assert reported.json()["source"] == "caregiver"
    assert "caregiver.dependant_created" in await _actions(session, child)


async def test_prescription_writes_are_refused_before_validation(
    api: Any, make_account: Builder
) -> None:
    parent = await make_account()
    headers = await login(api, parent.test_email)
    child = (await _add_dependant(api, headers)).json()["patient_id"]
    rx = await api.post(
        f"{API}/patients/{child}/prescriptions",
        json={"prescribed_on": date.today().isoformat(), "items": []},
        headers=headers,
    )
    assert rx.status_code == 403


@pytest.mark.parametrize(
    ("years", "basis", "status"),
    [
        (7, "parent_of_minor", 201),
        (7, "power_of_attorney", 422),  # a child needs a parent or guardian
        (40, "parent_of_minor", 422),  # an adult is not a minor
        (80, "power_of_attorney", 201),  # representative of an elderly parent
        (45, "court_appointed_guardian", 201),  # dependent adult
        (70, "adult_consented", 201),
    ],
)
async def test_dependant_basis_must_match_age(
    api: Any, make_account: Builder, years: int, basis: str, status: int
) -> None:
    headers = await login(api, (await make_account()).test_email)
    resp = await _add_dependant(api, headers, date_of_birth=_years_ago(years), basis=basis)
    assert resp.status_code == status, resp.text


async def test_dependant_needs_the_declaration(api: Any, make_account: Builder) -> None:
    headers = await login(api, (await make_account()).test_email)
    resp = await _add_dependant(api, headers, declaration_accepted=False)
    assert resp.status_code == 422


# --- adult child helping a parent who has an account -----------------------------------------


async def test_invited_caregiver_gets_only_the_granted_scopes(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    parent, child = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, parent)
    owner = await login(api, parent.test_email)
    _, carer = await _invite_and_accept(
        api, owner, pid, child, ["view_medications", "view_prescriptions", "report_health_info"]
    )

    assert (await api.get(f"{API}/patients/{pid}/prescriptions", headers=carer)).status_code == 200
    assert (await api.get(f"{API}/patients/{pid}/medications", headers=carer)).status_code == 200
    for path in ["visits", "appointments", "reports", "medical-history", "caregivers"]:
        resp = await api.get(f"{API}/patients/{pid}/{path}", headers=carer)
        assert resp.status_code == 403, path
    # An adult patient's own profile is theirs to edit, never a caregiver's.
    patched = await api.patch(
        f"{API}/patients/{pid}/profile", json={"blood_group": "O+"}, headers=carer
    )
    assert patched.status_code == 403

    # Record access is audited as the caregiver acting for the patient.
    rows = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.patient_id == pid, AuditLog.action == "prescription.list"
            )
        )
    ).all()
    assert rows
    assert rows[-1].actor_user_id == child.id
    assert rows[-1].context.get("via") == "caregiver"


async def test_caregiver_of_one_patient_cannot_see_another(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    a, b, carer = (
        await make_account(Role.PATIENT),
        await make_account(Role.PATIENT),
        await make_account(),
    )
    pid_a, pid_b = await patient_id_of(session, a), await patient_id_of(session, b)
    _, headers = await _invite_and_accept(
        api, await login(api, a.test_email), pid_a, carer, ["view_medications"]
    )
    assert (
        await api.get(f"{API}/patients/{pid_b}/medications", headers=headers)
    ).status_code == 404


# --- lifecycle -------------------------------------------------------------------------------


async def test_declined_invitation_grants_nothing(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    rel = await api.post(
        f"{API}/patients/{pid}/caregivers",
        json={
            "caregiver_email": carer.test_email,
            "relationship_type": "friend",
            "scopes": ["view_medications"],
        },
        headers=owner,
    )
    rel_id = rel.json()["relationship_id"]
    carer_headers = await login(api, carer.test_email)
    declined = await api.post(
        f"{API}/caregiver-invitations/{rel_id}/decline", headers=carer_headers
    )
    assert declined.status_code == 204
    again = await api.post(f"{API}/caregiver-invitations/{rel_id}/accept", headers=carer_headers)
    assert again.status_code == 409
    assert (
        await api.get(f"{API}/patients/{pid}/medications", headers=carer_headers)
    ).status_code == 404
    assert "caregiver.declined" in await _actions(session, pid)


async def test_last_guardian_of_a_dependant_cannot_leave(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    mother, father = await make_account(), await make_account()
    m = await login(api, mother.test_email)
    link = (await _add_dependant(api, m)).json()
    child, rel_id = link["patient_id"], link["relationship_id"]

    assert (await api.post(f"{API}/me/caregiving/{rel_id}/leave", headers=m)).status_code == 409
    # Nor can a guardian revoke themselves through the patient's caregiver list.
    assert (
        await api.delete(f"{API}/patients/{child}/caregivers/{rel_id}", headers=m)
    ).status_code == 403

    invited = await api.post(
        f"{API}/patients/{child}/caregivers",
        json={
            "caregiver_email": father.test_email,
            "relationship_type": "parent",
            "scopes": ["view_medications", "manage_caregivers"],
            "is_guardian": True,
            "guardian_basis": "parent_of_minor",
        },
        headers=m,
    )
    assert invited.status_code == 201, invited.text
    f = await login(api, father.test_email)
    await api.post(
        f"{API}/caregiver-invitations/{invited.json()['relationship_id']}/accept", headers=f
    )

    assert (await api.post(f"{API}/me/caregiving/{rel_id}/leave", headers=m)).status_code == 204
    assert (await api.get(f"{API}/patients/{child}/medications", headers=m)).status_code == 404
    assert (await api.get(f"{API}/patients/{child}/medications", headers=f)).status_code == 200
    assert "caregiver.left" in await _actions(session, uuid.UUID(child))


async def test_revoked_access_ends_immediately_and_is_audited(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    rel_id, headers = await _invite_and_accept(api, owner, pid, carer, ["view_appointments"])
    assert (await api.get(f"{API}/patients/{pid}/appointments", headers=headers)).status_code == 200
    assert (
        await api.delete(f"{API}/patients/{pid}/caregivers/{rel_id}", headers=owner)
    ).status_code == 204
    assert (await api.get(f"{API}/patients/{pid}/appointments", headers=headers)).status_code == 404
    dash = (await api.get(f"{API}/me/caregiving/dashboard", headers=headers)).json()
    assert dash["people"] == []
    actions = await _actions(session, pid)
    for expected in ["caregiver.invited", "caregiver.accepted", "caregiver.revoked"]:
        assert expected in actions


# --- dashboard and patient-side transparency ---------------------------------------------------


async def test_dashboard_shows_only_shared_sections(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    _, headers = await _invite_and_accept(api, owner, pid, carer, ["view_appointments"])

    dash = await api.get(f"{API}/me/caregiving/dashboard", headers=headers)
    assert dash.status_code == 200, dash.text
    (person,) = dash.json()["people"]
    assert person["patient_id"] == str(pid)
    assert person["appointments"] == []
    assert person["follow_ups"] == []
    assert person["today_doses"] is None
    assert person["missed_doses"] is None
    assert person["recent_reports"] is None
    views = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.patient_id == pid, AuditLog.action == "caregiver.dashboard_view"
            )
        )
    ).all()
    assert views
    assert views[-1].context["sections"] == "appointments"


async def test_patient_sees_caregiver_activity_but_caregiver_cannot(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    _, headers = await _invite_and_accept(api, owner, pid, carer, ["view_medications"])
    await api.get(f"{API}/patients/{pid}/medications", headers=headers)

    activity = await api.get(f"{API}/patients/{pid}/caregivers/activity", headers=owner)
    assert activity.status_code == 200
    actions = {row["action"] for row in activity.json()}
    assert {"caregiver.accepted", "medication.list"} <= actions
    assert all(row["caregiver_user_id"] == str(carer.id) for row in activity.json())
    denied = await api.get(f"{API}/patients/{pid}/caregivers/activity", headers=headers)
    assert denied.status_code == 403
