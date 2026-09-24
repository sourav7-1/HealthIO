"""Eval gates for the prescription confidence layer (AI_SAFETY.md §9), on synthetic cases.

For every critical field the simulated model got wrong (or made up), the assessed band
must not be "high": the reviewer has to see it flagged. Correct, clearly supported
readings of printed prescriptions should stay "high", so reviewers are not flooded with
false alarms.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.ai.ocr import OcrResult, OcrWord
from app.ai.schemas import CRITICAL_FIELDS, ExtractedField, Region
from app.modules.extraction.assessment import assess_field

CASES = json.loads(
    (Path(__file__).parent / "evals" / "prescriptions" / "cases.json").read_text(encoding="utf-8")
)["cases"]


def _ocr(text: str) -> OcrResult:
    words = [
        OcrWord(text=w, confidence=0.95, region=Region(x=0.01 * i, y=0.1, w=0.01, h=0.02))
        for i, w in enumerate(text.split())
    ]
    return OcrResult(engine="eval", text=text, words=words)


def _field(spec: list[Any]) -> ExtractedField:
    value, confidence, evidence = spec
    return ExtractedField(
        value=value,
        confidence=confidence,
        legibility="clear" if value is not None else "not_present",
        evidence=evidence,
    )


def _results() -> list[tuple[str, str, bool, str]]:
    """(case, field, correct, band) for every critical field of every case."""
    out = []
    for case in CASES:
        ocr = _ocr(case["ocr"])
        for name in sorted(CRITICAL_FIELDS):
            assessed = assess_field(
                name, _field(case["reading"][name]), ocr=ocr, handwritten=case["handwritten"]
            )
            correct = assessed["value"] == case["truth"][name]
            out.append((case["id"], name, correct, assessed["band"]))
    return out


def test_no_wrong_critical_value_is_presented_as_high_confidence() -> None:
    unflagged = [(c, f, b) for c, f, ok, b in _results() if not ok and b == "high"]
    assert unflagged == []


def test_the_eval_set_contains_errors_to_catch() -> None:
    assert sum(1 for *_, ok, _ in _results() if not ok) >= 5


@pytest.mark.parametrize("case_id", ["printed-clean"])
def test_clean_printed_reading_is_not_over_flagged(case_id: str) -> None:
    bands = [b for c, _, ok, b in _results() if c == case_id and ok]
    assert bands
    assert set(bands) == {"high"}
