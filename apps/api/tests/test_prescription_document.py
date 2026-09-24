"""Prescription document rendering and content fingerprint (no database)."""

import uuid
from datetime import UTC, date, datetime

from app.modules.prescriptions.document import (
    DocumentItem,
    PatientInfo,
    PrescriberInfo,
    PrescriptionDocument,
    status_banner,
)
from app.modules.prescriptions.models import Prescription, PrescriptionItem
from app.modules.prescriptions.pdf import ReportLabPdfRenderer
from app.modules.prescriptions.service import content_fingerprint


def _document(status: str = "issued", **overrides: object) -> PrescriptionDocument:
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "revision": 1,
        "status": status,
        "prescribed_on": date(2026, 9, 24),
        "issued_at": datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        "valid_until": None,
        "diagnosis_as_written": "Placeholder assessment",
        "advice": "Placeholder advice\nsecond line <b>not markup</b> & more",
        "follow_up_on": date(2026, 10, 8),
        "follow_up_instructions": "Placeholder",
        "revision_reason": None,
        "supersedes_prescription_id": None,
        "superseded_by_id": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "content_sha256": "a" * 64,
        "prescriber": PrescriberInfo(
            name="Dr Placeholder",
            qualifications=["MBBS"],
            specialty="General practice",
            registration_council="Test Council",
            registration_number="TEST-1",
            practice_name="Placeholder Clinic",
            practice_address="Placeholder address",
        ),
        "patient": PatientInfo(name="Placeholder Person", age_years=40, sex="female"),
        "items": [
            DocumentItem(
                sequence=1,
                medicine="Medicine A",
                generic_name="Generic A",
                strength="500 mg",
                dosage_form="tablet",
                route="oral",
                dose="1 tablet",
                frequency="1-0-1",
                meal_relation="after_food",
                duration_days=5,
                is_prn=False,
                prn_reason=None,
                instructions="Placeholder instruction",
            )
        ],
    }
    fields.update(overrides)
    return PrescriptionDocument(**fields)  # type: ignore[arg-type]


def test_pdf_renders_for_every_status_and_escapes_text() -> None:
    renderer = ReportLabPdfRenderer()
    for status in ["issued", "superseded", "cancelled", "draft"]:
        pdf = renderer.render(_document(status))
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000


def test_pdf_survives_text_outside_latin1_without_a_unicode_font() -> None:
    pdf = ReportLabPdfRenderer().render(
        _document(patient=PatientInfo(name="प्लेसहोल्डर", age_years=None, sex=None))
    )
    assert pdf.startswith(b"%PDF")


def test_only_the_current_version_has_no_banner() -> None:
    assert status_banner(_document("issued")) is None
    for status in ["superseded", "cancelled", "draft", "entered_in_error"]:
        banner = status_banner(_document(status))
        assert banner is not None
        assert "not valid" in banner.lower()


def test_fingerprint_is_deterministic_and_content_sensitive() -> None:
    rx = Prescription(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        prescriber_doctor_id=uuid.uuid4(),
        prescribed_on=date(2026, 9, 24),
        revision=1,
    )
    item = PrescriptionItem(sequence=1, drug_name="Medicine A", strength="500 mg", is_prn=False)
    first = content_fingerprint(rx, [item])
    assert first == content_fingerprint(rx, [item])
    item.strength = "250 mg"
    assert content_fingerprint(rx, [item]) != first
