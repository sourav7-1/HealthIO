"""Medication safety engine rules, with synthetic placeholder reference data only (no
real drug facts are encoded in tests)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.modules.assistant.safety import forbidden_category
from app.modules.safety import engine
from app.modules.safety.engine import AllergyRec, ConditionRec, Med, written_frequency
from app.modules.safety.models import WarningKind, WarningSeverity
from app.modules.safety.reference import (
    ContraindicationFact,
    Facts,
    InteractionFact,
    ReferenceImportError,
    Source,
    ingredient_key,
    split_generic,
    validate_dataset,
)

SRC = Source(uuid.uuid4(), "Placeholder Dataset", "2026.1", "ref-1")


def facts(**kw: Any) -> Facts:
    f = Facts(datasets=["Placeholder Dataset"])
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def kinds(report: engine.Report) -> list[WarningKind]:
    return sorted(f.kind for f in report.findings)


# --- normalisation --------------------------------------------------------------------------


def test_ingredient_names_are_normalised() -> None:
    assert ingredient_key("Ingredientol Hydrochloride") == "ingredientol"
    assert ingredient_key("Sodium") == "sodium"  # never empties a name
    assert split_generic("Alpha-ol + Beta-ine / Gamma and Delta") == (
        "alpha ol",
        "beta ine",
        "gamma",
        "delta",
    )
    assert split_generic(None) == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1-0-1", (2, False)),
        ("1-1-1", (3, False)),
        ("0-0-1", (1, False)),
        ("1 - 0 - 0 - 1", (2, False)),
        ("½-0-½", (2, False)),
        ("BD", (2, False)),
        ("TDS after food", (3, False)),
        ("once daily", (1, False)),
        ("SOS", (None, True)),
        ("when needed for pain", (None, True)),
        ("as directed", (None, False)),
        (None, (None, False)),
    ],
)
def test_written_frequency(text: str | None, expected: tuple[int | None, bool]) -> None:
    assert written_frequency(text) == expected


# --- rules ---------------------------------------------------------------------------------


def test_duplicate_medicines_by_normalised_name() -> None:
    r = engine.evaluate(
        [Med("med:1", "Placeholder-A"), Med("med:2", "placeholder a"), Med("med:3", "Other")],
        [],
        [],
        facts(),
    )
    assert kinds(r) == [WarningKind.DUPLICATE_MEDICATION]
    assert set(r.findings[0].subjects) == {"med:1", "med:2"}


def test_duplicate_ingredient_only_with_reliable_ingredient_data() -> None:
    meds = [
        Med("med:1", "Brand One", generic_name="Alpha-ol"),
        Med("med:2", "Brand Two", generic_name="alpha-ol hydrochloride + beta-ine"),
        Med("med:3", "Brand Three"),  # unknown ingredients: nothing is guessed
    ]
    r = engine.evaluate(meds, [], [], facts())
    assert kinds(r) == [WarningKind.DUPLICATE_INGREDIENT]
    assert "alpha ol" in r.findings[0].patient_detail
    assert r.unresolved == ["Brand Three"]


def test_dataset_products_supply_ingredients() -> None:
    f = facts(products={"brandthree": (("alpha ol",), SRC)})
    r = engine.evaluate(
        [Med("med:1", "Brand One", generic_name="alpha-ol"), Med("med:3", "Brand Three")], [], [], f
    )
    assert kinds(r) == [WarningKind.DUPLICATE_INGREDIENT]
    assert "Placeholder Dataset" in r.findings[0].clinician_detail


def test_interactions_come_only_from_the_dataset() -> None:
    meds = [
        Med("med:1", "One", generic_name="alpha-ol"),
        Med("med:2", "Two", generic_name="beta-ine"),
    ]
    assert engine.evaluate(meds, [], [], facts()).findings == []  # no dataset entry: nothing
    fact = InteractionFact(
        "alpha ol", "beta ine", "moderate", "Placeholder wording from the source.", SRC
    )
    r = engine.evaluate(meds, [], [], facts(interactions=[fact]))
    (f,) = r.findings
    assert f.kind == WarningKind.INTERACTION
    assert f.severity == WarningSeverity.CAUTION
    assert f.source_severity == "moderate"
    assert f.source == SRC
    assert "Placeholder wording from the source." in f.clinician_detail
    assert "Placeholder wording" not in f.patient_detail  # source wording is for clinicians


@pytest.mark.parametrize(
    ("source", "ours"), [("minor", "info"), ("major", "serious"), ("contraindicated", "serious")]
)
def test_severity_classification(source: str, ours: str) -> None:
    meds = [
        Med("med:1", "One", generic_name="alpha-ol"),
        Med("med:2", "Two", generic_name="beta-ine"),
    ]
    fact = InteractionFact("alpha ol", "beta ine", source, "x", SRC)
    assert (
        engine.evaluate(meds, [], [], facts(interactions=[fact])).findings[0].severity.value == ours
    )


def test_contraindication_matches_icd10_prefix_of_recorded_condition() -> None:
    fact = ContraindicationFact(
        "alpha ol", "X12", "Placeholder condition group", "major", "Source text.", SRC
    )
    meds = [Med("med:1", "One", generic_name="alpha-ol")]
    hit = engine.evaluate(
        meds, [], [ConditionRec("cond:1", "Placeholder", "X12.3")], facts(contraindications=[fact])
    )
    assert kinds(hit) == [WarningKind.CONTRAINDICATION]
    assert hit.findings[0].needs == ("medical_history",)
    miss = engine.evaluate(
        meds, [], [ConditionRec("cond:1", "Other", "Y01")], facts(contraindications=[fact])
    )
    assert miss.findings == []


def test_allergy_exact_and_class_matches() -> None:
    meds = [
        Med("med:1", "One", generic_name="alpha-ol"),
        Med("med:2", "Two", generic_name="beta-ine"),
    ]
    exact = engine.evaluate(meds, [AllergyRec("allergy:1", "Alpha-ol")], [], facts())
    assert [(f.kind, f.severity) for f in exact.findings] == [
        (WarningKind.ALLERGY, WarningSeverity.SERIOUS)
    ]
    grouped = facts(
        allergy_classes={"beta ine": [("grp", "Placeholder group", SRC)]},
        class_members={"grp": {"beta ine", "gamma"}},
    )
    by_class = engine.evaluate(meds, [AllergyRec("allergy:2", "Gamma")], [], grouped)
    assert [(f.kind, f.severity) for f in by_class.findings] == [
        (WarningKind.ALLERGY, WarningSeverity.CAUTION)
    ]
    assert "Placeholder group" in by_class.findings[0].patient_detail


def _line(**kw: Any) -> Med:
    base: dict[str, Any] = {"ref": "rxi:1", "name": "Line", "prescription_ref": "rx:1"}
    return Med(**(base | kw))


@pytest.mark.parametrize(
    ("line", "phrase"),
    [
        (_line(frequency_text="1-0-1", times_per_day=3), "written as '1-0-1' (2 a day)"),
        (_line(frequency_text="SOS"), "reads as 'when needed'"),
        (_line(is_prn=True, frequency_text="BD", prn_reason="x"), "frequency is written as 'BD'"),
        (_line(is_prn=True), "doesn't say when"),
        (_line(dose_amount=Decimal("1")), "dose amount or its unit"),
        (
            _line(
                dose_amount=Decimal("1"),
                dose_unit="tablet",
                times_per_day=2,
                duration_days=5,
                quantity=Decimal("6"),
            ),
            "quantity (6) is less than",
        ),
        (
            _line(start_date=date.today(), end_date=date.today() - timedelta(days=1)),
            "end date is before",
        ),
    ],
)
def test_prescription_consistency(line: Med, phrase: str) -> None:
    r = engine.evaluate([line], [], [], facts())
    assert [f.kind for f in r.findings] == [WarningKind.INCONSISTENT_PRESCRIPTION]
    assert phrase in r.findings[0].patient_detail
    assert r.findings[0].source.name == "Health Io prescription consistency rules"


def test_a_consistent_line_raises_nothing() -> None:
    ok = _line(
        frequency_text="1-0-1", times_per_day=2, dose_amount=Decimal("1"), dose_unit="tablet",
        duration_days=5, quantity=Decimal("10"),
    )  # fmt: skip
    assert engine.evaluate([ok], [], [], facts()).findings == []


def test_every_warning_is_advisory_and_never_an_instruction() -> None:
    meds = [
        Med("med:1", "One", generic_name="alpha-ol"),
        Med("med:2", "one"),
        Med("med:4", "Four", generic_name="alpha-ol"),
        Med(
            "rxi:3",
            "Two",
            generic_name="beta-ine",
            prescription_ref="rx:1",
            frequency_text="1-0-1",
            times_per_day=1,
        ),
    ]
    f = facts(
        interactions=[InteractionFact("alpha ol", "beta ine", "major", "Avoid combination.", SRC)],
        contraindications=[
            ContraindicationFact("beta ine", "X1", "Group", "major", "Do not use.", SRC)
        ],
    )
    r = engine.evaluate(
        meds, [AllergyRec("allergy:1", "beta-ine")], [ConditionRec("cond:1", "C", "X10")], f
    )
    assert {x.kind for x in r.findings} == set(WarningKind)
    for finding in r.findings:
        assert finding.patient_detail.endswith(engine.NO_CHANGE)
        assert forbidden_category(finding.patient_detail) is None
    assert engine.HEADLINE == "Potential issue detected. Please confirm with a doctor/pharmacist."


def test_fingerprint_is_stable_for_the_same_situation() -> None:
    meds = [Med("med:1", "Placeholder"), Med("med:2", "placeholder")]
    a = engine.evaluate(meds, [], [], facts()).findings[0].fingerprint
    b = engine.evaluate(list(reversed(meds)), [], [], facts()).findings[0].fingerprint
    assert a == b


# --- dataset validation --------------------------------------------------------------------


def _dataset(**meta: Any) -> dict[str, Any]:
    return {
        "dataset": {
            "key": "placeholder",
            "publisher": "nlm_rxnorm",
            "name": "Placeholder",
            "version": "1",
            "url": "https://www.nlm.nih.gov/placeholder",
            "license": "Placeholder licence",
            "reviewed_by": "Placeholder Reviewer",
            "reviewed_on": "2026-01-01",
        }
        | meta,
        "interactions": [{"a": "alpha", "b": "beta", "severity": "moderate", "description": "x"}],
    }


def test_valid_dataset_passes() -> None:
    validate_dataset(_dataset())


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_dataset(publisher="some_blog"), "not on the trusted list"),
        (_dataset(url="https://nlm.nih.gov.example.com/x"), "https on"),
        (_dataset(license=" "), "licence"),
        (_dataset(reviewed_on="2999-01-01"), "future"),
        (
            _dataset()
            | {
                "interactions": [
                    {"a": "alpha", "b": "Alpha", "severity": "moderate", "description": "x"}
                ]
            },
            "two different",
        ),
        (
            _dataset()
            | {"interactions": [{"a": "a", "b": "b", "severity": "scary", "description": "x"}]},
            "severity",
        ),
        (
            _dataset()
            | {
                "contraindications": [
                    {
                        "ingredient": "a",
                        "icd10_prefix": "xx",
                        "condition": "c",
                        "severity": "major",
                        "description": "d",
                    }
                ]
            },
            "ICD-10",
        ),
    ],
)
def test_untrusted_or_malformed_datasets_are_rejected(data: dict[str, Any], error: str) -> None:
    with pytest.raises(ReferenceImportError, match=error):
        validate_dataset(data)
