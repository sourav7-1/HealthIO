"""Trusted drug reference data: import (validated) and lookup.

A dataset is a JSON file produced from an allow-listed structured source (for example
RxNorm ingredient relationships, a licensed interaction database, or the national
formulary). It must declare its publisher, version, source URL, licence and the person
who reviewed the import. Nothing is typed in by hand here and nothing is inferred: the
engine can only report what an active dataset contains.

    {
      "dataset": {"key": "...", "publisher": "nlm_rxnorm", "name": "...", "version": "...",
                  "url": "https://...", "license": "...", "reviewed_by": "...",
                  "reviewed_on": "YYYY-MM-DD"},
      "products": [{"code": "...", "name": "...", "ingredients": ["..."], "form": "..."}],
      "interactions": [{"a": "...", "b": "...", "severity": "moderate",
                        "description": "...", "ref": "..."}],
      "contraindications": [{"ingredient": "...", "icd10_prefix": "...", "condition": "...",
                             "severity": "major", "description": "...", "ref": "..."}],
      "allergy_classes": [{"class": "...", "label": "...", "ingredients": ["..."]}]
    }
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.safety.models import (
    AllergyClassMember,
    DatasetStatus,
    DrugContraindication,
    DrugInteraction,
    DrugProduct,
    ReferenceDataset,
    SourceSeverity,
)


@dataclass(frozen=True)
class Publisher:
    name: str
    domains: tuple[str, ...]


# Structured sources a dataset may come from. Adding one is a reviewed change; check the
# source's licence terms before importing (some interaction databases are non-commercial).
TRUSTED_SOURCES: dict[str, Publisher] = {
    "nlm_rxnorm": Publisher("RxNorm (U.S. National Library of Medicine)", ("nlm.nih.gov",)),
    "nlm_dailymed": Publisher(
        "DailyMed structured product labels (NLM)", ("dailymed.nlm.nih.gov",)
    ),
    "openfda": Publisher("openFDA drug labels (U.S. FDA)", ("open.fda.gov", "fda.gov")),
    "cdsco": Publisher("Central Drugs Standard Control Organisation, India", ("cdsco.gov.in",)),
    "nlem_india": Publisher(
        "National List of Essential Medicines, India (MoHFW)", ("mohfw.gov.in",)
    ),
    "ddinter": Publisher("DDInter drug-drug interaction database", ("ddinter.scbdd.com",)),
    "who_atc": Publisher("WHO ATC classification (WHOCC)", ("whocc.no",)),
}

_SALTS = {
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "sulfate", "sulphate",
    "maleate", "besylate", "besilate", "succinate", "tartrate", "citrate", "phosphate",
    "acetate", "bromide", "mesylate", "fumarate", "dihydrate", "monohydrate", "trihydrate",
}  # fmt: skip


def ingredient_key(value: str) -> str:
    """Normalised ingredient name: lower case, words only, common salt names dropped
    when another word remains ("metformin hydrochloride" -> "metformin")."""
    words = re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
    kept = [w for w in words if w not in _SALTS]
    return " ".join(kept or words)


def name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def split_generic(value: str | None) -> tuple[str, ...]:
    """Ingredients of a recorded generic name ("amoxicillin + clavulanic acid")."""
    if not value or not value.strip():
        return ()
    parts = re.split(r"\s*(?:\+|,|/|&|\band\b)\s*", value, flags=re.I)
    return tuple(dict.fromkeys(k for p in parts if (k := ingredient_key(p))))


class ReferenceImportError(ValueError):
    pass


def validate_dataset(data: dict[str, Any], today: date | None = None) -> None:
    today = today or datetime.now(UTC).date()
    meta = data.get("dataset")
    if not isinstance(meta, dict):
        raise ReferenceImportError("missing 'dataset' metadata")
    missing = {
        "key",
        "publisher",
        "name",
        "version",
        "url",
        "license",
        "reviewed_by",
        "reviewed_on",
    } - set(meta)
    if missing:
        raise ReferenceImportError(f"missing metadata: {', '.join(sorted(missing))}")
    publisher = TRUSTED_SOURCES.get(meta["publisher"])
    if publisher is None:
        raise ReferenceImportError(f"source {meta['publisher']!r} is not on the trusted list")
    url = urlparse(str(meta["url"]))
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not any(
        host == d or host.endswith("." + d) for d in publisher.domains
    ):
        raise ReferenceImportError(f"url must be https on {', '.join(publisher.domains)}")
    if not str(meta["license"]).strip() or not str(meta["reviewed_by"]).strip():
        raise ReferenceImportError("licence and a named reviewer are required")
    try:
        reviewed_on = date.fromisoformat(str(meta["reviewed_on"]))
    except ValueError as exc:
        raise ReferenceImportError("reviewed_on must be YYYY-MM-DD") from exc
    if reviewed_on > today:
        raise ReferenceImportError("reviewed_on is in the future")
    severities = {s.value for s in SourceSeverity}
    for i in data.get("interactions", []):
        if (
            not (i.get("a") and i.get("b") and i.get("description"))
            or i.get("severity") not in severities
        ):
            raise ReferenceImportError("each interaction needs a, b, severity and description")
        if ingredient_key(i["a"]) == ingredient_key(i["b"]):
            raise ReferenceImportError("an interaction needs two different ingredients")
    for c in data.get("contraindications", []):
        if not (
            c.get("ingredient")
            and c.get("icd10_prefix")
            and c.get("condition")
            and c.get("description")
        ):
            raise ReferenceImportError(
                "each contraindication needs ingredient, icd10_prefix, condition, description"
            )
        if c.get("severity") not in severities or not re.fullmatch(
            r"[A-Z][0-9]{0,2}(\.[0-9A-Z]{0,4})?", c["icd10_prefix"]
        ):
            raise ReferenceImportError("contraindication severity or ICD-10 prefix is invalid")
    for p in data.get("products", []):
        if not (p.get("name") and p.get("ingredients")):
            raise ReferenceImportError("each product needs a name and ingredients")
    for a in data.get("allergy_classes", []):
        if not (a.get("class") and a.get("label") and a.get("ingredients")):
            raise ReferenceImportError("each allergy class needs class, label and ingredients")


async def import_dataset(
    session: AsyncSession, data: dict[str, Any], actor: uuid.UUID | None = None
) -> ReferenceDataset:
    """Load a validated dataset as the active version of its key (older versions retire)."""
    validate_dataset(data)
    meta = data["dataset"]
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    existing = await session.scalar(
        select(ReferenceDataset).where(
            ReferenceDataset.key == meta["key"], ReferenceDataset.version == meta["version"]
        )
    )
    if existing is not None:
        raise ReferenceImportError(f"{meta['key']} {meta['version']} is already imported")
    await session.execute(
        update(ReferenceDataset)
        .where(ReferenceDataset.key == meta["key"], ReferenceDataset.status == DatasetStatus.ACTIVE)
        .values(status=DatasetStatus.RETIRED, updated_by=actor)
    )
    ds = ReferenceDataset(
        key=meta["key"],
        publisher=meta["publisher"],
        name=meta["name"],
        version=str(meta["version"]),
        url=meta["url"],
        license=meta["license"],
        reviewed_by=meta["reviewed_by"],
        reviewed_on=date.fromisoformat(str(meta["reviewed_on"])),
        content_sha256=digest,
        created_by=actor,
        updated_by=actor,
    )
    session.add(ds)
    await session.flush()
    rows: list[Any] = []
    for p in data.get("products", []):
        rows.append(
            DrugProduct(
                dataset_id=ds.id,
                code=p.get("code"),
                name=p["name"],
                name_key=name_key(p["name"]),
                ingredients=[ingredient_key(i) for i in p["ingredients"]],
                dosage_form=p.get("form"),
            )
        )
    for i in data.get("interactions", []):
        a, b = sorted((ingredient_key(i["a"]), ingredient_key(i["b"])))
        rows.append(
            DrugInteraction(
                dataset_id=ds.id,
                ingredient_a=a,
                ingredient_b=b,
                severity=SourceSeverity(i["severity"]),
                description=i["description"],
                source_ref=i.get("ref"),
            )
        )
    for c in data.get("contraindications", []):
        rows.append(
            DrugContraindication(
                dataset_id=ds.id,
                ingredient=ingredient_key(c["ingredient"]),
                icd10_prefix=c["icd10_prefix"].upper(),
                condition_label=c["condition"],
                severity=SourceSeverity(c["severity"]),
                description=c["description"],
                source_ref=c.get("ref"),
            )
        )
    for a in data.get("allergy_classes", []):
        for ing in a["ingredients"]:
            rows.append(
                AllergyClassMember(
                    dataset_id=ds.id,
                    class_key=a["class"],
                    class_label=a["label"],
                    ingredient=ingredient_key(ing),
                )
            )
    session.add_all(rows)
    await session.flush()
    return ds


# --- lookup ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    dataset_id: uuid.UUID | None
    name: str
    version: str | None
    ref: str | None = None


@dataclass(frozen=True)
class InteractionFact:
    a: str
    b: str
    severity: str
    description: str
    source: Source


@dataclass(frozen=True)
class ContraindicationFact:
    ingredient: str
    icd10_prefix: str
    condition_label: str
    severity: str
    description: str
    source: Source


@dataclass
class Facts:
    """Everything the engine may use, loaded for one patient's medicines."""

    products: dict[str, tuple[tuple[str, ...], Source]] = field(
        default_factory=dict
    )  # name/code key
    interactions: list[InteractionFact] = field(default_factory=list)
    contraindications: list[ContraindicationFact] = field(default_factory=list)
    # ingredient -> [(class key, class label, source)]
    allergy_classes: dict[str, list[tuple[str, str, Source]]] = field(default_factory=dict)
    class_members: dict[str, set[str]] = field(default_factory=dict)
    datasets: list[str] = field(default_factory=list)


async def load_products(session: AsyncSession, keys: set[str], codes: set[str]) -> Facts:
    facts = Facts()
    if not keys and not codes:
        return facts
    rows = await session.execute(
        select(DrugProduct, ReferenceDataset)
        .join(ReferenceDataset, ReferenceDataset.id == DrugProduct.dataset_id)
        .where(
            ReferenceDataset.status == DatasetStatus.ACTIVE,
            or_(DrugProduct.name_key.in_(keys or {""}), DrugProduct.code.in_(codes or {""})),
        )
    )
    for product, ds in rows.tuples():
        src = Source(ds.id, ds.name, ds.version, product.code)
        facts.products[product.name_key] = (tuple(product.ingredients), src)
        if product.code:
            facts.products[f"code:{product.code}"] = (tuple(product.ingredients), src)
    return facts


async def load_facts(session: AsyncSession, facts: Facts, ingredients: set[str]) -> Facts:
    if not ingredients:
        return facts
    active = ReferenceDataset.status == DatasetStatus.ACTIVE
    for i, ds in (
        await session.execute(
            select(DrugInteraction, ReferenceDataset)
            .join(ReferenceDataset, ReferenceDataset.id == DrugInteraction.dataset_id)
            .where(
                active,
                DrugInteraction.ingredient_a.in_(ingredients),
                DrugInteraction.ingredient_b.in_(ingredients),
            )
        )
    ).tuples():
        facts.interactions.append(
            InteractionFact(
                i.ingredient_a,
                i.ingredient_b,
                i.severity.value,
                i.description,
                Source(ds.id, ds.name, ds.version, i.source_ref),
            )
        )
    for c, ds in (
        await session.execute(
            select(DrugContraindication, ReferenceDataset)
            .join(ReferenceDataset, ReferenceDataset.id == DrugContraindication.dataset_id)
            .where(active, DrugContraindication.ingredient.in_(ingredients))
        )
    ).tuples():
        facts.contraindications.append(
            ContraindicationFact(
                c.ingredient,
                c.icd10_prefix,
                c.condition_label,
                c.severity.value,
                c.description,
                Source(ds.id, ds.name, ds.version, c.source_ref),
            )
        )
    members = (
        await session.execute(
            select(AllergyClassMember, ReferenceDataset)
            .join(ReferenceDataset, ReferenceDataset.id == AllergyClassMember.dataset_id)
            # Callers pass allergy substances too, so both sides of a class match load.
            .where(active, AllergyClassMember.ingredient.in_(ingredients))
        )
    ).tuples()
    for m, ds in members:
        src = Source(ds.id, ds.name, ds.version)
        facts.class_members.setdefault(m.class_key, set()).add(m.ingredient)
        facts.allergy_classes.setdefault(m.ingredient, []).append((m.class_key, m.class_label, src))
    facts.datasets = sorted(
        (await session.scalars(select(ReferenceDataset.name).where(active))).all()
    )
    return facts
