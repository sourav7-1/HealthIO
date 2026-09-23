"""Authorization: roles, patient ownership, doctor boundaries, caregiver permissions."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.modules.access.dependencies import require_patient_permission
from app.modules.access.permissions import (
    CAREGIVER_GRANTABLE,
    NEVER_FOR_CAREGIVERS,
    Permission,
)
from app.modules.access.service import PatientAccess
from app.modules.audit.models import AuditLog
from app.modules.care_team.models import (
    DoctorPatientRelationship,
    DoctorProfile,
    DoctorVerificationStatus,
    RelationshipStatus,
)
from app.modules.consent.models import (
    ConsentPurpose,
    ConsentRecord,
    GranteeType,
    GrantorCapacity,
)
from app.modules.patients.models import PatientProfile
from tests.db.conftest import CHECK_VIOLATION, Builder, expect_db_error, login

pytestmark = pytest.mark.integration

API = "/api/v1"
CLINICAL_WRITES = [
    Permission.EDIT_CLINICAL_RECORDS,
    Permission.CHANGE_DOCTOR_PRESCRIPTION,
    Permission.DELETE_MEDICAL_RECORDS,
]
PROBED = [
    Permission.VIEW_MEDICATIONS,
    Permission.MANAGE_REMINDERS,
    Permission.VIEW_APPOINTMENTS,
    Permission.UPLOAD_REPORTS,
    Permission.VIEW_MEDICAL_HISTORY,
    Permission.VIEW_REPORTS,
    *CLINICAL_WRITES,
]


@pytest.fixture
def probe_routes(api_app: Any) -> None:
    """Test-only endpoints, one per permission, standing in for future medical features."""
    for perm in PROBED:

        def make(p: Permission) -> Any:
            async def probe(
                patient_id: uuid.UUID,
                access: PatientAccess = Depends(require_patient_permission(p)),
            ) -> dict[str, str]:
                return {"ok": p.value}

            return probe

        api_app.add_api_route(f"{API}/_probe/{perm.value}/{{patient_id}}", make(perm))


async def probe(api: Any, perm: Permission, patient_id: uuid.UUID, headers: dict[str, str]) -> int:
    resp = await api.get(f"{API}/_probe/{perm.value}/{patient_id}", headers=headers)
    return int(resp.status_code)


async def patient_id_of(session: AsyncSession, user: Any) -> uuid.UUID:
    pid = await session.scalar(select(PatientProfile.id).where(PatientProfile.user_id == user.id))
    assert pid is not None
    return pid


async def link_doctor(
    session: AsyncSession,
    doctor_user: Any,
    patient_id: uuid.UUID,
    *,
    verified: bool = True,
    status: RelationshipStatus = RelationshipStatus.ACTIVE,
    categories: list[str] | None = None,
) -> DoctorProfile:
    profile = DoctorProfile(
        user_id=doctor_user.id,
        display_name="Dr Placeholder",
        registration_council="Test Council",
        registration_number=uuid.uuid4().hex[:10],
        verification_status=(
            DoctorVerificationStatus.VERIFIED if verified else DoctorVerificationStatus.PENDING
        ),
        verified_at=datetime.now(UTC) if verified else None,
        verified_by=doctor_user.id if verified else None,
    )
    session.add(profile)
    await session.flush()
    active = status == RelationshipStatus.ACTIVE
    session.add(
        DoctorPatientRelationship(
            doctor_id=profile.id,
            patient_id=patient_id,
            status=status,
            initiated_by=doctor_user.id,
            started_at=datetime.now(UTC) - timedelta(days=1)
            if status != RelationshipStatus.PENDING_PATIENT
            else None,
            ended_at=None
            if active or status == RelationshipStatus.PENDING_PATIENT
            else datetime.now(UTC),
        )
    )
    if categories:
        owner = await session.scalar(
            select(PatientProfile.user_id).where(PatientProfile.id == patient_id)
        )
        session.add(
            ConsentRecord(
                patient_id=patient_id,
                granted_by=owner,
                grantor_capacity=GrantorCapacity.SELF,
                grantee_type=GranteeType.DOCTOR,
                grantee_user_id=doctor_user.id,
                purpose=ConsentPurpose.CARE_DELIVERY,
                data_categories=categories,
                notice_version="test-1",
            )
        )
    await session.flush()
    return profile


async def denied_reasons(session: AsyncSession) -> list[str | None]:
    rows = await session.scalars(
        select(AuditLog.reason_code)
        .where(AuditLog.action == "access.denied")
        .order_by(AuditLog.seq)
    )
    return list(rows.all())


# --- the permission catalogue ----------------------------------------------------------


def test_caregivers_can_never_be_granted_clinical_write_permissions() -> None:
    for perm in CLINICAL_WRITES:
        assert perm not in CAREGIVER_GRANTABLE
        assert perm in NEVER_FOR_CAREGIVERS


# --- authentication required -----------------------------------------------------------


async def test_protected_routes_require_authentication(api: Any) -> None:
    for method, path in [
        ("GET", f"{API}/me"),
        ("GET", f"{API}/patients/{uuid.uuid4()}/access"),
        ("GET", f"{API}/doctor/patients"),
        ("POST", f"{API}/admin/doctors/{uuid.uuid4()}/verify"),
    ]:
        resp = await api.request(method, path, json={} if method == "POST" else None)
        assert resp.status_code == 401, path


# --- role restrictions -----------------------------------------------------------------


async def test_only_admins_can_verify_doctors(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    admin = await make_account(Role.ADMIN)
    patient = await make_account(Role.PATIENT)
    doctor = await make_account(Role.DOCTOR)
    pending = DoctorProfile(
        user_id=doctor.id,
        display_name="Dr Pending",
        registration_council="Test Council",
        registration_number="REG-0001",
        verification_status=DoctorVerificationStatus.PENDING,
    )
    session.add(pending)
    await session.flush()
    url = f"{API}/admin/doctors/{pending.id}/verify"

    for user in (patient, doctor):
        resp = await api.post(url, json={}, headers=await login(api, user.test_email))
        assert resp.status_code == 403
    assert (await denied_reasons(session)).count("missing_role") == 2

    ok = await api.post(url, json={"notes": "checked"}, headers=await login(api, admin.test_email))
    assert ok.status_code == 200
    assert ok.json()["verification_status"] == "verified"
    again = await api.post(url, json={}, headers=await login(api, admin.test_email))
    assert again.status_code == 409


async def test_doctor_patient_list_is_doctor_only_and_scoped(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    doctor = await make_account(Role.DOCTOR)
    mine, other = await make_account(Role.PATIENT), await make_account(Role.PATIENT)
    await link_doctor(session, doctor, await patient_id_of(session, mine))
    patient_headers = await login(api, mine.test_email)
    assert (await api.get(f"{API}/doctor/patients", headers=patient_headers)).status_code == 403

    listed = await api.get(f"{API}/doctor/patients", headers=await login(api, doctor.test_email))
    assert listed.status_code == 200
    ids = {p["patient_id"] for p in listed.json()}
    assert ids == {str(await patient_id_of(session, mine))}
    assert str(await patient_id_of(session, other)) not in ids


async def test_admin_role_grants_no_patient_data(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    admin = await make_account(Role.ADMIN)
    patient = await make_account(Role.PATIENT)
    pid = await patient_id_of(session, patient)
    headers = await login(api, admin.test_email)
    assert (await api.get(f"{API}/patients/{pid}/access", headers=headers)).status_code == 404
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, headers) == 404


# --- patient ownership -----------------------------------------------------------------


async def test_patients_see_only_their_own_record(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    alice, bob = await make_account(Role.PATIENT), await make_account(Role.PATIENT)
    alice_id, bob_id = await patient_id_of(session, alice), await patient_id_of(session, bob)
    headers = await login(api, alice.test_email)

    own = await api.get(f"{API}/patients/{alice_id}/access", headers=headers)
    assert own.status_code == 200
    assert own.json()["via"] == ["self"]
    assert await probe(api, Permission.VIEW_MEDICATIONS, alice_id, headers) == 200
    # Patients cannot edit what doctors wrote, or delete medical records.
    for perm in CLINICAL_WRITES:
        assert await probe(api, perm, alice_id, headers) == 403

    # Another patient's record: indistinguishable from one that does not exist.
    other = await api.get(f"{API}/patients/{bob_id}/access", headers=headers)
    missing = await api.get(f"{API}/patients/{uuid.uuid4()}/access", headers=headers)
    assert other.status_code == missing.status_code == 404
    assert other.json()["type"] == missing.json()["type"]
    assert await probe(api, Permission.VIEW_MEDICATIONS, bob_id, headers) == 404

    reasons = await denied_reasons(session)
    assert "no_relationship" in reasons
    assert "unknown_patient" in reasons
    # The denial is recorded against Bob, so he can later see who tried to access his data.
    subject = await session.scalar(
        select(AuditLog.patient_id).where(AuditLog.reason_code == "no_relationship").limit(1)
    )
    assert subject == bob_id


# --- doctor / patient ownership boundaries ------------------------------------------------


async def test_doctor_access_needs_verification_active_link_and_consent(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    patient = await make_account(Role.PATIENT)
    pid = await patient_id_of(session, patient)

    # Unlinked doctor: nothing, not even existence.
    stranger = await make_account(Role.DOCTOR)
    other_pid = await patient_id_of(session, await make_account(Role.PATIENT))
    await link_doctor(session, stranger, other_pid, categories=["medications"])
    assert (
        await probe(api, Permission.VIEW_MEDICATIONS, pid, await login(api, stranger.test_email))
        == 404
    )

    # Linked but not yet verified by an admin: treated as unlinked.
    unverified = await make_account(Role.DOCTOR)
    await link_doctor(session, unverified, pid, verified=False, categories=["medications"])
    assert (
        await probe(api, Permission.VIEW_MEDICATIONS, pid, await login(api, unverified.test_email))
        == 404
    )

    # Relationship ended: access ends.
    former = await make_account(Role.DOCTOR)
    await link_doctor(
        session, former, pid, status=RelationshipStatus.ENDED, categories=["medications"]
    )
    assert (
        await probe(api, Permission.VIEW_MEDICATIONS, pid, await login(api, former.test_email))
        == 404
    )

    # Verified, active link, consent limited to medications and prescriptions.
    treating = await make_account(Role.DOCTOR)
    await link_doctor(session, treating, pid, categories=["medications", "prescriptions"])
    headers = await login(api, treating.test_email)
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, headers) == 200
    assert await probe(api, Permission.CHANGE_DOCTOR_PRESCRIPTION, pid, headers) == 200
    # Not consented: visible relationship, so 403 rather than 404.
    assert await probe(api, Permission.VIEW_MEDICAL_HISTORY, pid, headers) == 403
    assert await probe(api, Permission.EDIT_CLINICAL_RECORDS, pid, headers) == 403
    # Nobody deletes medical records through the API.
    assert await probe(api, Permission.DELETE_MEDICAL_RECORDS, pid, headers) == 403

    access = (await api.get(f"{API}/patients/{pid}/access", headers=headers)).json()
    assert access["via"] == ["doctor"]
    assert set(access["permissions"]) == {
        "view_medications",
        "view_prescriptions",
        "change_doctor_prescription",
    }


async def test_consent_withdrawal_takes_effect_immediately(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    patient, doctor = await make_account(Role.PATIENT), await make_account(Role.DOCTOR)
    pid = await patient_id_of(session, patient)
    await link_doctor(session, doctor, pid, categories=["medications"])
    headers = await login(api, doctor.test_email)
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, headers) == 200
    await session.execute(
        text(
            "UPDATE consent_records SET status = 'withdrawn', withdrawn_at = now(), "
            "withdrawn_by = :u WHERE patient_id = :p"
        ),
        {"u": patient.id, "p": pid},
    )
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, headers) == 403


async def test_doctor_role_without_doctor_link_cannot_act_as_doctor_on_own_record(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    """A doctor who is also a patient gets patient (not doctor) rights on their own record."""
    both = await make_account(Role.DOCTOR, Role.PATIENT)
    pid = await patient_id_of(session, both)
    headers = await login(api, both.test_email)
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, headers) == 200
    assert await probe(api, Permission.EDIT_CLINICAL_RECORDS, pid, headers) == 403


# --- caregiver permissions ---------------------------------------------------------------


GRANTED = [
    "view_medications",
    "manage_reminders",
    "view_appointments",
    "upload_reports",
    "view_medical_history",
]


async def _invite(
    api: Any, headers: dict[str, str], pid: uuid.UUID, email: str, scopes: list[str], **extra: Any
) -> Any:
    return await api.post(
        f"{API}/patients/{pid}/caregivers",
        json={"caregiver_email": email, "relationship_type": "child", "scopes": scopes, **extra},
        headers=headers,
    )


async def test_caregiver_gets_exactly_the_granted_permissions(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    invited = await _invite(api, owner, pid, carer.test_email, GRANTED)
    assert invited.status_code == 201, invited.text
    rel_id = invited.json()["relationship_id"]
    assert invited.json()["status"] == "invited"

    carer_headers = await login(api, carer.test_email)
    # An open invitation grants nothing.
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, carer_headers) == 404

    accepted = await api.post(f"{API}/caregiver-invitations/{rel_id}/accept", headers=carer_headers)
    assert accepted.status_code == 200
    me = (await api.get(f"{API}/me", headers=carer_headers)).json()
    assert "caregiver" in me["roles"]

    access = (await api.get(f"{API}/patients/{pid}/access", headers=carer_headers)).json()
    assert access["via"] == ["caregiver"]
    assert sorted(access["permissions"]) == sorted(GRANTED)
    for perm in [Permission(p) for p in GRANTED]:
        assert await probe(api, perm, pid, carer_headers) == 200, perm
    # Not granted, and never grantable.
    assert await probe(api, Permission.VIEW_REPORTS, pid, carer_headers) == 403
    for perm in CLINICAL_WRITES:
        assert await probe(api, perm, pid, carer_headers) == 403, perm
    # A caregiver cannot manage the patient's caregivers or its own grant.
    assert (
        await api.get(f"{API}/patients/{pid}/caregivers", headers=carer_headers)
    ).status_code == 403


async def test_clinical_write_permissions_cannot_be_granted_to_caregivers(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    for forbidden in [
        "edit_clinical_records",
        "change_doctor_prescription",
        "delete_medical_records",
    ]:
        resp = await _invite(api, owner, pid, carer.test_email, ["view_medications", forbidden])
        assert resp.status_code == 422, forbidden
    # Non-guardians cannot manage other caregivers.
    guardian_only = await _invite(api, owner, pid, carer.test_email, ["manage_caregivers"])
    assert guardian_only.status_code == 422

    # The database refuses such scopes too, whatever the code does.
    rel = await _invite(api, owner, pid, carer.test_email, ["view_medications"])
    async with expect_db_error(session, CHECK_VIOLATION):
        await session.execute(
            text(
                "INSERT INTO caregiver_permissions (id, patient_id, relationship_id, scope, "
                "granted_by) VALUES (gen_random_uuid(), :p, :r, 'edit_clinical_records', :u)"
            ),
            {"p": pid, "r": rel.json()["relationship_id"], "u": patient.id},
        )


async def test_changing_and_revoking_caregiver_permissions(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    rel_id = (await _invite(api, owner, pid, carer.test_email, GRANTED)).json()["relationship_id"]
    carer_headers = await login(api, carer.test_email)
    await api.post(f"{API}/caregiver-invitations/{rel_id}/accept", headers=carer_headers)

    narrowed = await api.put(
        f"{API}/patients/{pid}/caregivers/{rel_id}/scopes",
        json={"scopes": ["view_appointments"]},
        headers=owner,
    )
    assert narrowed.status_code == 200
    assert narrowed.json()["scopes"] == ["view_appointments"]
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, carer_headers) == 403
    assert await probe(api, Permission.VIEW_APPOINTMENTS, pid, carer_headers) == 200

    # Revoked permission rows are kept as history.
    history = await session.scalar(
        text("SELECT count(*) FROM caregiver_permissions WHERE relationship_id = :r"), {"r": rel_id}
    )
    assert history == len(GRANTED)

    revoked = await api.delete(f"{API}/patients/{pid}/caregivers/{rel_id}", headers=owner)
    assert revoked.status_code == 204
    assert await probe(api, Permission.VIEW_APPOINTMENTS, pid, carer_headers) == 404
    actions = set(
        (await session.scalars(select(AuditLog.action).where(AuditLog.patient_id == pid))).all()
    )
    assert {
        "caregiver.invited",
        "caregiver.accepted",
        "caregiver.scopes_changed",
        "caregiver.revoked",
    } <= actions


async def test_expired_caregiver_link_grants_nothing(
    api: Any, make_account: Builder, session: AsyncSession, probe_routes: None
) -> None:
    patient, carer = await make_account(Role.PATIENT), await make_account()
    pid = await patient_id_of(session, patient)
    owner = await login(api, patient.test_email)
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    rel_id = (
        await _invite(api, owner, pid, carer.test_email, ["view_medications"], expires_at=soon)
    ).json()["relationship_id"]
    carer_headers = await login(api, carer.test_email)
    await api.post(f"{API}/caregiver-invitations/{rel_id}/accept", headers=carer_headers)
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, carer_headers) == 200
    await session.execute(
        text(
            "UPDATE caregiver_relationships SET created_at = now() - interval '2 days', "
            "expires_at = now() - interval '1 minute' WHERE id = :r"
        ),
        {"r": rel_id},
    )
    assert await probe(api, Permission.VIEW_MEDICATIONS, pid, carer_headers) == 404


async def test_only_the_invited_user_can_accept(
    api: Any, make_account: Builder, session: AsyncSession
) -> None:
    patient, carer, intruder = (
        await make_account(Role.PATIENT),
        await make_account(),
        await make_account(),
    )
    pid = await patient_id_of(session, patient)
    rel_id = (
        await _invite(
            api, await login(api, patient.test_email), pid, carer.test_email, ["view_medications"]
        )
    ).json()["relationship_id"]
    resp = await api.post(
        f"{API}/caregiver-invitations/{rel_id}/accept",
        headers=await login(api, intruder.test_email),
    )
    assert resp.status_code == 404


async def test_guardian_of_a_dependant_can_manage_its_caregivers(
    api: Any,
    make_account: Builder,
    make_patient: Builder,
    session: AsyncSession,
    probe_routes: None,
) -> None:
    from app.modules.caregivers.models import (
        CaregiverPermission,
        CaregiverPermissionScope,
        CaregiverRelationship,
        CaregiverRelationshipType,
        CaregiverStatus,
    )

    child = await make_patient(dependant=True)  # no login of their own
    parent = await make_account(Role.CAREGIVER)
    rel = CaregiverRelationship(
        patient_id=child.id,
        caregiver_user_id=parent.id,
        relationship_type=CaregiverRelationshipType.PARENT,
        is_guardian=True,
        guardian_basis="parent of minor",
        status=CaregiverStatus.ACTIVE,
        invited_by=parent.id,
        accepted_at=datetime.now(UTC),
    )
    session.add(rel)
    await session.flush()
    for scope in (
        CaregiverPermissionScope.MANAGE_CAREGIVERS,
        CaregiverPermissionScope.VIEW_MEDICATIONS,
    ):
        session.add(
            CaregiverPermission(
                patient_id=child.id, relationship_id=rel.id, scope=scope, granted_by=parent.id
            )
        )
    await session.flush()

    headers = await login(api, parent.test_email)
    assert await probe(api, Permission.VIEW_MEDICATIONS, child.id, headers) == 200
    assert await probe(api, Permission.EDIT_CLINICAL_RECORDS, child.id, headers) == 403
    grandparent = await make_account()
    resp = await _invite(api, headers, child.id, grandparent.test_email, ["view_appointments"])
    assert resp.status_code == 201
