"""Medications service: the patient's actual regimens and adherence.

- Regimens from an issued prescription start PENDING_CONFIRMATION; the patient (or an
  authorised caregiver) confirms the schedule before anything becomes active.
- A doctor may record a medicine the patient already takes (CLINICIAN_RECORDED).
- Adherence is reported only from recorded dose events. With no data, the summary says
  so; it never estimates or infers.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.modules.medications.models import (
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    MedicationStatus,
    ScheduleStatus,
)

ACTIVE_STATUSES = (
    MedicationStatus.ACTIVE,
    MedicationStatus.PENDING_CONFIRMATION,
    MedicationStatus.PAUSED,
)


async def list_for_patient(session: AsyncSession, patient_id: uuid.UUID) -> list[Medication]:
    rows = await session.scalars(
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .order_by(Medication.created_at.desc())
    )
    return list(rows.all())


@dataclass(frozen=True)
class PrescribedLine:
    item_id: uuid.UUID
    drug_name: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    route: str | None
    is_prn: bool
    instructions: str | None
    duration_days: int | None


async def create_from_prescription(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    lines: list[PrescribedLine],
    start_date: date,
    actor: uuid.UUID,
) -> list[Medication]:
    meds = []
    for line in lines:
        med = Medication(
            patient_id=patient_id,
            source=MedicationSource.PRESCRIPTION,
            prescription_item_id=line.item_id,
            name=line.drug_name,
            generic_name=line.generic_name,
            strength=line.strength,
            dosage_form=line.dosage_form,
            route=line.route,
            is_prn=line.is_prn,
            instructions=line.instructions,
            status=MedicationStatus.PENDING_CONFIRMATION,
            start_date=start_date,
            end_date=(
                start_date + timedelta(days=line.duration_days - 1) if line.duration_days else None
            ),
            created_by=actor,
            updated_by=actor,
        )
        session.add(med)
        meds.append(med)
    await session.flush()
    return meds


async def record_existing(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    strength: str | None,
    dosage_form: str | None,
    route: str | None,
    instructions: str | None,
    start_date: date | None,
    is_prn: bool,
) -> Medication:
    """A medicine the patient already takes, as reported to and recorded by the doctor."""
    if not name.strip():
        raise ValidationFailedError("A medicine name is required.")
    now = datetime.now(UTC)
    med = Medication(
        patient_id=patient_id,
        source=MedicationSource.CLINICIAN_RECORDED,
        name=name.strip(),
        strength=strength,
        dosage_form=dosage_form,
        route=route,
        instructions=instructions,
        is_prn=is_prn,
        status=MedicationStatus.ACTIVE,
        start_date=start_date,
        confirmed_at=now,
        confirmed_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    session.add(med)
    await session.flush()
    return med


async def count_active_for_items(
    session: AsyncSession, item_ids: list[uuid.UUID], patient_ids: list[uuid.UUID]
) -> int:
    if not item_ids or not patient_ids:
        return 0
    count = await session.scalar(
        select(func.count(Medication.id)).where(
            Medication.prescription_item_id.in_(item_ids),
            Medication.patient_id.in_(patient_ids),
            Medication.status.in_(ACTIVE_STATUSES),
        )
    )
    return int(count or 0)


async def count_active_for_patient(session: AsyncSession, patient_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(Medication.id)).where(
            Medication.patient_id == patient_id, Medication.status.in_(ACTIVE_STATUSES)
        )
    )
    return int(count or 0)


@dataclass(frozen=True)
class AdherenceLine:
    medication_id: uuid.UUID
    name: str
    scheduled: int
    taken: int
    skipped: int
    missed: int

    @property
    def rate(self) -> float | None:
        decided = self.taken + self.skipped + self.missed
        return round(self.taken / decided, 4) if decided else None


@dataclass(frozen=True)
class AdherenceSummary:
    days: int
    has_schedules: bool
    lines: list[AdherenceLine]

    @property
    def total_recorded(self) -> int:
        return sum(line.taken + line.skipped + line.missed for line in self.lines)


async def adherence_summary(
    session: AsyncSession, patient_id: uuid.UUID, days: int = 30
) -> AdherenceSummary:
    """Counts from recorded dose events only (PRN doses excluded)."""
    since = datetime.now(UTC) - timedelta(days=days)
    has_schedules = bool(
        await session.scalar(
            select(func.count(MedicationSchedule.id)).where(
                MedicationSchedule.patient_id == patient_id,
                MedicationSchedule.status == ScheduleStatus.ACTIVE,
            )
        )
    )
    counted = func.count(MedicationDose.id)
    rows = await session.execute(
        select(MedicationDose.medication_id, Medication.name, MedicationDose.status, counted)
        .join(
            Medication,
            (Medication.id == MedicationDose.medication_id)
            & (Medication.patient_id == MedicationDose.patient_id),
        )
        .where(
            MedicationDose.patient_id == patient_id,
            MedicationDose.schedule_id.is_not(None),
            MedicationDose.scheduled_at >= since,
            # Past doses, plus upcoming ones already answered (e.g. taken a little early).
            (MedicationDose.scheduled_at <= datetime.now(UTC))
            | MedicationDose.status.in_([DoseStatus.TAKEN, DoseStatus.SKIPPED]),
            MedicationDose.status != DoseStatus.CANCELLED,
        )
        .group_by(MedicationDose.medication_id, Medication.name, MedicationDose.status)
    )
    per_med: dict[uuid.UUID, dict[str, int | str]] = {}
    for med_id, name, status, n in rows:
        entry = per_med.setdefault(med_id, {"name": name})
        entry[status.value] = int(n)
    lines = [
        AdherenceLine(
            medication_id=med_id,
            name=str(v["name"]),
            scheduled=sum(int(c) for k, c in v.items() if k != "name"),
            taken=int(v.get(DoseStatus.TAKEN.value, 0)),
            skipped=int(v.get(DoseStatus.SKIPPED.value, 0)),
            missed=int(v.get(DoseStatus.MISSED.value, 0)),
        )
        for med_id, v in per_med.items()
    ]
    return AdherenceSummary(days=days, has_schedules=has_schedules, lines=lines)
