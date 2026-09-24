"""The structured output a vision model must return for a prescription image.

Deliberately *absent*: diagnosis, indication, advice on what to take. The model is asked
only to transcribe what is visibly written; anything else it returns is discarded (and
the discarding is recorded). A field that is not visible is `null` with legibility
`not_present`; a field that is visible but unreadable is `null` with `illegible`.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Legibility = Literal["clear", "partly_legible", "illegible", "not_present"]

ITEM_FIELDS = (
    "medicine_name",
    "strength",
    "dose",
    "frequency",
    "duration",
    "meal_relation",
    "instructions",
)
HEADER_FIELDS = ("doctor_name", "doctor_registration", "clinic_name", "prescription_date")
# Always need explicit human confirmation, whatever the confidence (AI_SAFETY.md §4.2).
CRITICAL_FIELDS = frozenset({"medicine_name", "strength", "dose", "frequency"})


class Region(BaseModel):
    """Bounding box as fractions (0-1) of the image width and height."""

    model_config = ConfigDict(extra="ignore")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str | None = Field(default=None, max_length=300)
    confidence: float = Field(ge=0, le=1)
    legibility: Legibility
    # The exact characters as seen on the page, before any interpretation.
    evidence: str | None = Field(default=None, max_length=300)
    region: Region | None = None

    @field_validator("region", mode="before")
    @classmethod
    def _lenient_region(cls, v: Any) -> Any:
        """A malformed box is dropped (no highlight shown), not a reason to reject the
        whole reading."""
        if v is None:
            return None
        try:
            region = Region.model_validate(v)
        except ValidationError:
            return None
        if region.x + region.w > 1.001 or region.y + region.h > 1.001:
            return None
        return region


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    medicine_name: ExtractedField
    strength: ExtractedField
    dose: ExtractedField
    frequency: ExtractedField
    duration: ExtractedField
    meal_relation: ExtractedField
    instructions: ExtractedField


class PrescriptionExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_prescription: bool
    handwritten: bool
    items: list[ExtractedItem] = Field(max_length=30)
    doctor_name: ExtractedField
    doctor_registration: ExtractedField
    clinic_name: ExtractedField
    prescription_date: ExtractedField
    # Problems the model noticed: "bottom of the page cut off", "two dates visible", …
    reading_notes: list[str] = Field(default_factory=list, max_length=10)


def _field_schema(description: str) -> dict[str, object]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {
                "type": ["string", "null"],
                "description": "Exactly as written (abbreviations kept). null if not present "
                "or not readable. Never a guess, never a typical value.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How sure you are that `value` is exactly what is written.",
            },
            "legibility": {
                "type": "string",
                "enum": ["clear", "partly_legible", "illegible", "not_present"],
            },
            "evidence": {
                "type": ["string", "null"],
                "description": "The verbatim characters on the page this value was read from.",
            },
            "region": {
                "type": ["object", "null"],
                "description": "Where the evidence is, as fractions of image width/height.",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "w": {"type": "number"},
                    "h": {"type": "number"},
                },
                "required": ["x", "y", "w", "h"],
            },
        },
        "required": ["value", "confidence", "legibility", "evidence"],
    }


ITEM_DESCRIPTIONS = {
    "medicine_name": "Medicine name as written, including the form prefix if written (Tab., Syp.).",
    "strength": "Strength as written, e.g. '500 mg'. null if not written.",
    "dose": "Amount per intake as written, e.g. '1 tab', '5 ml'. null if not written. "
    "NEVER infer a dose from the strength or from typical use.",
    "frequency": "How often, as written, e.g. '1-0-1', 'BD', 'SOS'.",
    "duration": "For how long, as written, e.g. '5 days', 'x 1 wk', '5/7'.",
    "meal_relation": "Relation to food as written, e.g. 'after food', 'AC', 'PC'.",
    "instructions": "Any other instruction written for this medicine.",
}
HEADER_DESCRIPTIONS = {
    "doctor_name": "Prescriber's name, only if clearly printed or written.",
    "doctor_registration": "Prescriber's registration number, only if visible.",
    "clinic_name": "Clinic or hospital name, only if visible.",
    "prescription_date": "Date written on the prescription, as written.",
}

TOOL_NAME = "record_prescription_transcription"


def tool_input_schema() -> dict[str, object]:
    item = {
        "type": "object",
        "properties": {f: _field_schema(ITEM_DESCRIPTIONS[f]) for f in ITEM_FIELDS},
        "required": list(ITEM_FIELDS),
    }
    return {
        "type": "object",
        "properties": {
            "is_prescription": {"type": "boolean"},
            "handwritten": {"type": "boolean"},
            "items": {"type": "array", "items": item, "maxItems": 30},
            **{f: _field_schema(HEADER_DESCRIPTIONS[f]) for f in HEADER_FIELDS},
            "reading_notes": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        },
        "required": ["is_prescription", "handwritten", "items", *HEADER_FIELDS],
    }
