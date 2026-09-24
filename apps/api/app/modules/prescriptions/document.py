"""The prescription *document*: one render model for every output format.

The web view (JSON → React), the PDF export and any future format (FHIR
MedicationRequest bundle, print HTML, signed PDF) are all produced from
`PrescriptionDocument`, so they can never disagree about what was prescribed. Renderers
only lay out what is here; they never compute or infer clinical content.

    build (router)  ──►  PrescriptionDocument  ──►  DocumentRenderer.render() ──► bytes
                                              └──►  JSON for the web view

Adding a format = implementing `DocumentRenderer` and registering it in `RENDERERS`.
Production notes (not implemented yet):
  * Digital signature (PAdES) with the doctor's certificate once step-up MFA exists.
  * Rendering in a worker and storing the file keyed by `content_sha256`, so repeated
    downloads of an immutable version are served from object storage.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class PrescriberInfo:
    name: str
    qualifications: list[str]
    specialty: str | None
    registration_council: str | None
    registration_number: str | None
    practice_name: str | None
    practice_address: str | None


@dataclass(frozen=True)
class PatientInfo:
    name: str
    age_years: int | None
    sex: str | None


@dataclass(frozen=True)
class DocumentItem:
    sequence: int
    medicine: str
    generic_name: str | None
    strength: str | None
    dosage_form: str | None
    route: str | None
    dose: str | None  # e.g. "1 tablet"
    frequency: str | None  # as written, e.g. "1-0-1"
    meal_relation: str | None
    duration_days: int | None
    is_prn: bool
    prn_reason: str | None
    instructions: str | None


@dataclass(frozen=True)
class VersionRef:
    id: uuid.UUID
    revision: int
    status: str
    issued_at: datetime | None
    revision_reason: str | None


@dataclass(frozen=True)
class PrescriptionDocument:
    id: uuid.UUID
    revision: int
    status: str
    prescribed_on: date | None
    issued_at: datetime | None
    valid_until: date | None
    diagnosis_as_written: str | None
    advice: str | None
    follow_up_on: date | None
    follow_up_instructions: str | None
    revision_reason: str | None
    supersedes_prescription_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    content_sha256: str | None
    prescriber: PrescriberInfo | None
    patient: PatientInfo | None  # None when the viewer may not see demographics
    items: list[DocumentItem]
    versions: list[VersionRef] = field(default_factory=list)

    @property
    def is_current(self) -> bool:
        return self.status == "issued"


class DocumentRenderer(Protocol):
    content_type: str
    file_extension: str

    def render(self, document: PrescriptionDocument) -> bytes: ...


MEAL_WORDS = {
    "before_food": "before food",
    "after_food": "after food",
    "with_food": "with food",
    "empty_stomach": "on an empty stomach",
    "bedtime": "at bedtime",
    "any": None,
}


def meal_words(value: str | None) -> str | None:
    if value is None:
        return None
    return MEAL_WORDS.get(value, value.replace("_", " "))


def status_banner(document: PrescriptionDocument) -> str | None:
    """Wording shown prominently on any rendering of a non-current version."""
    if document.status == "draft":
        return "DRAFT - not issued, not valid for dispensing"
    if document.status == "superseded":
        return "SUPERSEDED - replaced by a corrected version; not valid for dispensing"
    if document.status == "cancelled":
        return "CANCELLED by the prescriber - not valid for dispensing"
    if document.status == "entered_in_error":
        return "ENTERED IN ERROR - not valid"
    return None
