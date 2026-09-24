"""Synthetic, non-sensitive prescription samples for AI pipeline tests.

Images are drawn at test time with Pillow from placeholder text (no real people,
clinics, registrations or patients). Model answers are scripted to exercise the
pipeline's safety behaviour deterministically; no network calls are made.
"""

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from app.ai.ocr import OcrResult, OcrWord
from app.ai.providers import ExtractionFailedError, ModelReading, parse_reading
from app.ai.schemas import Region

SAMPLE_LINES = [
    "Placeholder Clinic",
    "Dr. Sample Doctor  Reg. No. TEST-0000",
    "Date: 01/09/2026",
    "",
    "1) Tab. Samplemycin 500 mg   1 tab   1-0-1   x 5 days   after food",
    "2) Syp. Exampledryl 5 ml   SOS   for cough",
]


def synthetic_image(
    lines: list[str] | None = None, *, size: tuple[int, int] = (1200, 900), fmt: str = "PNG"
) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for line in lines or SAMPLE_LINES:
        draw.text((40, y), line, fill="black")
        y += 40
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def f(
    value: str | None,
    confidence: float = 0.97,
    *,
    legibility: str = "clear",
    evidence: str | None = "same",
    region: dict[str, float] | None = None,
) -> dict[str, Any]:
    """One extracted field; evidence defaults to the value itself (verbatim)."""
    return {
        "value": value,
        "confidence": confidence,
        "legibility": legibility if value is not None or legibility != "clear" else "not_present",
        "evidence": value if evidence == "same" else evidence,
        "region": region,
    }


def absent() -> dict[str, Any]:
    return {"value": None, "confidence": 0.95, "legibility": "not_present", "evidence": None}


def item(**overrides: dict[str, Any]) -> dict[str, Any]:
    base = {
        "medicine_name": f("Tab. Samplemycin"),
        "strength": f("500 mg"),
        "dose": f("1 tab"),
        "frequency": f("1-0-1"),
        "duration": f("x 5 days"),
        "meal_relation": f("after food"),
        "instructions": absent(),
    }
    base.update(overrides)
    return base


def reading(items: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "is_prescription": True,
        "handwritten": False,
        "items": items if items is not None else [item()],
        "doctor_name": f("Dr. Sample Doctor"),
        "doctor_registration": f("TEST-0000"),
        "clinic_name": f("Placeholder Clinic"),
        "prescription_date": f("01/09/2026"),
        "reading_notes": [],
    }
    base.update(overrides)
    return base


@dataclass
class ScriptedExtractor:
    """Returns a scripted answer (validated exactly like a real model answer)."""

    answer: dict[str, Any] | None = None
    error: ExtractionFailedError | None = None
    provider: str = "scripted"
    calls: list[tuple[bytes, str, OcrResult]] = field(default_factory=list)

    async def extract(self, image: bytes, media_type: str, ocr: OcrResult) -> ModelReading:
        self.calls.append((image, media_type, ocr))
        if self.error is not None:
            raise self.error
        extraction, dropped = parse_reading(self.answer or reading())
        return ModelReading(
            extraction=extraction,
            provider=self.provider,
            model="scripted-model-1",
            prompt_version="prescription_extraction_v1",
            latency_ms=5,
            input_tokens=100,
            output_tokens=50,
            response_id="scripted-response",
            dropped_keys=dropped,
        )


@dataclass
class ScriptedOcr:
    words: list[tuple[str, float]] = field(default_factory=list)
    name: str = "scripted-ocr"

    async def read(self, image: Image.Image) -> OcrResult:
        out = [
            OcrWord(text=t, confidence=c, region=Region(x=0.05 + 0.05 * i, y=0.3, w=0.04, h=0.03))
            for i, (t, c) in enumerate(self.words)
        ]
        return OcrResult(engine=self.name, text=" ".join(t for t, _ in self.words), words=out)
