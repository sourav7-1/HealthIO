"""Run the synthetic prescription eval set against the configured live vision model.

Usage (from apps/api, with HIO_AI_PROVIDER=anthropic and HIO_ANTHROPIC_API_KEY set):
    uv run python -m scripts.eval_prescriptions_live

Each case's page text is rendered into a synthetic image (placeholder names only), read
by the real model through the same preprocessing and assessment as production, and
compared with the truth. Reports field accuracy among "high" fields and the number of
wrong critical values that were NOT flagged (AI_SAFETY.md §9 gates). Costs API credits.
"""

import asyncio
import json
import sys
from pathlib import Path

from app.ai.ocr import OcrResult
from app.ai.preprocess import prepare
from app.ai.providers import build_extractor
from app.ai.schemas import CRITICAL_FIELDS
from app.core.config import get_settings
from app.modules.extraction.assessment import assess
from tests.ai_samples import synthetic_image

CASES = Path(__file__).resolve().parents[1] / "tests" / "evals" / "prescriptions" / "cases.json"


async def main() -> int:
    extractor = build_extractor(get_settings())
    if extractor is None:
        print("AI provider is not configured (HIO_AI_PROVIDER / HIO_ANTHROPIC_API_KEY).")
        return 2
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    high_total = high_correct = unflagged_errors = 0
    for case in cases:
        # Printed cases render their page text; the others render the true values
        # (synthetic images are typed, so "handwritten" cases only test the bookkeeping).
        page = case["ocr"] or " ".join(v for v in case["truth"].values() if v)
        lines = ["Placeholder Clinic", "Dr. Sample Doctor", "", page]
        prepared = prepare(synthetic_image(lines), "image/png")
        reading = await extractor.extract(
            prepared.model_bytes, prepared.media_type, OcrResult("none")
        )
        assessed = assess(reading.extraction, OcrResult("none"))
        first = assessed["items"][0] if assessed["items"] else {}
        for name in sorted(CRITICAL_FIELDS):
            field = first.get(name)
            got = field["value"] if field else None
            ok = got == case["truth"][name]
            band = field["band"] if field else "absent"
            if band == "high":
                high_total += 1
                high_correct += ok
            if not ok and band == "high":
                unflagged_errors += 1
            print(f"{case['id']:32} {name:14} {band:7} {'ok' if ok else 'WRONG'}  {got!r}")
    accuracy = high_correct / high_total if high_total else 0.0
    print(f"\nAccuracy among high-confidence critical fields: {accuracy:.1%} (gate >= 98%)")
    print(f"Wrong critical values not flagged: {unflagged_errors} (gate: 0 on this set)")
    return 0 if accuracy >= 0.98 and unflagged_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
