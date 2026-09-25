"""Clinical service: visits, clinical notes, and conditions/allergies/history.

Notes are drafts until signed. A draft is visible only to its author. Signed notes are
immutable (DB trigger); a correction is a new note that supersedes the signed one, and
the old note becomes SUPERSEDED only when the amendment is signed.

Diagnoses are recorded exactly as the doctor documents them (clinical and verification
status chosen by the doctor). The platform never infers or suggests a diagnosis.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DatePrecision, RecordSource
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.clinical.models import (
    AllergenCategory,
    Allergy,
    ClinicalNote,
    ConditionClinicalStatus,
    ConditionVerificationStatus,
    DoctorVisit,
    MedicalCondition,
    MedicalHistoryEntry,
    NoteStatus,
    NoteType,
    ReactionSeverity,
    Severity,
    SymptomReport,
    SymptomSeverity,
    SymptomStatus,
    VisitStatus,
    VisitType,
)
from app.modules.timeline.versions import change_reason

# --- visits --------------------------------------------------------------------------


async def list_visits(session: AsyncSession, patient_id: uuid.UUID) -> list[DoctorVisit]:
    rows = await session.scalars(
        select(DoctorVisit)
        .where(DoctorVisit.patient_id == patient_id)
        .order_by(DoctorVisit.started_at.desc().nulls_last(), DoctorVisit.created_at.desc())
    )
    return list(rows.all())


async def get_visit(
    session: AsyncSession, patient_id: uuid.UUID, visit_id: uuid.UUID, *, for_update: bool = False
) -> DoctorVisit:
    stmt = select(DoctorVisit).where(
        DoctorVisit.id == visit_id, DoctorVisit.patient_id == patient_id
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    visit: DoctorVisit | None = await session.scalar(stmt)
    if visit is None:
        raise NotFoundError()
    return visit


async def create_visit(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    visit_type: VisitType,
    started_at: datetime | None,
    chief_complaint: str | None,
    location: str | None,
) -> DoctorVisit:
    visit = DoctorVisit(
        patient_id=patient_id,
        doctor_id=doctor_id,
        visit_type=visit_type,
        status=VisitStatus.IN_PROGRESS,
        started_at=started_at or datetime.now(UTC),
        chief_complaint=chief_complaint,
        location=location,
        created_by=actor,
        updated_by=actor,
    )
    session.add(visit)
    await session.flush()
    return visit


async def complete_visit(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    visit_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
) -> DoctorVisit:
    visit = await get_visit(session, patient_id, visit_id, for_update=True)
    if visit.doctor_id != doctor_id:
        raise ForbiddenError("Only the doctor who recorded this visit can complete it.")
    if visit.status != VisitStatus.IN_PROGRESS:
        raise ConflictError("Only a visit in progress can be completed.")
    visit.status = VisitStatus.COMPLETED
    visit.ended_at = datetime.now(UTC)
    visit.updated_by = actor
    await session.flush()
    return visit


# --- notes ---------------------------------------------------------------------------


async def list_notes(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    viewer_doctor_id: uuid.UUID | None,
    visit_id: uuid.UUID | None = None,
) -> list[ClinicalNote]:
    visible = ClinicalNote.status != NoteStatus.DRAFT
    if viewer_doctor_id is not None:
        visible = or_(visible, ClinicalNote.author_doctor_id == viewer_doctor_id)
    stmt = select(ClinicalNote).where(ClinicalNote.patient_id == patient_id, visible)
    if visit_id is not None:
        stmt = stmt.where(ClinicalNote.visit_id == visit_id)
    rows = await session.scalars(stmt.order_by(ClinicalNote.created_at.desc()))
    return list(rows.all())


async def _note_for_author(
    session: AsyncSession, patient_id: uuid.UUID, note_id: uuid.UUID, doctor_id: uuid.UUID
) -> ClinicalNote:
    note: ClinicalNote | None = await session.scalar(
        select(ClinicalNote)
        .where(ClinicalNote.id == note_id, ClinicalNote.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # Someone else's draft is indistinguishable from a missing note.
    if note is None or (note.status == NoteStatus.DRAFT and note.author_doctor_id != doctor_id):
        raise NotFoundError()
    return note


async def create_note(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    visit_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    note_type: NoteType,
    body: str,
) -> ClinicalNote:
    visit = await get_visit(session, patient_id, visit_id)
    if visit.status in (VisitStatus.CANCELLED, VisitStatus.ENTERED_IN_ERROR):
        raise ConflictError("Notes cannot be added to a cancelled visit.")
    note = ClinicalNote(
        patient_id=patient_id,
        visit_id=visit_id,
        author_doctor_id=doctor_id,
        note_type=note_type,
        body=body,
        created_by=actor,
        updated_by=actor,
    )
    session.add(note)
    await session.flush()
    return note


async def update_draft(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    note_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    body: str,
    note_type: NoteType | None,
) -> ClinicalNote:
    note = await _note_for_author(session, patient_id, note_id, doctor_id)
    if note.status != NoteStatus.DRAFT or note.author_doctor_id != doctor_id:
        raise ConflictError("Only your own draft notes can be edited. Amend a signed note instead.")
    note.body = body
    if note_type is not None:
        note.note_type = note_type
    note.updated_by = actor
    await session.flush()
    return note


async def sign_note(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    note_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
) -> ClinicalNote:
    note = await _note_for_author(session, patient_id, note_id, doctor_id)
    if note.status != NoteStatus.DRAFT or note.author_doctor_id != doctor_id:
        raise ConflictError("Only your own draft notes can be signed.")
    if not note.body.strip():
        raise ValidationFailedError("A note cannot be signed empty.")
    now = datetime.now(UTC)
    if note.supersedes_note_id is not None:
        # The amendment replaces the earlier signed note (trigger allows signed→superseded).
        await session.execute(
            update(ClinicalNote)
            .where(
                ClinicalNote.id == note.supersedes_note_id,
                ClinicalNote.status == NoteStatus.SIGNED,
            )
            .values(status=NoteStatus.SUPERSEDED, updated_by=actor)
            .execution_options(synchronize_session=False)
        )
    note.status = NoteStatus.SIGNED
    note.signed_at = now
    note.signed_by = actor
    note.updated_by = actor
    await session.flush()
    return note


async def start_amendment(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    note_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    body: str,
    reason: str,
) -> ClinicalNote:
    original = await _note_for_author(session, patient_id, note_id, doctor_id)
    if original.status != NoteStatus.SIGNED:
        raise ConflictError("Only a signed note can be amended.")
    if original.author_doctor_id != doctor_id:
        raise ForbiddenError("Only the author can amend a note; add a new note instead.")
    pending = await session.scalar(
        select(ClinicalNote.id).where(ClinicalNote.supersedes_note_id == original.id)
    )
    if pending is not None:
        raise ConflictError("This note already has an amendment in progress.")
    amendment = ClinicalNote(
        patient_id=patient_id,
        visit_id=original.visit_id,
        author_doctor_id=doctor_id,
        note_type=original.note_type,
        body=body,
        supersedes_note_id=original.id,
        amendment_reason=reason,
        created_by=actor,
        updated_by=actor,
    )
    session.add(amendment)
    await session.flush()
    return amendment


# --- problem list, allergies, history ----------------------------------------------------


async def list_conditions(session: AsyncSession, patient_id: uuid.UUID) -> list[MedicalCondition]:
    rows = await session.scalars(
        select(MedicalCondition)
        .where(MedicalCondition.patient_id == patient_id, MedicalCondition.deleted_at.is_(None))
        .order_by(MedicalCondition.created_at.desc())
    )
    return list(rows.all())


async def record_condition(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    icd10_code: str | None,
    clinical_status: ConditionClinicalStatus,
    verification_status: ConditionVerificationStatus,
    severity: Severity | None,
    onset_date: date | None,
    visit_id: uuid.UUID | None,
    notes: str | None,
) -> MedicalCondition:
    if verification_status == ConditionVerificationStatus.ENTERED_IN_ERROR:
        raise ValidationFailedError("Use the correction workflow to mark an entry in error.")
    if visit_id is not None:
        await get_visit(session, patient_id, visit_id)
    condition = MedicalCondition(
        patient_id=patient_id,
        name=name.strip(),
        icd10_code=icd10_code.upper().strip() if icd10_code else None,
        clinical_status=clinical_status,
        verification_status=verification_status,
        severity=severity,
        onset_date=onset_date,
        onset_precision=DatePrecision.DAY if onset_date else None,
        source=RecordSource.DOCTOR,
        visit_id=visit_id,
        notes=notes,
        created_by=actor,
        updated_by=actor,
    )
    session.add(condition)
    await session.flush()
    return condition


async def list_allergies(session: AsyncSession, patient_id: uuid.UUID) -> list[Allergy]:
    rows = await session.scalars(
        select(Allergy)
        .where(Allergy.patient_id == patient_id, Allergy.deleted_at.is_(None))
        .order_by(Allergy.created_at.desc())
    )
    return list(rows.all())


async def list_history(session: AsyncSession, patient_id: uuid.UUID) -> list[MedicalHistoryEntry]:
    rows = await session.scalars(
        select(MedicalHistoryEntry)
        .where(
            MedicalHistoryEntry.patient_id == patient_id,
            MedicalHistoryEntry.deleted_at.is_(None),
        )
        .order_by(MedicalHistoryEntry.occurred_on.desc().nulls_last())
    )
    return list(rows.all())


async def recent_visits_by_doctor(
    session: AsyncSession, doctor_id: uuid.UUID, patient_ids: list[uuid.UUID], limit: int = 8
) -> list[tuple[uuid.UUID, datetime]]:
    """(patient_id, last visit time) for this doctor's most recently seen patients."""
    if not patient_ids:
        return []
    last = func.max(DoctorVisit.started_at)
    rows = await session.execute(
        select(DoctorVisit.patient_id, last)
        .where(DoctorVisit.doctor_id == doctor_id, DoctorVisit.patient_id.in_(patient_ids))
        .group_by(DoctorVisit.patient_id)
        .order_by(last.desc().nulls_last())
        .limit(limit)
    )
    return [(pid, when) for pid, when in rows]


# --- patient-reported entries --------------------------------------------------------------


async def report_allergy(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    substance: str,
    category: AllergenCategory,
    reaction: str | None,
    severity: ReactionSeverity | None,
    source: RecordSource = RecordSource.PATIENT,
) -> Allergy:
    """Recorded as patient- or caregiver-reported, unconfirmed until a doctor confirms it."""
    allergy = Allergy(
        patient_id=patient_id,
        substance=substance.strip(),
        category=category,
        reaction=reaction,
        severity=severity,
        verification_status=ConditionVerificationStatus.UNCONFIRMED,
        source=source,
        created_by=actor,
        updated_by=actor,
    )
    session.add(allergy)
    await session.flush()
    return allergy


async def report_condition(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    onset_date: date | None,
    notes: str | None,
    source: RecordSource = RecordSource.PATIENT,
) -> MedicalCondition:
    condition = MedicalCondition(
        patient_id=patient_id,
        name=name.strip(),
        clinical_status=ConditionClinicalStatus.ACTIVE,
        verification_status=ConditionVerificationStatus.UNCONFIRMED,
        onset_date=onset_date,
        onset_precision=DatePrecision.DAY if onset_date else None,
        source=source,
        notes=notes,
        created_by=actor,
        updated_by=actor,
    )
    session.add(condition)
    await session.flush()
    return condition


async def remove_self_reported(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    kind: str,
    entry_id: uuid.UUID,
    actor: uuid.UUID,
) -> None:
    """Patients (and caregivers allowed to report) may remove patient- or caregiver-
    reported entries, never a doctor's entry.
    The row is kept (soft delete) so the history stays auditable."""
    row: Allergy | MedicalCondition | None
    if kind == "allergies":
        row = await session.scalar(
            select(Allergy)
            .where(
                Allergy.id == entry_id,
                Allergy.patient_id == patient_id,
                Allergy.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        row = await session.scalar(
            select(MedicalCondition)
            .where(
                MedicalCondition.id == entry_id,
                MedicalCondition.patient_id == patient_id,
                MedicalCondition.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if row is None:
        raise NotFoundError()
    if row.source not in (RecordSource.PATIENT, RecordSource.CAREGIVER):
        raise ForbiddenError("Entries made by a doctor can only be changed by that doctor.")
    row.deleted_at = datetime.now(UTC)
    row.deleted_by = actor
    row.updated_by = actor
    await session.flush()


# --- symptoms ------------------------------------------------------------------------

REPORTER_SIDE = (RecordSource.PATIENT, RecordSource.CAREGIVER)
SYMPTOM_FIELDS = (
    "symptom",
    "body_site",
    "severity",
    "status",
    "onset_date",
    "resolved_on",
    "notes",
)


async def list_symptoms(session: AsyncSession, patient_id: uuid.UUID) -> list[SymptomReport]:
    rows = await session.scalars(
        select(SymptomReport)
        .where(SymptomReport.patient_id == patient_id)
        .order_by(SymptomReport.reported_at.desc())
    )
    return list(rows.all())


def _check_symptom_dates(onset: date | None, resolved: date | None) -> None:
    today = datetime.now(UTC).date()
    if (onset and onset > today) or (resolved and resolved > today):
        raise ValidationFailedError("Dates cannot be in the future.")
    if onset and resolved and resolved < onset:
        raise ValidationFailedError("A symptom cannot end before it started.")


async def report_symptom(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    source: RecordSource,
    symptom: str,
    body_site: str | None,
    severity: SymptomSeverity | None,
    onset_date: date | None,
    resolved_on: date | None,
    notes: str | None,
    visit_id: uuid.UUID | None = None,
) -> SymptomReport:
    """Stored exactly as described, labelled with who reported it."""
    _check_symptom_dates(onset_date, resolved_on)
    if visit_id is not None:
        await get_visit(session, patient_id, visit_id)
    row = SymptomReport(
        patient_id=patient_id,
        symptom=symptom.strip(),
        body_site=body_site,
        severity=severity,
        status=SymptomStatus.RESOLVED if resolved_on else SymptomStatus.ONGOING,
        onset_date=onset_date,
        resolved_on=resolved_on,
        notes=notes,
        source=source,
        visit_id=visit_id,
        created_by=actor,
        updated_by=actor,
    )
    session.add(row)
    await session.flush()
    return row


async def correct_symptom(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    symptom_id: uuid.UUID,
    actor: uuid.UUID,
    actor_is_doctor: bool,
    expected_version: int,
    reason: str,
    changes: dict[str, object],
) -> SymptomReport:
    """A correction is never silent: it needs a reason, and the database keeps the
    earlier version. Patients and caregivers correct what they reported; doctors correct
    what doctors recorded. Nobody changes the other side's words."""
    row: SymptomReport | None = await session.scalar(
        select(SymptomReport)
        .where(SymptomReport.id == symptom_id, SymptomReport.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError()
    if actor_is_doctor != (row.source == RecordSource.DOCTOR):
        raise ForbiddenError(
            "Only the side that reported this symptom can correct it. Add a new entry instead."
        )
    if row.status == SymptomStatus.ENTERED_IN_ERROR:
        raise ConflictError("This entry was marked as entered in error and can't be changed.")
    if row.version != expected_version:
        raise ConflictError("This entry changed since you opened it. Reload and try again.")
    unknown = set(changes) - set(SYMPTOM_FIELDS)
    if unknown:
        raise ValidationFailedError(f"Unknown fields: {', '.join(sorted(unknown))}")
    if "symptom" in changes and not str(changes["symptom"] or "").strip():
        raise ValidationFailedError("Describe the symptom.")
    onset = changes.get("onset_date", row.onset_date)
    resolved = changes.get("resolved_on", row.resolved_on)
    _check_symptom_dates(onset, resolved)  # type: ignore[arg-type]
    status = changes.get("status", row.status)
    if status == SymptomStatus.RESOLVED and resolved is None:
        raise ValidationFailedError("Add the date the symptom stopped.")
    if resolved is not None and status == SymptomStatus.ONGOING and "status" not in changes:
        changes["status"] = SymptomStatus.RESOLVED
    async with change_reason(session, reason):
        for key, value in changes.items():
            setattr(row, key, value.strip() if isinstance(value, str) else value)
        row.updated_by = actor
    return row
