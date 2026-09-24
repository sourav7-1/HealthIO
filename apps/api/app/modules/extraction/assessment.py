"""Confidence estimation: turn the model's self-reported confidence into a reviewable
confidence using independent checks (AI_SAFETY.md §4). Confidence is only ever *lowered*
here; each reduction is recorded as a flag, so nothing is silently changed.

Bands: high ≥ 0.90 · medium 0.60-0.90 (must confirm) · low < 0.60 ("Could not
confidently read this field": the value is not pre-filled for the reviewer).
"""

import re
from typing import Any

from app.ai.ocr import OcrResult, locate, tokens
from app.ai.schemas import (
    CRITICAL_FIELDS,
    HEADER_FIELDS,
    ITEM_FIELDS,
    ExtractedField,
    PrescriptionExtraction,
)

HIGH = 0.90
LOW = 0.60
LOW_CAP = 0.59
UNSUPPORTED_CAP = 0.30
HANDWRITTEN_CAP = 0.89
LOW_MESSAGE = "Could not confidently read this field."

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def band(confidence: float, value: str | None, legibility: str) -> str:
    if value is None:
        return "absent" if legibility == "not_present" else "low"
    if confidence >= HIGH:
        return "high"
    if confidence >= LOW:
        return "medium"
    return "low"


def assess_field(
    name: str, field: ExtractedField, *, ocr: OcrResult, handwritten: bool
) -> dict[str, Any]:
    confidence = field.confidence
    flags: list[str] = []
    region = field.region.model_dump() if field.region else None
    region_source = "model" if region else None
    value = field.value.strip() if field.value and field.value.strip() else None

    def cap(limit: float, flag: str) -> None:
        nonlocal confidence
        flags.append(flag)
        confidence = min(confidence, limit)

    if value is None:
        flags.append("not_present" if field.legibility == "not_present" else "illegible")
    else:
        if field.legibility == "illegible":
            cap(UNSUPPORTED_CAP, "marked_illegible")
        elif field.legibility == "partly_legible":
            cap(LOW_CAP, "partly_legible")
        evidence = (field.evidence or "").strip()
        if not evidence:
            cap(LOW_CAP, "no_evidence")
        else:
            # Numbers in the value must appear in the text it was read from: a dose or
            # strength the page does not show is treated as unsupported.
            numbers_in_evidence = set(_NUMBER.findall(evidence))
            numbers_missing = any(n not in numbers_in_evidence for n in _NUMBER.findall(value))
            if numbers_missing or not set(tokens(value)) & set(tokens(evidence)):
                cap(UNSUPPORTED_CAP, "value_not_in_evidence")
            if ocr.available:
                found, ocr_conf = locate(ocr, evidence)
                if found is not None:
                    region, region_source = found.model_dump(), "ocr"
                    if ocr_conf is not None and ocr_conf < LOW:
                        cap(HANDWRITTEN_CAP, "ocr_low_confidence")
                elif name in CRITICAL_FIELDS:
                    cap(LOW_CAP, "ocr_disagrees")
        if handwritten and name in CRITICAL_FIELDS:
            cap(HANDWRITTEN_CAP, "handwritten")

    result_band = band(confidence, value, field.legibility)
    return {
        "value": value,
        "model_confidence": round(field.confidence, 3),
        "confidence": round(confidence, 3),
        "band": result_band,
        "legibility": field.legibility,
        "evidence": field.evidence,
        "region": region,
        "region_source": region_source,
        "flags": flags,
        "critical": name in CRITICAL_FIELDS,
        "requires_confirmation": name in CRITICAL_FIELDS or result_band in ("medium", "low"),
        "message": LOW_MESSAGE if result_band == "low" else None,
    }


def assess(reading: PrescriptionExtraction, ocr: OcrResult) -> dict[str, Any]:
    hw = reading.handwritten
    return {
        "is_prescription": reading.is_prescription,
        "handwritten": hw,
        "reading_notes": reading.reading_notes,
        "items": [
            {f: assess_field(f, getattr(item, f), ocr=ocr, handwritten=hw) for f in ITEM_FIELDS}
            for item in reading.items
        ],
        "header": {
            f: assess_field(f, getattr(reading, f), ocr=ocr, handwritten=hw) for f in HEADER_FIELDS
        },
    }
