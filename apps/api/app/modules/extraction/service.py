"""Prescription scans service: pipeline, review and conversion.

    photo (health_documents, original kept) ─► preprocessing ─► OCR ─► vision model
      ─► schema validation ─► confidence assessment ─► needs_review
      ─► person reviews every field that needs it ─► confirm
      ─► Prescription(source=uploaded, status=recorded, patient/doctor-verified)
      ─► its medicines wait for the patient to confirm reminder times

Safety rules applied here (AI_SAFETY.md §3-4):
- Nothing becomes clinical data until a named person confirms it.
- Low-confidence readings are not pre-filled; critical fields always need a decision.
- A missing dose/strength/frequency is saved as missing ("not on the prescription"),
  never filled in. There is no diagnosis field.
- The AI reading is never edited; the review keeps each field's final value, status and
  history next to it. Interpretations (1-0-1 → twice a day) are dictionary-based and
  shown to the reviewer; unrecognised text is saved exactly as written.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ocr import OcrEngine
from app.ai.preprocess import UnsupportedImageError, prepare
from app.ai.providers import ExtractionFailedError, VisionExtractor
from app.ai.schemas import CRITICAL_FIELDS, HEADER_FIELDS, ITEM_FIELDS
from app.core.config import Settings
from app.core.enums import VerificationStatus
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.storage import Storage
from app.modules.extraction import normalize
from app.modules.extraction.assessment import assess
from app.modules.extraction.models import (
    OPEN_STATUSES,
    PrescriptionScan,
    PrescriptionScanMode,
    PrescriptionScanStatus,
    VerifierRole,
)
from app.modules.prescriptions.models import (
    Prescription,
    PrescriptionItem,
    PrescriptionSource,
    PrescriptionStatus,
)
from app.modules.records import service as records
from app.modules.records.models import DocumentType, HealthDocument

PIPELINE_VERSION = "rx-scan-1"
AI_NOTICE_VERSION = "2026-09-ai-en"
MAX_ITEMS = 30
DONE = ("confirmed", "corrected", "not_on_prescription")


# --- review state helpers ---------------------------------------------------------------------


def _empty_field() -> dict[str, Any]:
    return {"status": "unverified", "value": None, "by": None, "at": None, "history": []}


def _initial_field(assessed: dict[str, Any] | None) -> dict[str, Any]:
    field = _empty_field()
    # Only readings the reviewer can see with some confidence are pre-filled.
    if assessed is not None and assessed["band"] in ("high", "medium"):
        field["value"] = assessed["value"]
    return field


def initial_review(assessment: dict[str, Any] | None) -> dict[str, Any]:
    items = (assessment or {}).get("items", [])
    header = (assessment or {}).get("header", {})
    return {
        "items": [
            {
                "key": f"i{n + 1}",
                "source_index": n,
                "removed": False,
                "fields": {f: _initial_field(item[f]) for f in ITEM_FIELDS},
            }
            for n, item in enumerate(items)
        ],
        "header": {f: _initial_field(header.get(f)) for f in HEADER_FIELDS},
        "next_key": len(items) + 1,
    }


def load(scan: PrescriptionScan) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(review, assessment) — assessment is None for manual entry."""
    result = json.loads(scan.result) if scan.result else {}
    review = json.loads(scan.review) if scan.review else initial_review(None)
    return review, result.get("assessment")


def assessed_field(
    assessment: dict[str, Any] | None, item: dict[str, Any] | None, field: str
) -> dict[str, Any] | None:
    if assessment is None:
        return None
    if item is None:
        return dict(assessment["header"][field])
    idx = item.get("source_index")
    if idx is None:
        return None
    return dict(assessment["items"][idx][field])


# --- creating and running scans ---------------------------------------------------------------


async def create(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: uuid.UUID,
    mode: PrescriptionScanMode,
) -> PrescriptionScan:
    doc = await records.usable_document(session, patient_id, document_id)
    if doc.document_type != DocumentType.PRESCRIPTION:
        raise ValidationFailedError("Only documents uploaded as prescriptions can be read.")
    open_scan = await session.scalar(
        select(PrescriptionScan.id).where(
            PrescriptionScan.document_id == doc.id, PrescriptionScan.status.in_(OPEN_STATUSES)
        )
    )
    if open_scan is not None:
        raise ConflictError("This prescription is already being read or reviewed.")
    scan = PrescriptionScan(
        patient_id=patient_id,
        document_id=doc.id,
        mode=mode,
        status=PrescriptionScanStatus.QUEUED,
        requested_by=actor,
        pipeline_version=PIPELINE_VERSION,
        created_by=actor,
        updated_by=actor,
    )
    if mode == PrescriptionScanMode.MANUAL:
        scan.status = PrescriptionScanStatus.NEEDS_REVIEW
        scan.result = json.dumps({"mode": "manual"})
        scan.review = json.dumps(initial_review(None))
    session.add(scan)
    await session.flush()
    return scan


async def get(session: AsyncSession, patient_id: uuid.UUID, scan_id: uuid.UUID) -> PrescriptionScan:
    scan: PrescriptionScan | None = await session.scalar(
        select(PrescriptionScan).where(
            PrescriptionScan.id == scan_id, PrescriptionScan.patient_id == patient_id
        )
    )
    if scan is None:
        raise NotFoundError()
    return scan


async def _for_update(
    session: AsyncSession, patient_id: uuid.UUID, scan_id: uuid.UUID
) -> PrescriptionScan:
    scan: PrescriptionScan | None = await session.scalar(
        select(PrescriptionScan)
        .where(PrescriptionScan.id == scan_id, PrescriptionScan.patient_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if scan is None:
        raise NotFoundError()
    return scan


async def list_for_patient(session: AsyncSession, patient_id: uuid.UUID) -> list[PrescriptionScan]:
    rows = await session.scalars(
        select(PrescriptionScan)
        .where(PrescriptionScan.patient_id == patient_id)
        .order_by(PrescriptionScan.created_at.desc())
    )
    return list(rows.all())


async def mark_running(session: AsyncSession, scan: PrescriptionScan) -> bool:
    if scan.status != PrescriptionScanStatus.QUEUED:
        return False
    scan.status = PrescriptionScanStatus.RUNNING
    scan.started_at = datetime.now(UTC)
    await session.flush()
    return True


def _fail(scan: PrescriptionScan, code: str, message: str) -> None:
    scan.status = PrescriptionScanStatus.FAILED
    scan.error_code = code
    scan.error_message = message[:300]
    scan.finished_at = datetime.now(UTC)


async def process(
    session: AsyncSession,
    scan: PrescriptionScan,
    *,
    storage: Storage,
    settings: Settings,
    extractor: VisionExtractor | None,
    ocr_engine: OcrEngine,
) -> PrescriptionScan:
    """Run the pipeline for a RUNNING scan. Always ends in NEEDS_REVIEW or FAILED."""
    if scan.status != PrescriptionScanStatus.RUNNING:
        return scan
    if extractor is None:
        _fail(scan, "ai_not_configured", "AI reading is not available. You can type it in instead.")
        await session.flush()
        return scan
    doc: HealthDocument = await records.usable_document(session, scan.patient_id, scan.document_id)
    data = await storage.read(doc.storage_key, settings.upload_max_bytes)
    if data is None:
        _fail(scan, "image_unavailable", "The uploaded photo could not be read from storage.")
        await session.flush()
        return scan
    scan.image_sha256 = hashlib.sha256(data).hexdigest()
    try:
        prepared = prepare(data, doc.content_type)
    except UnsupportedImageError as exc:
        _fail(scan, exc.code, exc.message)
        await session.flush()
        return scan
    scan.preprocessing = prepared.steps
    ocr = await ocr_engine.read(prepared.ocr_image)
    scan.ocr_engine = ocr.engine
    try:
        reading = await extractor.extract(prepared.model_bytes, prepared.media_type, ocr)
    except ExtractionFailedError as exc:
        scan.provider = extractor.provider
        _fail(scan, exc.code, exc.message)
        await session.flush()
        return scan

    extraction = reading.extraction
    if len(extraction.items) > MAX_ITEMS:
        _fail(scan, "too_many_items", "Too many lines were read to review safely.")
        await session.flush()
        return scan
    assessment = assess(extraction, ocr)
    scan.provider = reading.provider
    scan.model = reading.model
    scan.prompt_version = reading.prompt_version
    scan.latency_ms = reading.latency_ms
    scan.input_tokens = reading.input_tokens
    scan.output_tokens = reading.output_tokens
    scan.attempts = reading.attempts
    scan.provider_response_id = reading.response_id
    scan.ocr_text = ocr.text or None
    scan.result = json.dumps(
        {
            "mode": "ai",
            "reading": extraction.model_dump(),
            "assessment": assessment,
            # Names (never values) of anything returned outside the schema, e.g. an
            # invented "diagnosis" key. Discarded, and recorded that it was discarded.
            "discarded_keys": reading.dropped_keys,
        }
    )
    scan.review = json.dumps(initial_review(assessment))
    scan.status = PrescriptionScanStatus.NEEDS_REVIEW
    scan.finished_at = datetime.now(UTC)
    await session.flush()
    return scan


# --- review -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    op: str  # set_field | add_item | remove_item | restore_item
    item_key: str | None = None
    field: str | None = None
    action: str | None = None  # set | confirm | not_on_prescription | reset
    value: str | None = None


def _find_item(review: dict[str, Any], key: str | None) -> dict[str, Any]:
    for item in review["items"]:
        if item["key"] == key:
            return dict(item)
    raise ValidationFailedError("That medicine line does not exist.")


def _apply_field(
    field: dict[str, Any],
    ai: dict[str, Any] | None,
    action: str,
    value: str | None,
    actor: uuid.UUID,
    now: str,
) -> None:
    previous = field["value"]
    if action == "reset":
        field.update(status="unverified", value=_initial_field(ai)["value"])
    elif action == "not_on_prescription":
        field.update(status="not_on_prescription", value=None)
    else:
        chosen = (value if action == "set" else field["value"]) or ""
        chosen = chosen.strip()
        if not chosen:
            raise ValidationFailedError(
                "There is no value to confirm. Type what the prescription says, or mark it as "
                "not on the prescription."
            )
        if len(chosen) > 300:
            raise ValidationFailedError("That value is too long.")
        same_as_ai = ai is not None and ai.get("value") is not None and chosen == ai["value"]
        field.update(status="confirmed" if same_as_ai else "corrected", value=chosen)
    field["by"] = str(actor)
    field["at"] = now
    field["history"].append(
        {
            "action": action,
            "status": field["status"],
            "by": str(actor),
            "at": now,
            "previous": previous,
        }
    )


async def apply_review(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    scan_id: uuid.UUID,
    ops: list[Op],
    actor: uuid.UUID,
) -> tuple[PrescriptionScan, list[str]]:
    """Apply review operations; returns the scan and the changed paths (for the audit)."""
    scan = await _for_update(session, patient_id, scan_id)
    if scan.status != PrescriptionScanStatus.NEEDS_REVIEW:
        raise ConflictError("This scan is not waiting for review.")
    review, assessment = load(scan)
    now = datetime.now(UTC).isoformat()
    changed: list[str] = []
    for op in ops:
        if op.op == "add_item":
            if sum(1 for i in review["items"] if not i["removed"]) >= MAX_ITEMS:
                raise ValidationFailedError(
                    f"A prescription can have at most {MAX_ITEMS} medicines."
                )
            key = f"i{review['next_key']}"
            review["next_key"] += 1
            review["items"].append(
                {
                    "key": key,
                    "source_index": None,  # typed in by a person, not read by AI
                    "removed": False,
                    "fields": {f: _empty_field() for f in ITEM_FIELDS},
                }
            )
            changed.append(f"items.{key}:added")
        elif op.op in ("remove_item", "restore_item"):
            item = next((i for i in review["items"] if i["key"] == op.item_key), None)
            if item is None:
                raise ValidationFailedError("That medicine line does not exist.")
            item["removed"] = op.op == "remove_item"
            changed.append(f"items.{op.item_key}:{'removed' if item['removed'] else 'restored'}")
        elif op.op == "set_field":
            if op.action not in ("set", "confirm", "not_on_prescription", "reset"):
                raise ValidationFailedError("Unknown review action.")
            if op.item_key is None:
                if op.field not in HEADER_FIELDS:
                    raise ValidationFailedError("Unknown field.")
                target = review["header"][op.field]
                ai = assessed_field(assessment, None, op.field)
                path = f"header.{op.field}"
            else:
                if op.field not in ITEM_FIELDS:
                    raise ValidationFailedError("Unknown field.")
                item = next((i for i in review["items"] if i["key"] == op.item_key), None)
                if item is None:
                    raise ValidationFailedError("That medicine line does not exist.")
                target = item["fields"][op.field]
                ai = assessed_field(assessment, item, op.field)
                path = f"items.{op.item_key}.{op.field}"
            _apply_field(target, ai, op.action, op.value, actor, now)
            changed.append(f"{path}:{target['status']}")
        else:
            raise ValidationFailedError("Unknown review operation.")
    scan.review = json.dumps(review)
    scan.updated_by = actor
    await session.flush()
    return scan, changed


def _auto_acceptable(ai: dict[str, Any] | None, field: dict[str, Any]) -> bool:
    """A non-critical field left untouched may be accepted as shown only if the AI read it
    with high confidence (or confidently found it absent), or a person typed the line."""
    if ai is None:
        return field["value"] is None or field["status"] != "unverified"
    if ai["band"] == "high":
        return True
    return bool(ai["band"] == "absent" and ai["model_confidence"] >= 0.9)


def blocking_issues(
    review: dict[str, Any], assessment: dict[str, Any] | None
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    live = [i for i in review["items"] if not i["removed"]]
    if not live:
        issues.append({"path": "items", "problem": "Add at least one medicine."})
    for item in live:
        for f in ITEM_FIELDS:
            field = item["fields"][f]
            ai = assessed_field(assessment, item, f)
            path = f"items.{item['key']}.{f}"
            if f == "medicine_name":
                if field["status"] not in ("confirmed", "corrected") or not field["value"]:
                    issues.append({"path": path, "problem": "Confirm or type the medicine name."})
            elif f in CRITICAL_FIELDS:
                if field["status"] not in DONE:
                    issues.append(
                        {
                            "path": path,
                            "problem": "Confirm this, correct it, or mark it as not on "
                            "the prescription.",
                        }
                    )
            elif field["status"] == "unverified" and not _auto_acceptable(ai, field):
                issues.append({"path": path, "problem": "Check this field against the photo."})
    for f in HEADER_FIELDS:
        field = review["header"][f]
        if field["status"] == "unverified" and not _auto_acceptable(
            assessed_field(assessment, None, f), field
        ):
            issues.append({"path": f"header.{f}", "problem": "Check this field against the photo."})
    return issues


# --- conversion -------------------------------------------------------------------------------


def _final(field: dict[str, Any]) -> str | None:
    if field["status"] == "not_on_prescription":
        return None
    value = field["value"]
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class ItemPlan:
    """How one reviewed line is stored. Text that cannot be interpreted is kept verbatim."""

    drug_name: str
    strength: str | None
    dose_amount: Any
    dose_unit: str | None
    frequency_text: str | None
    times_per_day: int | None
    is_prn: bool
    prn_reason: str | None
    meal_relation: Any
    duration_days: int | None
    instructions: str | None


def plan_item(fields: dict[str, dict[str, Any]]) -> ItemPlan:
    name = _final(fields["medicine_name"]) or ""
    strength = _final(fields["strength"])
    dose_text = _final(fields["dose"])
    freq_text = _final(fields["frequency"])
    duration_text = _final(fields["duration"])
    meal_text = _final(fields["meal_relation"])
    extra: list[str] = []

    d = normalize.dose(dose_text)
    if dose_text and d is None:
        extra.append(f"Dose as written: {dose_text}")
    f = normalize.frequency(freq_text)
    du = normalize.duration(duration_text)
    if duration_text and du is None:
        extra.append(f"Duration as written: {duration_text}")
    m = normalize.meal(meal_text)
    if meal_text and m is None:
        extra.append(f"Food: {meal_text}")
    instructions = _final(fields["instructions"])
    text = "; ".join([p for p in [instructions, *extra] if p]) or None
    is_prn = bool(f and f.is_prn)
    return ItemPlan(
        drug_name=name,
        strength=strength,
        dose_amount=d.amount if d else None,
        dose_unit=d.unit if d else None,
        frequency_text=freq_text,
        times_per_day=f.times_per_day if f else None,
        is_prn=is_prn,
        prn_reason=f"as written: {freq_text}" if is_prn else None,
        meal_relation=m.relation if m else None,
        duration_days=du.days if du else None,
        instructions=text[:1000] if text else None,
    )


@dataclass(frozen=True)
class Confirmed:
    scan: PrescriptionScan
    prescription: Prescription
    items: list[PrescriptionItem]
    corrected_fields: int


async def confirm(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    scan_id: uuid.UUID,
    actor: uuid.UUID,
    role: VerifierRole,
    today: date,
) -> Confirmed:
    scan = await _for_update(session, patient_id, scan_id)
    if scan.status != PrescriptionScanStatus.NEEDS_REVIEW:
        raise ConflictError("This scan is not waiting for review.")
    review, assessment = load(scan)
    issues = blocking_issues(review, assessment)
    if issues:
        # The review view lists each one (GET returns `issues`).
        raise ValidationFailedError(f"{len(issues)} field(s) still need your check before saving.")
    now = datetime.now(UTC)
    header = review["header"]
    level = (
        VerificationStatus.DOCTOR_VERIFIED
        if role == VerifierRole.DOCTOR
        else VerificationStatus.PATIENT_VERIFIED
    )
    rx = Prescription(
        patient_id=patient_id,
        source=PrescriptionSource.UPLOADED,
        status=PrescriptionStatus.DRAFT,
        document_id=scan.document_id,
        external_prescriber_name=(_final(header["doctor_name"]) or None),
        external_prescriber_registration=(_final(header["doctor_registration"]) or "")[:64] or None,
        external_facility_name=_final(header["clinic_name"]),
        prescribed_on=normalize.prescription_date(_final(header["prescription_date"]), today),
        created_by=actor,
        updated_by=actor,
    )
    session.add(rx)
    await session.flush()
    items: list[PrescriptionItem] = []
    corrected = 0
    live = [i for i in review["items"] if not i["removed"]]
    for seq, item in enumerate(live, start=1):
        corrected += sum(1 for f in item["fields"].values() if f["status"] == "corrected")
        plan = plan_item(item["fields"])
        row = PrescriptionItem(
            patient_id=patient_id,
            prescription_id=rx.id,
            sequence=seq,
            drug_name=plan.drug_name[:200],
            strength=(plan.strength or "")[:64] or None,
            dose_amount=plan.dose_amount,
            dose_unit=plan.dose_unit,
            frequency_text=(plan.frequency_text or "")[:64] or None,
            times_per_day=plan.times_per_day,
            meal_relation=plan.meal_relation,
            duration_days=plan.duration_days,
            is_prn=plan.is_prn,
            prn_reason=plan.prn_reason[:200] if plan.prn_reason else None,
            instructions=plan.instructions,
            created_by=actor,
            updated_by=actor,
        )
        session.add(row)
        items.append(row)
    await session.flush()
    rx.status = PrescriptionStatus.RECORDED
    rx.verification_status = level
    rx.verified_at = now
    rx.verified_by = actor
    scan.status = PrescriptionScanStatus.VERIFIED
    scan.verified_by = actor
    scan.verified_at = now
    scan.verifier_role = role
    scan.verification_level = level
    scan.prescription_id = rx.id
    scan.updated_by = actor
    await session.flush()
    return Confirmed(scan, rx, items, corrected)


async def reject(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    scan_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str,
) -> PrescriptionScan:
    scan = await _for_update(session, patient_id, scan_id)
    if scan.status != PrescriptionScanStatus.NEEDS_REVIEW:
        raise ConflictError("This scan is not waiting for review.")
    scan.status = PrescriptionScanStatus.REJECTED
    scan.rejected_reason = reason.strip()[:300]
    scan.updated_by = actor
    await session.flush()
    return scan
