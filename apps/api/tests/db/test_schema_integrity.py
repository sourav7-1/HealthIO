"""Constraints and triggers behave as documented in docs/data-model.md."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordSource, VerificationStatus
from app.modules.appointments.models import Appointment, AppointmentMode, AppointmentStatus
from app.modules.audit.models import AuditLog, AuditOutcome
from app.modules.audit.service import AuditEvent, record_event, truncate_ip, verify_chain
from app.modules.care_team.models import DoctorPatientRelationship, RelationshipStatus
from app.modules.caregivers.models import (
    CaregiverPermission,
    CaregiverPermissionScope,
    CaregiverRelationship,
    CaregiverRelationshipType,
    CaregiverStatus,
)
from app.modules.clinical.models import (
    ClinicalNote,
    DoctorVisit,
    NoteStatus,
    NoteType,
    VisitStatus,
    VisitType,
)
from app.modules.consent.models import (
    ConsentPurpose,
    ConsentRecord,
    ConsentStatus,
    GranteeType,
    GrantorCapacity,
)
from app.modules.identity.models import User
from app.modules.medications.models import (
    DoseRecordedVia,
    DoseStatus,
    Medication,
    MedicationAdherence,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleType,
)
from app.modules.prescriptions.models import (
    Prescription,
    PrescriptionItem,
    PrescriptionSource,
    PrescriptionStatus,
)
from tests.db.conftest import (
    BAD_TRANSITION,
    CHECK_VIOLATION,
    EXCLUSION_VIOLATION,
    FK_VIOLATION,
    IMMUTABLE,
    UNIQUE_VIOLATION,
    Builder,
    expect_db_error,
)

pytestmark = pytest.mark.integration
NOW = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)


async def _visit(session: AsyncSession, patient_id, doctor_id) -> DoctorVisit:  # type: ignore[no-untyped-def]
    visit = DoctorVisit(
        patient_id=patient_id,
        doctor_id=doctor_id,
        visit_type=VisitType.IN_PERSON,
        status=VisitStatus.IN_PROGRESS,
        started_at=NOW,
    )
    session.add(visit)
    await session.flush()
    return visit


async def _draft_prescription(session: AsyncSession, patient_id, doctor_id) -> Prescription:  # type: ignore[no-untyped-def]
    rx = Prescription(
        patient_id=patient_id,
        prescriber_doctor_id=doctor_id,
        source=PrescriptionSource.DOCTOR_ISSUED,
        prescribed_on=NOW.date(),
    )
    session.add(rx)
    await session.flush()
    session.add(
        PrescriptionItem(
            patient_id=patient_id,
            prescription_id=rx.id,
            sequence=1,
            drug_name="Test Medicine A",
            dose_amount=Decimal("1"),
            dose_unit="unit",
        )
    )
    await session.flush()
    return rx


# --- ownership boundaries ------------------------------------------------------------


async def test_many_to_many_doctors_and_patients(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    p1, p2 = await make_patient(), await make_patient()
    d1, d2 = await make_doctor(), await make_doctor()
    for d in (d1, d2):
        for p in (p1, p2):
            session.add(
                DoctorPatientRelationship(
                    doctor_id=d.id,
                    patient_id=p.id,
                    status=RelationshipStatus.ACTIVE,
                    initiated_by=d.user_id,
                    started_at=NOW,
                )
            )
    await session.flush()

    # Only one open relationship per pair; ended ones are kept as history.
    async with expect_db_error(session, UNIQUE_VIOLATION):
        session.add(
            DoctorPatientRelationship(
                doctor_id=d1.id,
                patient_id=p1.id,
                status=RelationshipStatus.PENDING_PATIENT,
                initiated_by=d1.user_id,
            )
        )


async def test_child_rows_cannot_point_at_another_patients_parent(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    alice, bob = await make_patient(), await make_patient()
    doctor = await make_doctor()
    rx = await _draft_prescription(session, alice.id, doctor.id)

    async with expect_db_error(session, FK_VIOLATION):
        session.add(
            PrescriptionItem(
                patient_id=bob.id, prescription_id=rx.id, sequence=2, drug_name="Test Medicine B"
            )
        )


async def test_dependant_patient_without_login_and_guardian_permissions(
    session: AsyncSession, make_patient: Builder, make_user: Builder
) -> None:
    child = await make_patient(dependant=True)
    assert child.user_id is None
    guardian = await make_user()
    rel = CaregiverRelationship(
        patient_id=child.id,
        caregiver_user_id=guardian.id,
        relationship_type=CaregiverRelationshipType.PARENT,
        is_guardian=True,
        guardian_basis="parent of minor",
        status=CaregiverStatus.ACTIVE,
        invited_by=guardian.id,
        accepted_at=NOW,
    )
    session.add(rel)
    await session.flush()
    grant = CaregiverPermission(
        patient_id=child.id,
        relationship_id=rel.id,
        scope=CaregiverPermissionScope.VIEW_MEDICATIONS,
        granted_by=guardian.id,
    )
    session.add(grant)
    await session.flush()

    async with expect_db_error(session, UNIQUE_VIOLATION):  # one active grant per scope
        session.add(
            CaregiverPermission(
                patient_id=child.id,
                relationship_id=rel.id,
                scope=CaregiverPermissionScope.VIEW_MEDICATIONS,
                granted_by=guardian.id,
            )
        )

    # Revoking keeps the row (history) and allows a later re-grant.
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = guardian.id
    await session.flush()
    session.add(
        CaregiverPermission(
            patient_id=child.id,
            relationship_id=rel.id,
            scope=CaregiverPermissionScope.VIEW_MEDICATIONS,
            granted_by=guardian.id,
        )
    )
    await session.flush()

    async with expect_db_error(session, CHECK_VIOLATION):  # guardian needs a basis
        session.add(
            CaregiverRelationship(
                patient_id=child.id,
                caregiver_user_id=(await make_user()).id,
                relationship_type=CaregiverRelationshipType.OTHER,
                is_guardian=True,
                invited_by=guardian.id,
            )
        )


# --- immutable clinical records ------------------------------------------------------


async def test_signed_note_is_immutable_and_amended_by_supersession(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    visit = await _visit(session, patient.id, doctor.id)
    note = ClinicalNote(
        patient_id=patient.id,
        visit_id=visit.id,
        author_doctor_id=doctor.id,
        note_type=NoteType.PROGRESS,
        body="placeholder note text",
    )
    session.add(note)
    await session.flush()

    note.body = "edited while draft"  # drafts are editable
    note.status = NoteStatus.SIGNED
    note.signed_at = NOW
    note.signed_by = doctor.user_id
    await session.flush()

    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            update(ClinicalNote)
            .where(ClinicalNote.id == note.id)
            .values(body="rewritten after signing")
            .execution_options(synchronize_session=False)
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(text("DELETE FROM clinical_notes WHERE id = :id"), {"id": note.id})

    amendment = ClinicalNote(
        patient_id=patient.id,
        visit_id=visit.id,
        author_doctor_id=doctor.id,
        note_type=NoteType.PROGRESS,
        body="corrected text",
        supersedes_note_id=note.id,
        amendment_reason="typo",
    )
    session.add(amendment)
    await session.execute(
        text("UPDATE clinical_notes SET status = 'superseded' WHERE id = :id"), {"id": note.id}
    )
    await session.flush()

    async with expect_db_error(session, BAD_TRANSITION):
        await session.execute(
            text("UPDATE clinical_notes SET status = 'signed' WHERE id = :id"), {"id": note.id}
        )


async def test_issued_prescription_and_items_are_frozen(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    rx = await _draft_prescription(session, patient.id, doctor.id)
    await session.execute(
        text("UPDATE prescriptions SET status = 'issued', issued_at = now() WHERE id = :id"),
        {"id": rx.id},
    )

    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            text("UPDATE prescriptions SET advice = 'x' WHERE id = :id"), {"id": rx.id}
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            text("UPDATE prescription_items SET dose_amount = 2 WHERE prescription_id = :id"),
            {"id": rx.id},
        )
    async with expect_db_error(session, IMMUTABLE):
        session.add(
            PrescriptionItem(
                patient_id=patient.id, prescription_id=rx.id, sequence=2, drug_name="Late add"
            )
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(text("DELETE FROM prescriptions WHERE id = :id"), {"id": rx.id})

    # Cancellation is the one allowed change, and it is final.
    await session.execute(
        text(
            "UPDATE prescriptions SET status = 'cancelled', cancelled_at = now(), "
            "cancel_reason = 'test' WHERE id = :id"
        ),
        {"id": rx.id},
    )
    async with expect_db_error(session, BAD_TRANSITION):
        await session.execute(
            text("UPDATE prescriptions SET status = 'issued', cancelled_at = NULL WHERE id = :id"),
            {"id": rx.id},
        )


async def test_draft_prescription_can_be_discarded_with_items(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    rx = await _draft_prescription(session, patient.id, doctor.id)
    await session.execute(text("DELETE FROM prescriptions WHERE id = :id"), {"id": rx.id})
    remaining = await session.scalar(
        text("SELECT count(*) FROM prescription_items WHERE prescription_id = :id"), {"id": rx.id}
    )
    assert remaining == 0


async def test_uploaded_prescription_needs_human_verification_to_be_recorded(
    session: AsyncSession, make_patient: Builder
) -> None:
    patient = await make_patient()
    rx = Prescription(
        patient_id=patient.id,
        source=PrescriptionSource.MANUAL_ENTRY,
        status=PrescriptionStatus.RECORDED,  # without verification: rejected
    )
    async with expect_db_error(session, CHECK_VIOLATION):
        session.add(rx)
    rx2 = Prescription(
        patient_id=patient.id,
        source=PrescriptionSource.MANUAL_ENTRY,
        status=PrescriptionStatus.RECORDED,
        verification_status=VerificationStatus.PATIENT_VERIFIED,
        verified_at=NOW,
        verified_by=patient.user_id,
    )
    session.add(rx2)
    await session.flush()


# --- medications and doses -----------------------------------------------------------


async def test_medication_chain_and_dose_integrity(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    rx = await _draft_prescription(session, patient.id, doctor.id)
    item_id = await session.scalar(
        select(PrescriptionItem.id).where(PrescriptionItem.prescription_id == rx.id)
    )

    async with expect_db_error(session, CHECK_VIOLATION):  # cannot be active unconfirmed
        session.add(
            Medication(
                patient_id=patient.id,
                source=MedicationSource.PRESCRIPTION,
                prescription_item_id=item_id,
                name="Test Medicine A",
                status=MedicationStatus.ACTIVE,
            )
        )

    med = Medication(
        patient_id=patient.id,
        source=MedicationSource.PRESCRIPTION,
        prescription_item_id=item_id,
        name="Test Medicine A",
    )
    other = Medication(patient_id=patient.id, source=MedicationSource.SELF_REPORTED, name="B")
    session.add_all([med, other])
    await session.flush()
    schedule = MedicationSchedule(
        patient_id=patient.id,
        medication_id=med.id,
        schedule_type=ScheduleType.FIXED_TIMES,
        times_of_day=[time(8, 0), time(20, 0)],
        timezone="Asia/Kolkata",
        effective_from=NOW,
    )
    session.add(schedule)
    await session.flush()

    session.add(
        MedicationDose(
            patient_id=patient.id,
            medication_id=med.id,
            schedule_id=schedule.id,
            scheduled_at=NOW,
        )
    )
    await session.flush()

    async with expect_db_error(session, UNIQUE_VIOLATION):  # idempotent materialisation
        session.add(
            MedicationDose(
                patient_id=patient.id,
                medication_id=med.id,
                schedule_id=schedule.id,
                scheduled_at=NOW,
            )
        )
    async with expect_db_error(session, FK_VIOLATION):  # schedule belongs to another medication
        session.add(
            MedicationDose(
                patient_id=patient.id,
                medication_id=other.id,
                schedule_id=schedule.id,
                scheduled_at=NOW + timedelta(hours=1),
            )
        )
    async with expect_db_error(session, CHECK_VIOLATION):  # taken requires taken_at
        session.add(
            MedicationDose(
                patient_id=patient.id,
                medication_id=med.id,
                schedule_id=schedule.id,
                scheduled_at=NOW + timedelta(hours=12),
                status=DoseStatus.TAKEN,
                recorded_via=DoseRecordedVia.PATIENT_APP,
            )
        )

    session.add(  # PRN dose: no schedule, logged as taken
        MedicationDose(
            patient_id=patient.id,
            medication_id=other.id,
            status=DoseStatus.TAKEN,
            taken_at=NOW,
            recorded_via=DoseRecordedVia.CAREGIVER_APP,
        )
    )
    await session.flush()

    adherence = MedicationAdherence(
        patient_id=patient.id,
        medication_id=med.id,
        day=date(2030, 1, 15),
        scheduled_count=4,
        taken_count=3,
        taken_late_count=1,
        skipped_count=0,
        missed_count=1,
    )
    session.add(adherence)
    await session.flush()
    ratio = await session.scalar(
        select(MedicationAdherence.adherence_ratio).where(MedicationAdherence.id == adherence.id)
    )
    assert ratio == Decimal("0.7500")


# --- appointments --------------------------------------------------------------------


async def test_doctor_cannot_be_double_booked(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    p1, p2, doctor = await make_patient(), await make_patient(), await make_doctor()

    def appt(patient_id, start: datetime, status=AppointmentStatus.SCHEDULED) -> Appointment:  # type: ignore[no-untyped-def]
        return Appointment(
            patient_id=patient_id,
            doctor_id=doctor.id,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            mode=AppointmentMode.IN_PERSON,
            status=status,
            booked_by=doctor.user_id,
            cancelled_at=NOW if status == AppointmentStatus.CANCELLED else None,
        )

    session.add(appt(p1.id, NOW))
    await session.flush()
    async with expect_db_error(session, EXCLUSION_VIOLATION):
        session.add(appt(p2.id, NOW + timedelta(minutes=15)))

    session.add(appt(p2.id, NOW + timedelta(minutes=30)))  # back-to-back is fine
    session.add(appt(p2.id, NOW + timedelta(minutes=10), AppointmentStatus.CANCELLED))
    await session.flush()


# --- consent -------------------------------------------------------------------------


async def test_consent_scope_is_immutable_and_withdrawal_is_final(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    consent = ConsentRecord(
        patient_id=patient.id,
        granted_by=patient.user_id,
        grantor_capacity=GrantorCapacity.SELF,
        grantee_type=GranteeType.DOCTOR,
        grantee_user_id=doctor.user_id,
        purpose=ConsentPurpose.CARE_DELIVERY,
        data_categories=["medications", "allergies"],
        notice_version="test-1",
    )
    session.add(consent)
    await session.flush()

    async with expect_db_error(session, CHECK_VIOLATION):
        session.add(
            ConsentRecord(
                patient_id=patient.id,
                granted_by=patient.user_id,
                grantor_capacity=GrantorCapacity.SELF,
                grantee_type=GranteeType.DOCTOR,
                grantee_user_id=doctor.user_id,
                purpose=ConsentPurpose.CARE_DELIVERY,
                data_categories=["not_a_category"],
                notice_version="test-1",
            )
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            text("UPDATE consent_records SET data_categories = '{documents}' WHERE id = :id"),
            {"id": consent.id},
        )

    await session.execute(
        update(ConsentRecord)
        .where(ConsentRecord.id == consent.id)
        .values(status=ConsentStatus.WITHDRAWN, withdrawn_at=NOW, withdrawn_by=patient.user_id)
    )
    async with expect_db_error(session, BAD_TRANSITION):
        await session.execute(
            text(
                "UPDATE consent_records SET status = 'active', withdrawn_at = NULL, "
                "withdrawn_by = NULL WHERE id = :id"
            ),
            {"id": consent.id},
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            text("DELETE FROM consent_records WHERE id = :id"), {"id": consent.id}
        )


# --- privacy -------------------------------------------------------------------------


async def test_sensitive_columns_are_ciphertext_at_rest(
    session: AsyncSession, make_patient: Builder, make_doctor: Builder
) -> None:
    patient, doctor = await make_patient(), await make_doctor()
    visit = await _visit(session, patient.id, doctor.id)
    note = ClinicalNote(
        patient_id=patient.id,
        visit_id=visit.id,
        author_doctor_id=doctor.id,
        note_type=NoteType.OTHER,
        body="placeholder sensitive text",
    )
    session.add(note)
    await session.flush()

    raw = await session.scalar(
        text("SELECT body FROM clinical_notes WHERE id = :id"), {"id": note.id}
    )
    assert raw.startswith("v1.")
    assert "placeholder" not in raw
    await session.refresh(note)  # reload from the DB: decrypts transparently
    assert note.body == "placeholder sensitive text"


async def test_history_rows_resist_hard_delete_except_erasure(
    session: AsyncSession, make_user: Builder
) -> None:
    user = await make_user()
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})

    await session.execute(text("SET LOCAL hio.allow_hard_delete = 'on'"))
    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    assert await session.scalar(select(User.id).where(User.id == user.id)) is None


async def test_updated_at_is_maintained_by_the_database(
    session: AsyncSession, make_user: Builder
) -> None:
    user = await make_user()
    await session.execute(
        text(
            "UPDATE users SET created_at = now() - interval '1 day', "
            "updated_at = now() - interval '1 day' WHERE id = :id"
        ),
        {"id": user.id},
    )
    # Raw SQL that forgets updated_at still gets it refreshed by the trigger.
    await session.execute(
        text("UPDATE users SET display_name = 'x' WHERE id = :id"), {"id": user.id}
    )
    fresh = await session.scalar(
        text("SELECT updated_at > created_at + interval '1 hour' FROM users WHERE id = :id"),
        {"id": user.id},
    )
    assert fresh is True


# --- audit ---------------------------------------------------------------------------


async def test_audit_chain_is_append_only_and_tamper_evident(
    session: AsyncSession, make_patient: Builder, make_user: Builder
) -> None:
    patient, actor = await make_patient(), await make_user()
    rows = [
        await record_event(
            session,
            AuditEvent(
                action=f"test.action_{i}",
                outcome=AuditOutcome.ALLOWED,
                actor_user_id=actor.id,
                actor_role="doctor",
                patient_id=patient.id,
                resource_type="test",
                changed_fields=["b", "a"],
                ip_address="203.0.113.77",
                user_agent="placeholder-agent",
                justification="placeholder reason" if i == 1 else None,
            ),
        )
        for i in range(3)
    ]
    assert rows[1].prev_hash == rows[0].hash
    assert rows[0].ip_address == "203.0.113.0"  # truncated to /24
    assert rows[0].changed_fields == ["a", "b"]
    assert (await verify_chain(session)).ok

    async with expect_db_error(session, IMMUTABLE):
        await session.execute(
            text("UPDATE audit_logs SET action = 'x' WHERE id = :id"), {"id": rows[1].id}
        )
    async with expect_db_error(session, IMMUTABLE):
        await session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": rows[1].id})

    # Even someone able to bypass the trigger (a superuser) cannot edit history unnoticed.
    await session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER USER"))
    await session.execute(
        text("UPDATE audit_logs SET action = 'test.forged' WHERE id = :id"), {"id": rows[1].id}
    )
    await session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER USER"))
    session.expire_all()
    result = await verify_chain(session)
    assert not result.ok
    assert result.first_bad_seq == rows[1].seq


def test_truncate_ip() -> None:
    assert truncate_ip("2001:db8:abcd:12::1") == "2001:db8:abcd::"
    assert truncate_ip(None) is None


async def test_audit_log_has_no_updated_columns(session: AsyncSession) -> None:
    cols = set(AuditLog.__table__.c.keys())
    assert "updated_at" not in cols
    assert "deleted_at" not in cols


async def test_record_source_values_are_enforced(
    session: AsyncSession, make_patient: Builder
) -> None:
    patient = await make_patient()
    async with expect_db_error(session, CHECK_VIOLATION):
        await session.execute(
            text(
                "INSERT INTO medical_conditions (id, patient_id, name, clinical_status, "
                "verification_status, source, version) VALUES (gen_random_uuid(), :p, 'x', "
                "'active', 'confirmed', 'guessed_by_ai', 1)"
            ),
            {"p": patient.id},
        )
    assert RecordSource.AI_EXTRACTION.value == "ai_extraction"
