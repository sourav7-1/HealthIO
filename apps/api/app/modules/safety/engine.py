# ruff: noqa: E501  (user-facing sentences read better unwrapped)
"""The medication safety engine: deterministic rules over the patient's medicines,
allergies, conditions and a prescription, using only trusted reference data.

    medicines + allergies + conditions + prescription lines
        │
        ├─ duplicate medicines            same normalised name (no external data needed)
        ├─ duplicate active ingredients   only when ingredients are known reliably: a
        │                                 recorded generic name or a dataset product
        ├─ drug-drug interactions         only pairs an active dataset lists
        ├─ contraindications              dataset ingredient x recorded ICD-10 condition
        ├─ allergy conflicts              exact ingredient/name match with an allergy, or
        │                                 an allergy class from a dataset
        └─ inconsistent prescriptions     internal consistency of a written line
                                          (Health Io rules, not medical facts)
        ▼
    Finding  →  stored as a warning  →  doctor review / patient acknowledgement

Every finding is worded as "Potential issue detected. Please confirm with a
doctor/pharmacist." Nothing here tells anyone to stop or change a medicine, and no
interaction or contraindication exists unless a dataset lists it.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations

from app.modules.safety.models import WarningKind, WarningSeverity
from app.modules.safety.reference import Facts, Source, ingredient_key, name_key, split_generic

HEADLINE = "Potential issue detected. Please confirm with a doctor/pharmacist."
NO_CHANGE = "Please don't stop, skip or change any medicine on your own because of this."
RULES = Source(None, "Health Io prescription consistency rules", "1")
RECORD = Source(None, "Health Io record comparison", "1")

SEVERITY: dict[str, WarningSeverity] = {
    "minor": WarningSeverity.INFO,
    "moderate": WarningSeverity.CAUTION,
    "major": WarningSeverity.SERIOUS,
    "contraindicated": WarningSeverity.SERIOUS,
}


@dataclass(frozen=True)
class Med:
    ref: str  # "med:<id>" (current list) or "rxi:<id>" (prescription line)
    name: str
    generic_name: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    drug_code: str | None = None
    # Prescription line details (for consistency checks).
    prescription_ref: str | None = None
    frequency_text: str | None = None
    times_per_day: int | None = None
    dose_amount: Decimal | None = None
    dose_unit: str | None = None
    is_prn: bool = False
    prn_reason: str | None = None
    duration_days: int | None = None
    quantity: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class AllergyRec:
    ref: str
    substance: str
    substance_code: str | None = None


@dataclass(frozen=True)
class ConditionRec:
    ref: str
    name: str
    icd10: str


@dataclass(frozen=True)
class Finding:
    kind: WarningKind
    severity: WarningSeverity
    title: str
    patient_detail: str
    clinician_detail: str
    source: Source
    subjects: tuple[str, ...]
    source_severity: str | None = None
    needs: tuple[str, ...] = ()  # record categories needed to see the names involved
    # What makes this situation distinct besides the records involved (the ingredient
    # pair, the allergy, the consistency rule...). Never depends on row order.
    key: str = ""

    @property
    def fingerprint(self) -> str:
        raw = f"{self.kind.value}|{'|'.join(sorted(self.subjects))}|{self.key}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Resolved:
    med: Med
    ingredients: tuple[str, ...]
    ingredient_source: str | None  # "recorded generic name" or dataset name


def resolve(med: Med, facts: Facts) -> Resolved:
    """Ingredients from the recorded generic name, else from a dataset product (by code
    or name). Unknown stays unknown: no guessing from brand names."""
    recorded = split_generic(med.generic_name)
    if recorded:
        return Resolved(med, recorded, "recorded generic name")
    for key in ([f"code:{med.drug_code}"] if med.drug_code else []) + [name_key(med.name)]:
        if key in facts.products:
            ingredients, src = facts.products[key]
            return Resolved(med, ingredients, src.name)
    return Resolved(med, (), None)


def _para(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _names(*meds: Med) -> str:
    return " and ".join(m.name for m in meds)


# --- rules ----------------------------------------------------------------------------------


def duplicates(items: list[Resolved]) -> list[Finding]:
    out: list[Finding] = []
    by_name: dict[str, list[Resolved]] = {}
    for r in items:
        by_name.setdefault(name_key(r.med.name), []).append(r)
    same_name_pairs: set[frozenset[str]] = set()
    for group in by_name.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda r: r.med.ref)
        refs = tuple(r.med.ref for r in group)
        same_name_pairs |= {frozenset(p) for p in combinations(refs, 2)}
        name = group[0].med.name
        out.append(
            Finding(
                WarningKind.DUPLICATE_MEDICATION,
                WarningSeverity.CAUTION,
                f"{name} is listed more than once",
                _para(
                    f"{name} appears {len(group)} times in the medicine list or prescription.",
                    NO_CHANGE,
                ),
                f"{name} is recorded {len(group)} times ({', '.join(refs)}). "
                "It may be the same supply recorded twice or a second prescription.",
                RECORD,
                refs,
                key=name_key(name),
            )
        )
    for a, b in combinations(items, 2):
        if frozenset((a.med.ref, b.med.ref)) in same_name_pairs:
            continue
        shared = sorted(set(a.ingredients) & set(b.ingredients))
        if not shared:
            continue
        sources = "; ".join(sorted({s for s in (a.ingredient_source, b.ingredient_source) if s}))
        out.append(
            Finding(
                WarningKind.DUPLICATE_INGREDIENT,
                WarningSeverity.CAUTION,
                f"{_names(a.med, b.med)} share an active ingredient",
                _para(f"{_names(a.med, b.med)} both contain {', '.join(shared)}.", NO_CHANGE),
                f"Shared active ingredient(s): {', '.join(shared)}. Ingredients from: {sources}.",
                RECORD,
                (a.med.ref, b.med.ref),
                key=",".join(shared),
            )
        )
    return out


def interactions(items: list[Resolved], facts: Facts) -> list[Finding]:
    out: list[Finding] = []
    for a, b in combinations(items, 2):
        if name_key(a.med.name) == name_key(b.med.name):
            continue
        pairs = {tuple(sorted((x, y))) for x in a.ingredients for y in b.ingredients if x != y}
        for fact in facts.interactions:
            if (fact.a, fact.b) not in pairs:
                continue
            out.append(
                Finding(
                    WarningKind.INTERACTION,
                    SEVERITY[fact.severity],
                    f"Possible interaction: {_names(a.med, b.med)}",
                    _para(
                        f"{fact.source.name} lists {fact.a} and {fact.b} as a possible "
                        f"interaction (rated {fact.severity}).",
                        NO_CHANGE,
                    ),
                    f"{fact.a} + {fact.b} ({fact.severity}), as worded by {fact.source.name}"
                    f"{' ' + fact.source.version if fact.source.version else ''}: {fact.description}",
                    fact.source,
                    (a.med.ref, b.med.ref),
                    fact.severity,
                    key=f"{fact.a}|{fact.b}|{fact.source.dataset_id}",
                )
            )
    return out


def contraindications(
    items: list[Resolved], conditions: list[ConditionRec], facts: Facts
) -> list[Finding]:
    out: list[Finding] = []
    for r in items:
        for fact in facts.contraindications:
            if fact.ingredient not in r.ingredients:
                continue
            for c in conditions:
                if not c.icd10.upper().replace(" ", "").startswith(fact.icd10_prefix):
                    continue
                out.append(
                    Finding(
                        WarningKind.CONTRAINDICATION,
                        SEVERITY[fact.severity],
                        f"Possible concern with {r.med.name} and a recorded condition",
                        _para(
                            f"{fact.source.name} lists {fact.ingredient} (in {r.med.name}) as a possible "
                            f"concern for people with {fact.condition_label}, and {c.name} is in the health record.",
                            NO_CHANGE,
                        ),
                        f"{fact.ingredient} vs {fact.condition_label} (ICD-10 {fact.icd10_prefix}*, "
                        f"recorded: {c.name} {c.icd10}; {fact.severity}), as worded by "
                        f"{fact.source.name}: {fact.description}",
                        fact.source,
                        (r.med.ref, c.ref),
                        fact.severity,
                        ("medical_history",),
                        key=f"{fact.ingredient}|{fact.icd10_prefix}|{fact.source.dataset_id}",
                    )
                )
    return out


def allergy_conflicts(
    items: list[Resolved], allergies: list[AllergyRec], facts: Facts
) -> list[Finding]:
    out: list[Finding] = []
    for r in items:
        names = set(r.ingredients) | {ingredient_key(r.med.name)}
        for al in allergies:
            substance = ingredient_key(al.substance)
            exact = substance in names or (
                al.substance_code is not None and al.substance_code == r.med.drug_code
            )
            if exact:
                out.append(
                    Finding(
                        WarningKind.ALLERGY,
                        WarningSeverity.SERIOUS,
                        f"Possible allergy conflict: {r.med.name}",
                        _para(
                            f"{r.med.name} may contain something listed as an allergy in the health record "
                            f"({al.substance}).",
                            NO_CHANGE,
                        ),
                        f"{r.med.name} (ingredients: {', '.join(r.ingredients) or 'not known'}) matches the "
                        f"recorded allergy '{al.substance}'.",
                        RECORD,
                        (r.med.ref, al.ref),
                        needs=("medical_history",),
                        key=f"exact|{substance}",
                    )
                )
                continue
            for ing in r.ingredients:
                for class_key, label, src in facts.allergy_classes.get(ing, []):
                    if substance in facts.class_members.get(class_key, set()):
                        out.append(
                            Finding(
                                WarningKind.ALLERGY,
                                WarningSeverity.CAUTION,
                                f"Possible allergy conflict: {r.med.name}",
                                _para(
                                    f"{src.name} groups {ing} (in {r.med.name}) with {al.substance}, which is "
                                    f"listed as an allergy in the health record ({label}).",
                                    NO_CHANGE,
                                ),
                                f"{ing} and the recorded allergy '{al.substance}' are both in the class "
                                f"'{label}' in {src.name} {src.version or ''}.",
                                src,
                                (r.med.ref, al.ref),
                                needs=("medical_history",),
                                key=f"class|{class_key}|{substance}",
                            )
                        )
    return out


# --- prescription consistency (Health Io rules, not medical facts) ---------------------------

_ABBREV = {
    "od": 1, "qd": 1, "once daily": 1, "once a day": 1, "hs": 1, "at bedtime": 1,
    "bd": 2, "bid": 2, "twice daily": 2, "twice a day": 2,
    "tds": 3, "tid": 3, "thrice daily": 3, "three times a day": 3,
    "qid": 4, "qds": 4, "four times a day": 4,
}  # fmt: skip
_PRN = re.compile(r"\b(sos|prn|as needed|when needed|if needed|as required)\b", re.I)
_DASHES = re.compile(r"^\s*(\d+(?:\.\d+)?|½|1/2)(\s*-\s*(\d+(?:\.\d+)?|½|1/2)){2,3}\s*$")
_COUNTABLE = {
    "tablet",
    "tablets",
    "tab",
    "tabs",
    "capsule",
    "capsules",
    "cap",
    "caps",
    "sachet",
    "sachets",
}


def written_frequency(text: str | None) -> tuple[int | None, bool]:
    """(doses a day, as-needed?) from how a frequency is written: "1-0-1", "BD", "SOS"."""
    if not text:
        return None, False
    t = text.strip().lower()
    if _PRN.search(t):
        return None, True
    if _DASHES.match(t):
        parts = [p.strip() for p in t.split("-")]
        return sum(1 for p in parts if p not in ("0", "0.0")), False
    for word, n in _ABBREV.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            return n, False
    return None, False


def prescription_consistency(m: Med) -> list[Finding]:
    if m.prescription_ref is None:
        return []
    issues: list[tuple[WarningSeverity, str]] = []
    per_day, prn_written = written_frequency(m.frequency_text)
    if per_day is not None and m.times_per_day is not None and per_day != m.times_per_day:
        issues.append(
            (
                WarningSeverity.CAUTION,
                f"the frequency is written as '{m.frequency_text}' ({per_day} a day) but recorded as "
                f"{m.times_per_day} a day",
            )
        )
    if prn_written and not m.is_prn:
        issues.append(
            (
                WarningSeverity.CAUTION,
                f"'{m.frequency_text}' reads as 'when needed' but the line is scheduled",
            )
        )
    if m.is_prn and per_day is not None:
        issues.append(
            (
                WarningSeverity.CAUTION,
                f"the line is 'when needed' but the frequency is written as '{m.frequency_text}'",
            )
        )
    if m.is_prn and not (m.prn_reason and m.prn_reason.strip()):
        issues.append((WarningSeverity.INFO, "it is 'when needed' but doesn't say when to take it"))
    if (m.dose_amount is None) != (not (m.dose_unit and m.dose_unit.strip())):
        issues.append((WarningSeverity.INFO, "the dose amount or its unit is missing"))
    times = m.times_per_day or per_day
    if (
        m.quantity is not None
        and m.dose_amount is not None
        and times
        and m.duration_days
        and (m.dose_unit or "").strip().lower() in _COUNTABLE
    ):
        needed = m.dose_amount * times * m.duration_days
        if m.quantity < needed:
            issues.append(
                (
                    WarningSeverity.CAUTION,
                    f"the quantity ({m.quantity.normalize():f}) is less than dose x frequency x duration "
                    f"({needed.normalize():f})",
                )
            )
    if m.start_date and m.end_date and m.end_date < m.start_date:
        issues.append((WarningSeverity.CAUTION, "the end date is before the start date"))
    out: list[Finding] = []
    for severity, issue in issues:
        out.append(
            Finding(
                WarningKind.INCONSISTENT_PRESCRIPTION,
                severity,
                f"Prescription details for {m.name} don't match",
                _para(f"In the prescription for {m.name}, {issue}.", NO_CHANGE),
                f"{m.name}: {issue}.",
                RULES,
                (m.ref, m.prescription_ref),
                key=issue,
            )
        )
    return out


# --- entry point ----------------------------------------------------------------------------


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # medicines with unknown ingredients
    datasets: list[str] = field(default_factory=list)


def evaluate(
    meds: list[Med], allergies: list[AllergyRec], conditions: list[ConditionRec], facts: Facts
) -> Report:
    items = [resolve(m, facts) for m in meds]
    findings = (
        duplicates(items)
        + interactions(items, facts)
        + contraindications(items, conditions, facts)
        + allergy_conflicts(items, allergies, facts)
        + [f for r in items for f in prescription_consistency(r.med)]
    )
    unique: dict[str, Finding] = {}
    for f in findings:
        unique.setdefault(f.fingerprint, f)
    return Report(
        findings=list(unique.values()),
        unresolved=[r.med.name for r in items if not r.ingredients],
        datasets=facts.datasets,
    )
