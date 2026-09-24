"""Prescriptions service: doctor e-prescriptions.

Lifecycle: DRAFT (editable, items replaceable) → ISSUED (frozen by DB trigger) →
CANCELLED / SUPERSEDED / ENTERED_IN_ERROR. Only the prescribing doctor edits, issues or
cancels. Issuing hands the items to the medications module, which creates regimens in
PENDING_CONFIRMATION: nothing becomes active until the patient confirms the schedule.

Corrections never modify an issued prescription. The doctor starts a *revision*: a new
draft (revision + 1, with a reason) that supersedes the old one. When it is issued, the
old row's status becomes SUPERSEDED (the only change its trigger allows) and every
version stays readable as history.
"""

import hashlib
import json
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


@dataclass(frozen=True)
class Details:
    """Prescription-level fields a doctor writes (everything except the items)."""

    valid_until: date | None = None
    diagnosis_as_written: str | None = None
    advice: str | None = None
    follow_up_on: date | None = None
    follow_up_instructions: str | None = None


def _validate_details(details: Details, prescribed_on: date | None) -> None:
    start = prescribed_on or datetime.now(UTC).date()
    if details.valid_until is not None and details.valid_until < start:
        raise ValidationFailedError("'Valid until' cannot be before the prescription date.")
    if details.follow_up_on is not None and details.follow_up_on < start:
        raise ValidationFailedError("The follow-up date cannot be before the prescription date.")
    if details.follow_up_instructions and details.follow_up_on is None:
        raise ValidationFailedError("Add a follow-up date for the follow-up instructions.")


def _apply_details(rx: Prescription, details: Details) -> None:
    rx.valid_until = details.valid_until
    rx.diagnosis_as_written = details.diagnosis_as_written
    rx.advice = details.advice
    rx.follow_up_on = details.follow_up_on
    rx.follow_up_instructions = details.follow_up_instructions


async def create_draft(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    items: list[ItemInput],
    visit_id: uuid.UUID | None,
    prescribed_on: date | None,
    details: Details,
) -> PrescriptionWithItems:
    _validate_items(items)
    _validate_details(details, prescribed_on)
    rx = Prescription(
        patient_id=patient_id,
        source=PrescriptionSource.DOCTOR_ISSUED,
        status=PrescriptionStatus.DRAFT,
        prescriber_doctor_id=doctor_id,
        visit_id=visit_id,
        prescribed_on=prescribed_on or datetime.now(UTC).date(),
        created_by=actor,
        updated_by=actor,
    )
    _apply_details(rx, details)
    session.add(rx)
    await session.flush()
    _add_items(session, rx, items, actor)
    await session.flush()
    return PrescriptionWithItems(rx, (await _items(session, [rx.id]))[rx.id])


async def start_revision(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str,
    items: list[ItemInput],
    details: Details,
) -> PrescriptionWithItems:
    """Start a correction of an issued prescription as a new draft version."""
    old = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if old.status != PrescriptionStatus.ISSUED:
        raise ConflictError("Only an issued prescription can be corrected.")
    successor = await session.scalar(
        select(Prescription.id).where(Prescription.supersedes_prescription_id == old.id)
    )
    if successor is not None:
        raise ConflictError(
            "A correction of this prescription already exists. Open the draft to continue."
        )
    if len(reason.strip()) < 5:
        raise ValidationFailedError("Say briefly what is being corrected.")
    _validate_items(items)
    today = datetime.now(UTC).date()
    _validate_details(details, today)
    rx = Prescription(
        patient_id=patient_id,
        source=PrescriptionSource.DOCTOR_ISSUED,
        status=PrescriptionStatus.DRAFT,
        prescriber_doctor_id=doctor_id,
        visit_id=old.visit_id,
        supersedes_prescription_id=old.id,
        revision=old.revision + 1,
        revision_reason=reason.strip(),
        prescribed_on=today,
        created_by=actor,
        updated_by=actor,
    )
    _apply_details(rx, details)
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
    details: Details,
    revision_reason: str | None = None,
) -> PrescriptionWithItems:
    rx = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if rx.status != PrescriptionStatus.DRAFT:
        raise ConflictError(
            "An issued prescription cannot be edited. Use 'Correct' to issue a new version."
        )
    _validate_items(items)
    _validate_details(details, rx.prescribed_on)
    await session.execute(delete(PrescriptionItem).where(PrescriptionItem.prescription_id == rx.id))
    _apply_details(rx, details)
    if rx.revision > 1 and revision_reason and revision_reason.strip():
        rx.revision_reason = revision_reason.strip()
    rx.updated_by = actor
    _add_items(session, rx, items, actor)
    await session.flush()
    return PrescriptionWithItems(rx, (await _items(session, [rx.id]))[rx.id])


def _canon(value: object) -> object:
    if isinstance(value, (uuid.UUID, date, datetime, Decimal)):
        return str(value)
    return value


def content_fingerprint(rx: Prescription, items: list[PrescriptionItem]) -> str:
    """SHA-256 over the clinical content in a canonical form (sorted keys, fixed item
    order). Anyone holding an export can recompute and compare it with the record."""
    head_fields = (
        "id", "patient_id", "prescriber_doctor_id", "prescribed_on", "valid_until",
        "diagnosis_as_written", "advice", "follow_up_on", "follow_up_instructions",
        "revision", "revision_reason", "supersedes_prescription_id",
    )  # fmt: skip
    item_fields = (
        "sequence", "drug_name", "generic_name", "strength", "dosage_form", "route",
        "dose_amount", "dose_unit", "frequency_text", "times_per_day", "meal_relation",
        "duration_days", "quantity", "is_prn", "prn_reason", "instructions",
    )  # fmt: skip
    payload = {
        "prescription": {f: _canon(getattr(rx, f)) for f in head_fields},
        "items": [{f: _canon(getattr(i, f)) for f in item_fields} for i in items],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssueResult:
    row: PrescriptionWithItems
    superseded: PrescriptionWithItems | None


async def issue(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID,
) -> IssueResult:
    rx = await _for_prescriber(session, patient_id, prescription_id, doctor_id)
    if rx.status != PrescriptionStatus.DRAFT:
        raise ConflictError("This prescription has already been issued.")
    items = (await _items(session, [rx.id]))[rx.id]
    if not items:
        raise ValidationFailedError("Add at least one medicine before issuing.")
    superseded: PrescriptionWithItems | None = None
    if rx.supersedes_prescription_id is not None:
        old = await _for_prescriber(session, patient_id, rx.supersedes_prescription_id, doctor_id)
        if old.status != PrescriptionStatus.ISSUED:
            raise ConflictError(
                "The prescription being corrected is no longer active, so this correction "
                "cannot be issued. Discard the draft and write a new prescription."
            )
        old.status = PrescriptionStatus.SUPERSEDED  # the only change its trigger allows
        old.updated_by = actor
        superseded = PrescriptionWithItems(old, (await _items(session, [old.id]))[old.id])
    rx.status = PrescriptionStatus.ISSUED
    rx.issued_at = datetime.now(UTC)
    rx.content_sha256 = content_fingerprint(rx, items)
    rx.updated_by = actor
    await session.flush()
    return IssueResult(PrescriptionWithItems(rx, items), superseded)


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
    successor = await session.scalar(
        select(Prescription.id).where(Prescription.supersedes_prescription_id == rx.id)
    )
    if successor is not None:
        raise ConflictError("Discard the correction draft of this prescription first.")
    rx.status = PrescriptionStatus.CANCELLED
    rx.cancelled_at = datetime.now(UTC)
    rx.cancelled_by = actor
    rx.cancel_reason = reason
    rx.updated_by = actor
    await session.flush()
    return rx


async def get_one(
    session: AsyncSession,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    *,
    viewer_doctor_id: uuid.UUID | None,
) -> PrescriptionWithItems:
    rx = await session.scalar(
        select(Prescription).where(
            Prescription.id == prescription_id, Prescription.patient_id == patient_id
        )
    )
    if rx is None or (
        rx.status == PrescriptionStatus.DRAFT and rx.prescriber_doctor_id != viewer_doctor_id
    ):
        raise NotFoundError()
    return PrescriptionWithItems(rx, (await _items(session, [rx.id]))[rx.id])


async def version_chain(
    session: AsyncSession,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    *,
    viewer_doctor_id: uuid.UUID | None,
) -> list[Prescription]:
    """Every version of this prescription, oldest first (others' drafts excluded)."""
    rows = list(
        (
            await session.scalars(
                select(Prescription).where(
                    Prescription.patient_id == patient_id,
                    Prescription.source == PrescriptionSource.DOCTOR_ISSUED,
                )
            )
        ).all()
    )
    by_id = {r.id: r for r in rows}
    successor = {r.supersedes_prescription_id: r for r in rows if r.supersedes_prescription_id}
    start = by_id.get(prescription_id)
    if start is None:
        return []
    while start.supersedes_prescription_id and start.supersedes_prescription_id in by_id:
        start = by_id[start.supersedes_prescription_id]
    chain = [start]
    while chain[-1].id in successor:
        chain.append(successor[chain[-1].id])
    return [
        r
        for r in chain
        if r.status != PrescriptionStatus.DRAFT or r.prescriber_doctor_id == viewer_doctor_id
    ]


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


async def items_by_ids(
    session: AsyncSession, patient_id: uuid.UUID, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PrescriptionItem]:
    """Prescription lines (as the doctor wrote them) for display next to a medicine."""
    if not item_ids:
        return {}
    rows = await session.scalars(
        select(PrescriptionItem).where(
            PrescriptionItem.patient_id == patient_id, PrescriptionItem.id.in_(item_ids)
        )
    )
    return {i.id: i for i in rows.all()}


async def by_ids(
    session: AsyncSession, patient_id: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, Prescription]:
    if not ids:
        return {}
    rows = await session.scalars(
        select(Prescription).where(Prescription.patient_id == patient_id, Prescription.id.in_(ids))
    )
    return {r.id: r for r in rows.all()}
