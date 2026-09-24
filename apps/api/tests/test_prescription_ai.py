"""AI prescription pipeline: normalisation, confidence assessment, schema guard and
preprocessing, on synthetic samples (no database, no network)."""

import asyncio
import io
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from app.ai.ocr import OcrResult
from app.ai.preprocess import UnsupportedImageError, prepare
from app.ai.providers import parse_reading
from app.ai.schemas import ExtractedField, PrescriptionExtraction
from app.core.enums import MealRelation
from app.modules.extraction import normalize
from app.modules.extraction.assessment import LOW_MESSAGE, assess, assess_field
from app.modules.extraction.service import initial_review, plan_item
from tests.ai_samples import ScriptedOcr, absent, f, item, reading, synthetic_image

NO_OCR = OcrResult(engine="none")


def _field(**kw: object) -> ExtractedField:
    return ExtractedField.model_validate(f(**kw))  # type: ignore[arg-type]


# --- normalisation: fixed dictionaries only ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "per_day", "prn"),
    [
        ("1-0-1", 2, False),
        ("1-1-1", 3, False),
        ("0-0-1", 1, False),
        ("BD", 2, False),
        ("t.d.s", 3, False),
        ("OD", 1, False),
        ("SOS", None, True),
        ("as needed", None, True),
    ],
)
def test_frequency_dictionary(text: str, per_day: int | None, prn: bool) -> None:
    result = normalize.frequency(text)
    assert result is not None
    assert result.times_per_day == per_day
    assert result.is_prn == prn


@pytest.mark.parametrize("text", ["once a week", "alternate days", "0-0-0", "1-2", "", "take some"])
def test_unknown_frequency_is_not_interpreted(text: str) -> None:
    assert normalize.frequency(text) is None


def test_duration_meal_dose_and_strength() -> None:
    assert normalize.duration("x 5 days").days == 5  # type: ignore[union-attr]
    assert normalize.duration("5/7").days == 5  # type: ignore[union-attr]
    assert normalize.duration("2 weeks").days == 14  # type: ignore[union-attr]
    assert normalize.duration("2/52").days == 14  # type: ignore[union-attr]
    assert normalize.duration("1 month") is None  # not a fixed number of days
    assert normalize.meal("PC").relation == MealRelation.AFTER_FOOD  # type: ignore[union-attr]
    assert normalize.meal("AC").relation == MealRelation.BEFORE_FOOD  # type: ignore[union-attr]
    assert normalize.meal("with milk") is None
    one = normalize.dose("1 tab")
    assert one is not None
    assert (one.amount, one.unit) == (Decimal(1), "tablet")
    half = normalize.dose("½ tab")
    assert half is not None
    assert half.amount == Decimal("0.5")
    assert normalize.dose("1 spoonful") is None  # unknown unit stays as written
    assert normalize.strength("500MG") == "500 mg"
    assert normalize.strength("250 mg/5 ml") == "250 mg/5 ml"


def test_dates_are_day_first_and_never_in_the_future() -> None:
    today = date(2026, 9, 24)
    assert normalize.prescription_date("01/09/2026", today) == date(2026, 9, 1)
    assert normalize.prescription_date("3 Sep 2026", today) == date(2026, 9, 3)
    assert normalize.prescription_date("01/12/2026", today) is None  # future
    assert normalize.prescription_date("31/02/2026", today) is None  # not a date


# --- confidence assessment --------------------------------------------------------------------


def test_clear_critical_field_is_high_but_still_needs_confirmation() -> None:
    out = assess_field("dose", _field(value="1 tab"), ocr=NO_OCR, handwritten=False)
    assert out["band"] == "high"
    assert out["requires_confirmation"] is True
    assert out["message"] is None


def test_low_confidence_is_flagged_not_accepted() -> None:
    out = assess_field(
        "strength", _field(value="500 mg", confidence=0.4), ocr=NO_OCR, handwritten=False
    )
    assert out["band"] == "low"
    assert out["message"] == LOW_MESSAGE
    # The reviewer does not get the uncertain reading pre-filled.
    review = initial_review(
        assess(
            PrescriptionExtraction.model_validate(reading([item(strength=f("500 mg", 0.4))])),
            NO_OCR,
        )
    )
    assert review["items"][0]["fields"]["strength"]["value"] is None
    assert review["items"][0]["fields"]["medicine_name"]["value"] == "Tab. Samplemycin"


def test_value_without_supporting_text_is_capped() -> None:
    no_evidence = assess_field(
        "dose", _field(value="1 tab", evidence=None), ocr=NO_OCR, handwritten=False
    )
    assert "no_evidence" in no_evidence["flags"]
    assert no_evidence["band"] == "low"
    # A number the page does not show (e.g. a guessed dose) is treated as unsupported.
    invented = assess_field(
        "dose", _field(value="2 tab", evidence="1 tab"), ocr=NO_OCR, handwritten=False
    )
    assert "value_not_in_evidence" in invented["flags"]
    assert invented["confidence"] <= 0.3


def test_missing_and_illegible_fields_stay_empty() -> None:
    missing = assess_field(
        "dose", ExtractedField.model_validate(absent()), ocr=NO_OCR, handwritten=False
    )
    assert missing["value"] is None
    assert missing["band"] == "absent"
    unreadable = assess_field(
        "frequency",
        ExtractedField.model_validate(
            {"value": None, "confidence": 0.2, "legibility": "illegible"}
        ),
        ocr=NO_OCR,
        handwritten=False,
    )
    assert unreadable["band"] == "low"
    assert unreadable["message"] == LOW_MESSAGE


def test_ocr_disagreement_and_region_from_ocr() -> None:
    words = ScriptedOcr(words=[("Tab.", 0.95), ("Samplemycin", 0.93), ("500", 0.9), ("mg", 0.9)])
    ocr = asyncio.run(words.read(Image.new("L", (10, 10))))
    agree = assess_field(
        "medicine_name", _field(value="Tab. Samplemycin"), ocr=ocr, handwritten=False
    )
    assert agree["region_source"] == "ocr"
    assert agree["band"] == "high"
    disagree = assess_field("frequency", _field(value="1-0-1"), ocr=ocr, handwritten=False)
    assert "ocr_disagrees" in disagree["flags"]
    assert disagree["band"] == "low"


def test_handwritten_critical_fields_are_never_high() -> None:
    out = assess_field("strength", _field(value="500 mg"), ocr=NO_OCR, handwritten=True)
    assert out["band"] == "medium"
    assert "handwritten" in out["flags"]


# --- schema guard -----------------------------------------------------------------------------


def test_invented_diagnosis_is_discarded_and_recorded() -> None:
    answer = reading()
    answer["diagnosis"] = "should never be kept"
    answer["items"][0]["indication"] = "should never be kept"
    parsed, dropped = parse_reading(answer)
    assert dropped == ["diagnosis"]
    assert "diagnosis" not in parsed.model_dump()
    assert "indication" not in parsed.items[0].model_dump()


def test_malformed_region_is_dropped_not_fatal() -> None:
    answer = reading([item(strength=f("500 mg", region={"x": 0.9, "y": 0.1, "w": 0.5, "h": 0.1}))])
    parsed, _ = parse_reading(answer)
    assert parsed.items[0].strength.region is None


# --- conversion never invents -----------------------------------------------------------------


def _reviewed(**values: str | None) -> dict[str, dict[str, object]]:
    fields = {}
    for name in [
        "medicine_name",
        "strength",
        "dose",
        "frequency",
        "duration",
        "meal_relation",
        "instructions",
    ]:
        v = values.get(name)
        fields[name] = {"status": "not_on_prescription" if v is None else "confirmed", "value": v}
    return fields


def test_missing_dose_is_saved_as_missing() -> None:
    plan = plan_item(
        _reviewed(medicine_name="Tab. Samplemycin", strength="500 mg", frequency="1-0-1")
    )
    assert plan.dose_amount is None
    assert plan.dose_unit is None
    assert plan.times_per_day == 2
    assert plan.duration_days is None


def test_unrecognised_text_is_kept_verbatim() -> None:
    plan = plan_item(
        _reviewed(
            medicine_name="Syp. Exampledryl",
            dose="1 spoonful",
            frequency="SOS",
            duration="1 month",
            meal_relation="with milk",
            instructions="for cough",
        )
    )
    assert plan.is_prn is True
    assert plan.prn_reason == "as written: SOS"
    assert plan.frequency_text == "SOS"
    assert plan.instructions == (
        "for cough; Dose as written: 1 spoonful; Duration as written: 1 month; Food: with milk"
    )


# --- preprocessing ----------------------------------------------------------------------------


def test_preprocessing_orients_and_strips_metadata() -> None:
    image = Image.open(io.BytesIO(synthetic_image(size=(1200, 700))))
    exif = image.getexif()
    exif[0x0112] = 6  # rotated 90°
    exif[0x010F] = "Placeholder Camera"
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", exif=exif.tobytes())
    prepared = prepare(buf.getvalue(), "image/jpeg")
    assert "exif_orientation_applied" in prepared.steps
    assert (prepared.width, prepared.height) == (700, 1200)
    out = Image.open(io.BytesIO(prepared.model_bytes))
    assert not out.getexif()


def test_preprocessing_refuses_what_it_cannot_read() -> None:
    with pytest.raises(UnsupportedImageError) as pdf:
        prepare(b"%PDF-1.7", "application/pdf")
    assert pdf.value.code == "unsupported_type"
    with pytest.raises(UnsupportedImageError) as small:
        prepare(synthetic_image(size=(300, 200)), "image/png")
    assert small.value.code == "too_small"
    with pytest.raises(UnsupportedImageError) as broken:
        prepare(b"not an image", "image/png")
    assert broken.value.code == "unreadable_image"
