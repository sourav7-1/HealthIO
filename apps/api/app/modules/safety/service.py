"""Medication safety service: gathers what the engine needs, stores what it finds, and
records reviews.

- `recheck()` runs after anything that changes the picture (a prescription issued, a
  medicine added or confirmed, an allergy or condition recorded). Open warnings for
  situations that still exist are refreshed; new ones are added; ones that no longer
  apply (a medicine stopped, an allergy entered in error) are marked resolved. Nothing is
  deleted.
- `preview()` checks a draft prescription against the patient's current medicines
  without storing anything, so the doctor sees potential issues before issuing.
- Reviews: a doctor marks a warning reviewed (with a note); the patient or a caregiver
  acknowledges having seen it. Neither changes any medicine.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.clinical.models import (
    Allergy,
    AllergyClinicalStatus,
    ConditionClinicalStatus,
    ConditionVerificationStatus,
    MedicalCondition,
)
from app.modules.medications.models import OPEN_MEDICATION_STATUSES, Medication
from app.modules.prescriptions.models import Prescription, PrescriptionItem
from app.modules.safety import engine
from app.modules.safety.models import ReviewStatus, SafetyWarning, WarningStatus
from app.modules.safety.reference import Facts, ingredient_key, load_facts, load_products, name_key

CLOSED_VERIFICATION = (
    ConditionVerificationStatus.REFUTED,
    ConditionVerificationStatus.ENTERED_IN_ERROR,
)
LIVE_CONDITION = (
    ConditionClinicalStatus.ACTIVE,
    ConditionClinicalStatus.RECURRENCE,
    ConditionClinicalStatus.RELAPSE,
)


def _from_item(ref: str, name: str, item: PrescriptionItem) -> engine.Med:
    """A prescription line as the engine sees it (with the details consistency checks use)."""
    return engine.Med(
        ref=ref,
        name=name,
        generic_name=item.generic_name,
        strength=item.strength,
        dosage_form=item.dosage_form,
        drug_code=item.drug_code,
        prescription_ref=f"rx:{item.prescription_id}",
        frequency_text=item.frequency_text,
        times_per_day=item.times_per_day,
        dose_amount=item.dose_amount,
        dose_unit=item.dose_unit,
        is_prn=item.is_prn,
        prn_reason=item.prn_reason,
        duration_days=item.duration_days,
        quantity=item.quantity,
    )


async def current_medicines(
    session: AsyncSession, patient_id: uuid.UUID, *, exclude_prescription: uuid.UUID | None = None
) -> list[engine.Med]:
    meds = (
        await session.scalars(
            select(Medication).where(
                Medication.patient_id == patient_id, Medication.status.in_(OPEN_MEDICATION_STATUSES)
            )
        )
    ).all()
    item_ids = {m.prescription_item_id for m in meds if m.prescription_item_id}
    items = (
        {
            i.id: i
            for i in (
                await session.scalars(
                    select(PrescriptionItem).where(PrescriptionItem.id.in_(item_ids))
                )
            ).all()
        }
        if item_ids
        else {}
    )
    out: list[engine.Med] = []
    for m in meds:
        item = items.get(m.prescription_item_id) if m.prescription_item_id else None
        if (
            item is not None
            and exclude_prescription is not None
            and item.prescription_id == exclude_prescription
        ):
            continue
        if item is not None:
            med = replace(
                _from_item(f"med:{m.id}", m.name, item),
                generic_name=m.generic_name or item.generic_name,
                drug_code=m.drug_code or item.drug_code,
                strength=m.strength or item.strength,
                start_date=m.start_date,
                end_date=m.end_date,
            )
        else:
            med = engine.Med(
                ref=f"med:{m.id}",
                name=m.name,
                generic_name=m.generic_name,
                strength=m.strength,
                dosage_form=m.dosage_form,
                drug_code=m.drug_code,
                is_prn=m.is_prn,
                start_date=m.start_date,
                end_date=m.end_date,
            )
        out.append(med)
    return out


async def _allergies_and_conditions(
    session: AsyncSession, patient_id: uuid.UUID
) -> tuple[list[engine.AllergyRec], list[engine.ConditionRec]]:
    allergies = [
        engine.AllergyRec(f"allergy:{a.id}", a.substance, a.substance_code)
        for a in (
            await session.scalars(
                select(Allergy).where(
                    Allergy.patient_id == patient_id,
                    Allergy.deleted_at.is_(None),
                    Allergy.clinical_status == AllergyClinicalStatus.ACTIVE,
                    Allergy.verification_status.not_in(CLOSED_VERIFICATION),
                )
            )
        ).all()
    ]
    conditions = [
        engine.ConditionRec(f"cond:{c.id}", c.name, c.icd10_code or "")
        for c in (
            await session.scalars(
                select(MedicalCondition).where(
                    MedicalCondition.patient_id == patient_id,
                    MedicalCondition.deleted_at.is_(None),
                    MedicalCondition.icd10_code.is_not(None),
                    MedicalCondition.clinical_status.in_(LIVE_CONDITION),
                    MedicalCondition.verification_status.not_in(CLOSED_VERIFICATION),
                )
            )
        ).all()
    ]
    return allergies, conditions


async def _facts(
    session: AsyncSession, meds: list[engine.Med], allergies: list[engine.AllergyRec]
) -> Facts:
    facts = await load_products(
        session, {name_key(m.name) for m in meds}, {m.drug_code for m in meds if m.drug_code}
    )
    ingredients = {i for m in meds for i in engine.resolve(m, facts).ingredients}
    return await load_facts(
        session, facts, ingredients | {ingredient_key(a.substance) for a in allergies}
    )


async def evaluate_for(
    session: AsyncSession, patient_id: uuid.UUID, meds: list[engine.Med]
) -> engine.Report:
    allergies, conditions = await _allergies_and_conditions(session, patient_id)
    facts = await _facts(session, meds, allergies)
    return engine.evaluate(meds, allergies, conditions, facts)


async def recheck(
    session: AsyncSession, patient_id: uuid.UUID, *, trigger: str, actor: uuid.UUID | None
) -> list[SafetyWarning]:
    """Bring stored warnings in line with the patient's current medicines."""
    now = datetime.now(UTC)
    report = await evaluate_for(session, patient_id, await current_medicines(session, patient_id))
    open_rows = {
        w.fingerprint: w
        for w in (
            await session.scalars(
                select(SafetyWarning)
                .where(
                    SafetyWarning.patient_id == patient_id,
                    SafetyWarning.status == WarningStatus.OPEN,
                )
                .with_for_update()
            )
        ).all()
    }
    found = {f.fingerprint: f for f in report.findings}
    for fp, row in open_rows.items():
        if fp in found:
            row.last_checked_at = now
        else:
            row.status = WarningStatus.RESOLVED
            row.resolved_at = now
            row.updated_by = actor
    for fp, f in found.items():
        if fp in open_rows:
            continue
        session.add(
            SafetyWarning(
                patient_id=patient_id,
                kind=f.kind,
                severity=f.severity,
                source_severity=f.source_severity,
                fingerprint=fp,
                title=f.title,
                patient_detail=f.patient_detail,
                clinician_detail=f.clinician_detail,
                source_type="reference_dataset" if f.source.dataset_id else "consistency_rule",
                source_name=f.source.name,
                source_version=f.source.version,
                source_ref=f.source.ref,
                dataset_id=f.source.dataset_id,
                subjects=list(f.subjects),
                needs=list(f.needs),
                trigger=trigger,
                detected_at=now,
                last_checked_at=now,
                created_by=actor,
                updated_by=actor,
            )
        )
    await session.flush()
    return await list_warnings(session, patient_id)


async def preview_prescription(
    session: AsyncSession, patient_id: uuid.UUID, prescription_id: uuid.UUID
) -> engine.Report:
    """A draft (or any) prescription's lines against the other current medicines."""
    rx = await session.scalar(
        select(Prescription).where(
            Prescription.id == prescription_id, Prescription.patient_id == patient_id
        )
    )
    if rx is None:
        raise NotFoundError()
    items = (
        await session.scalars(
            select(PrescriptionItem).where(PrescriptionItem.prescription_id == rx.id)
        )
    ).all()
    lines = [_from_item(f"rxi:{i.id}", i.drug_name, i) for i in items]
    current = await current_medicines(
        session,
        patient_id,
        exclude_prescription=rx.supersedes_prescription_id or rx.id,
    )
    current = [m for m in current if m.prescription_ref != f"rx:{rx.id}"]
    return await evaluate_for(session, patient_id, lines + current)


async def list_warnings(
    session: AsyncSession, patient_id: uuid.UUID, *, include_resolved: bool = False
) -> list[SafetyWarning]:
    stmt = select(SafetyWarning).where(SafetyWarning.patient_id == patient_id)
    if not include_resolved:
        stmt = stmt.where(SafetyWarning.status == WarningStatus.OPEN)
    return list((await session.scalars(stmt.order_by(SafetyWarning.detected_at.desc()))).all())


async def review(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    warning_id: uuid.UUID,
    actor: uuid.UUID,
    as_doctor: bool,
    note: str | None,
) -> SafetyWarning:
    """Doctor: reviewed (the note says what was decided, e.g. "aware, benefits outweigh").
    Patient or caregiver: acknowledged (seen; to be discussed with the doctor). A doctor's
    review is never downgraded by a later acknowledgement."""
    row = await session.scalar(
        select(SafetyWarning)
        .where(SafetyWarning.id == warning_id, SafetyWarning.patient_id == patient_id)
        .with_for_update()
    )
    if row is None:
        raise NotFoundError()
    if not as_doctor and row.review_status == ReviewStatus.REVIEWED:
        raise ConflictError("A doctor has already reviewed this warning.")
    row.review_status = ReviewStatus.REVIEWED if as_doctor else ReviewStatus.ACKNOWLEDGED
    row.reviewed_at = datetime.now(UTC)
    row.reviewed_by = actor
    row.reviewer_role = "doctor" if as_doctor else "patient_side"
    row.review_note = note.strip() if note and note.strip() else None
    row.updated_by = actor
    await session.flush()
    return row
