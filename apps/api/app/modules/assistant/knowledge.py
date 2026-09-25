"""Trusted knowledge library for general health and medicine information.

Only texts from allow-listed publishers enter the library, and only through
`scripts/import_knowledge.py` after a named clinical reviewer has checked them. Each text
records its source URL (which must be on the publisher's own domain), who reviewed it,
when, and when the review expires; expired or retired texts are never retrieved. Nothing
is fetched from the web at question time, so arbitrary internet content can never be a
source for an answer.

Retrieval is PostgreSQL full-text search over passages (`knowledge_chunks.search`), with
a boost for texts about medicines on the patient's list.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.injection import scan
from app.modules.assistant.models import KnowledgeChunk, KnowledgeDocument, KnowledgeStatus


@dataclass(frozen=True)
class Publisher:
    name: str
    domains: tuple[str, ...]


# Public-health bodies and official medicine references. Adding one is a reviewed change.
TRUSTED_PUBLISHERS: dict[str, Publisher] = {
    "who": Publisher("World Health Organization", ("who.int",)),
    "mohfw": Publisher(
        "Ministry of Health and Family Welfare, Government of India", ("mohfw.gov.in",)
    ),
    "icmr": Publisher("Indian Council of Medical Research", ("icmr.gov.in",)),
    "cdsco": Publisher("Central Drugs Standard Control Organisation", ("cdsco.gov.in",)),
    "medlineplus": Publisher(
        "MedlinePlus (U.S. National Library of Medicine)", ("medlineplus.gov",)
    ),
    "dailymed": Publisher(
        "DailyMed (U.S. National Library of Medicine)", ("dailymed.nlm.nih.gov",)
    ),
    "nhs": Publisher("NHS (UK National Health Service)", ("nhs.uk",)),
    "cdc": Publisher("U.S. Centers for Disease Control and Prevention", ("cdc.gov",)),
}
CATEGORIES = ("medication", "condition", "wellness", "emergency", "test", "procedure")
MAX_CHUNK_CHARS = 1400
MAX_DOCUMENT_CHARS = 60_000


class KnowledgeImportError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeText:
    publisher: str
    title: str
    url: str
    category: str
    reviewed_by: str
    reviewed_on: date
    review_due: date
    body: str
    language: str = "en"
    medicines: tuple[str, ...] = ()


def parse_markdown(raw: str) -> KnowledgeText:
    """A reviewed text: `---` front matter (key: value lines) then Markdown body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not match:
        raise KnowledgeImportError("missing front matter")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    missing = {
        "publisher",
        "title",
        "url",
        "category",
        "reviewed_by",
        "reviewed_on",
        "review_due",
    } - set(meta)
    if missing:
        raise KnowledgeImportError(f"missing fields: {', '.join(sorted(missing))}")
    try:
        reviewed_on = date.fromisoformat(meta["reviewed_on"])
        review_due = date.fromisoformat(meta["review_due"])
    except ValueError as exc:
        raise KnowledgeImportError("dates must be YYYY-MM-DD") from exc
    return KnowledgeText(
        publisher=meta["publisher"],
        title=meta["title"],
        url=meta["url"],
        category=meta["category"],
        reviewed_by=meta["reviewed_by"],
        reviewed_on=reviewed_on,
        review_due=review_due,
        body=match.group(2).strip(),
        language=meta.get("language", "en"),
        medicines=tuple(
            m.strip().lower() for m in meta.get("medicines", "").split(",") if m.strip()
        ),
    )


def validate(doc: KnowledgeText, today: date | None = None) -> None:
    today = today or datetime.now(UTC).date()
    publisher = TRUSTED_PUBLISHERS.get(doc.publisher)
    if publisher is None:
        raise KnowledgeImportError(f"publisher {doc.publisher!r} is not on the trusted list")
    parsed = urlparse(doc.url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == d or host.endswith("." + d) for d in publisher.domains
    ):
        raise KnowledgeImportError(f"URL must be https on {', '.join(publisher.domains)}")
    if doc.category not in CATEGORIES:
        raise KnowledgeImportError(f"category must be one of {', '.join(CATEGORIES)}")
    if not doc.reviewed_by.strip():
        raise KnowledgeImportError("a named clinical reviewer is required")
    if doc.reviewed_on > today or doc.review_due <= today or doc.review_due < doc.reviewed_on:
        raise KnowledgeImportError("review dates are invalid or the review has expired")
    if not doc.body or len(doc.body) > MAX_DOCUMENT_CHARS:
        raise KnowledgeImportError("body is empty or too long")
    injected = scan(doc.body)
    if injected.flagged:
        raise KnowledgeImportError(
            f"body contains AI-directed instructions ({', '.join(injected.reasons)})"
        )


def chunk(body: str) -> list[tuple[str | None, str]]:
    """Split on Markdown headings, then on paragraphs to keep passages short."""
    out: list[tuple[str | None, str]] = []
    heading: str | None = None
    for block in re.split(r"(?m)^(#{1,4} .+)$", body):
        block = block.strip()
        if not block:
            continue
        if re.match(r"^#{1,4} ", block):
            heading = block.lstrip("#").strip()[:300]
            continue
        current = ""
        for para in re.split(r"\n\s*\n", block):
            para = re.sub(r"\s+", " ", para).strip()
            if current and len(current) + len(para) > MAX_CHUNK_CHARS:
                out.append((heading, current))
                current = ""
            current = f"{current} {para}".strip()
        if current:
            out.append((heading, current[: MAX_CHUNK_CHARS * 2]))
    return out


async def import_text(
    session: AsyncSession, doc: KnowledgeText, *, actor: uuid.UUID | None = None
) -> KnowledgeDocument:
    """Insert or replace (same publisher + URL) a reviewed text and its passages."""
    validate(doc)
    digest = hashlib.sha256(doc.body.encode()).hexdigest()
    row = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.publisher == doc.publisher, KnowledgeDocument.url == doc.url
        )
    )
    if row is None:
        row = KnowledgeDocument(publisher=doc.publisher, url=doc.url, created_by=actor)
        session.add(row)
    row.title = doc.title
    row.language = doc.language
    row.category = doc.category
    row.medicines = list(doc.medicines)
    row.reviewed_by = doc.reviewed_by
    row.reviewed_on = doc.reviewed_on
    row.review_due = doc.review_due
    row.status = KnowledgeStatus.ACTIVE
    row.content_sha256 = digest
    row.updated_by = actor
    await session.flush()
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == row.id))
    session.add_all(
        KnowledgeChunk(
            document_id=row.id, seq=i, heading=h, body=b, created_by=actor, updated_by=actor
        )
        for i, (h, b) in enumerate(chunk(doc.body), start=1)
    )
    await session.flush()
    return row


# --- retrieval -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    id: str  # "lib:<chunk id>", what the model cites
    publisher: str
    title: str
    heading: str | None
    url: str
    reviewed_on: date
    text: str


_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def _tsquery(question: str, medicines: list[str]) -> str | None:
    words = list(dict.fromkeys(_WORD.findall(question.lower()) + [m for m in medicines if m]))[:24]
    safe = [w.replace("-", " ").split()[0] for w in words]
    return " | ".join(safe) if safe else None


async def search(
    session: AsyncSession, question: str, *, medicines: list[str] | None = None, limit: int = 4
) -> list[Passage]:
    """Best-matching passages from active, in-date texts."""
    meds = [m.lower() for m in (medicines or [])]
    mentioned = [m for m in meds if m and m in question.lower()]
    q = _tsquery(question, mentioned)
    if q is None:
        return []
    tsq = func.to_tsquery("english", q)
    rank = func.ts_rank_cd(KnowledgeChunk.search, tsq)
    today = datetime.now(UTC).date()
    stmt = (
        select(KnowledgeChunk, KnowledgeDocument, rank.label("rank"))
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeChunk.search.op("@@")(tsq),
            KnowledgeDocument.status == KnowledgeStatus.ACTIVE,
            KnowledgeDocument.review_due > today,
        )
    )
    if mentioned:  # texts about a medicine the person asked about come first
        boost = or_(*[KnowledgeDocument.medicines.contains([m]) for m in mentioned])
        stmt = stmt.order_by(boost.desc())
    rows = await session.execute(stmt.order_by(text("rank DESC"), KnowledgeChunk.id).limit(limit))
    return [
        Passage(
            id=f"lib:{c.id}",
            publisher=TRUSTED_PUBLISHERS.get(d.publisher, Publisher(d.publisher, ())).name,
            title=d.title,
            heading=c.heading,
            url=d.url,
            reviewed_on=d.reviewed_on,
            text=c.body,
        )
        for c, d, _ in rows.tuples()
    ]
