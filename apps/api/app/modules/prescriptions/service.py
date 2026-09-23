"""Prescriptions service: doctor e-prescriptions.

Lifecycle: DRAFT (editable, items replaceable) → ISSUED (frozen by DB trigger) →
CANCELLED / SUPERSEDED / ENTERED_IN_ERROR. Only the prescribing doctor edits, issues or
cancels. Issuing hands the items to the medications module, which creates regimens in
PENDING_CONFIRMATION: nothing becomes active until the patient confirms the schedule.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MealRelation
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.modules.prescriptions.models import (
    Prescription,
    PrescriptionItem,
    PrescriptionSource,
    PrescriptionStatus,
)


@dataclass(frozen=True)
class ItemInput:
    drug_name: str
    generic_name: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    route: str | None = None
    dose_amount: Decimal | None = None
    dose_unit: str | None = None
    frequency_text: str | None = None
    times_per_day: int | None = None
    meal_relation: MealRelation | None = None
    duration_days: int | None = None
    quantity: Decimal | None = None
    is_prn: bool = False
    prn_reason: str | None = None
    instructions: str | None = None


@dataclass(frozen=True)
class PrescriptionWithItems:
    prescription: Prescription
    items: list[PrescriptionItem]


async def _items(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PrescriptionItem]]:
    if not ids:
        return {}
    rows = await session.scalars(
        select(PrescriptionItem)
        .where(PrescriptionItem.prescription_id.in_(ids))
        .order_by(PrescriptionItem.sequence)
    )
    grouped: dict[uuid.UUID, list[PrescriptionItem]] = {i: [] for i in ids}
    for item in rows.all():
        grouped[item.prescription_id].append(item)
    return grouped


async def list_for_patient(
    session: AsyncSession, patient_id: uuid.UUID, *, viewer_doctor_id: uuid.UUID | None
) -> list[PrescriptionWithItems]:
    """Drafts are visible only to the prescribing doctor."""
    stmt = select(Prescription).where(Prescription.patient_id == patient_id)
    rows = list((await session.scalars(stmt.order_by(Prescription.created_at.desc()))).all())
    rows = [
        p
        for p in rows
        if p.status != PrescriptionStatus.DRAFT or p.prescriber_doctor_id == viewer_doctor_id
    ]
    items = await _items(session, [p.id for p in rows])
    return [PrescriptionWithItems(p, items[p.id]) for p in rows]


async def _for_prescriber(
    session: AsyncSession, patient_id: uuid.UUID, prescription_id: uuid.UUID, doctor_id: uuid.UUID
) -> Prescription:
    rx: Prescription | None = await session.scalar(
        select(Prescription)
        .where(Prescription.id == prescription_id, Prescription.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rx is None or (
        rx.status == PrescriptionStatus.DRAFT and rx.prescriber_doctor_id != doctor_id
    ):
        raise NotFoundError()
    if rx.prescriber_doctor_id != doctor_id:
        raise ForbiddenError("Only the prescribing doctor can change this prescription.")
    return rx


def _validate_items(items: list[ItemInput]) -> None:
    if not items:
        raise ValidationFailedError("Add at least one medicine.")
    if len(items) > 30:
        raise ValidationFailedError("A prescription can have at most 30 medicines.")
    for item in items:
        if not item.drug_name.strip():
            raise ValidationFailedError("Every line needs a medicine name.")
        if item.is_prn and not (item.prn_reason or "").strip():
            raise ValidationFailedError("As-needed medicines need a reason (e.g. 'for fever').")


def _add_items(
    session: AsyncSession, rx: Prescription, items: list[ItemInput], actor: uuid.UUID
) -> None:
    for seq, item in enumerate(items, start=1):
        session.add(
            PrescriptionItem(
                patient_id=rx.patient_id,
                prescription_id=rx.id,
                sequence=seq,
                drug_name=item.drug_name.strip(),
                generic_name=item.generic_name,
                strength=item.strength,
                dosage_form=item.dosage_form,
                route=item.route,
                dose_amount=item.dose_amount,
                dose_unit=item.dose_unit,
                frequency_text=item.frequency_text,
                times_per_day=item.times_per_day,
                meal_relation=item.meal_relation,
                duration_days=item.duration_days,
                quantity=item.quantity,
                is_prn=item.is_prn,
                prn_reason=item.prn_reason if item.is_prn else None,
                instructions=item.instructions,
                created_by=actor,
                updated_by=actor,
            )
        )


async def create_draft(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    items: list[ItemInput],
    visit_id: uuid.UUID | None,
    prescribed_on: date | None,
    valid_until: date | None,
    diagnosis_as_written: str | None,
    advice: str | None,
) -> PrescriptionWithItems:
    _validate_items(items)
    rx = Prescription(
        patient_id=patient_id,
        source=PrescriptionSource.DOCTOR_ISSUED,
        status=PrescriptionStatus.DRAFT,
        prescriber_doctor_id=doctor_id,
        visit_id=visit_id,
        prescribed_on=prescribed_on or datetime.now(UTC).date(),
        valid_until=valid_until,
        diagnosis_as_written=diagnosis_as_written,
        advice=advice,
        created_by=actor,
        updated_by=actor,
    )
    session.add(rx)
    await session.flush()
    _add_items(session, rx, items, actor)
    await session.flush()
    return PrescriptionWithItems(rx, (await _items(session, [rx.id]))[rx.id])


async def replace_draft(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    items: list[ItemInput],
    valid_until: date | None,
    diagnosis_as_written: str | None,
    advice: str | None,
) -> PrescriptionWithItems:
    rx = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if rx.status != PrescriptionStatus.DRAFT:
        raise ConflictError(
            "An issued prescription cannot be edited. Cancel it and write a new one."
        )
    _validate_items(items)
    await session.execute(delete(PrescriptionItem).where(PrescriptionItem.prescription_id == rx.id))
    rx.valid_until = valid_until
    rx.diagnosis_as_written = diagnosis_as_written
    rx.advice = advice
    rx.updated_by = actor
    _add_items(session, rx, items, actor)
    await session.flush()
    return PrescriptionWithItems(rx, (await _items(session, [rx.id]))[rx.id])


async def issue(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
) -> PrescriptionWithItems:
    rx = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if rx.status != PrescriptionStatus.DRAFT:
        raise ConflictError("This prescription has already been issued.")
    items = (await _items(session, [rx.id]))[rx.id]
    if not items:
        raise ValidationFailedError("Add at least one medicine before issuing.")
    rx.status = PrescriptionStatus.ISSUED
    rx.issued_at = datetime.now(UTC)
    rx.updated_by = actor
    await session.flush()
    return PrescriptionWithItems(rx, items)


async def cancel(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str,
) -> Prescription:
    rx = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if rx.status == PrescriptionStatus.DRAFT:
        await session.delete(rx)  # an unissued draft is simply discarded
        await session.flush()
        return rx
    if rx.status != PrescriptionStatus.ISSUED:
        raise ConflictError("Only an issued prescription can be cancelled.")
    rx.status = PrescriptionStatus.CANCELLED
    rx.cancelled_at = datetime.now(UTC)
    rx.cancelled_by = actor
    rx.cancel_reason = reason
    rx.updated_by = actor
    await session.flush()
    return rx


async def active_item_ids_by_prescriber(
    session: AsyncSession, doctor_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(PrescriptionItem.id)
        .join(
            Prescription,
            (Prescription.id == PrescriptionItem.prescription_id)
            & (Prescription.patient_id == PrescriptionItem.patient_id),
        )
        .where(
            Prescription.prescriber_doctor_id == doctor_id,
            Prescription.status == PrescriptionStatus.ISSUED,
        )
    )
    return list(rows.all())
