"""Import reviewed health-education texts into the assistant's trusted library.

Usage: uv run python -m scripts.import_knowledge path/to/dir-or-file.md [--dry-run]

Each file is Markdown with front matter:

    ---
    publisher: medlineplus            # key from TRUSTED_PUBLISHERS
    title: Taking medicines safely
    url: https://medlineplus.gov/...  # must be https on the publisher's domain
    category: medication              # medication, condition, wellness, emergency, test, procedure
    medicines: metformin              # optional, comma-separated generic names
    reviewed_by: Dr A. Reviewer (MBBS, Health Io clinical safety)
    reviewed_on: 2026-09-01
    review_due: 2027-09-01
    language: en
    ---
    ## Heading
    Text copied from the publisher's page (respect its licence) and checked by the reviewer.

Files that fail validation (publisher not trusted, wrong domain, expired review, text
containing AI-directed instructions…) are rejected and nothing from them is imported.
"""

import asyncio
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.db import Database
from app.modules.assistant.knowledge import (
    KnowledgeImportError,
    import_text,
    parse_markdown,
    validate,
)


async def main(paths: list[Path], dry_run: bool) -> int:
    files = [f for p in paths for f in (sorted(p.rglob("*.md")) if p.is_dir() else [p])]
    parsed = []
    failed = 0
    for f in files:
        try:
            doc = parse_markdown(f.read_text(encoding="utf-8"))
            validate(doc)
            parsed.append(doc)
            print(f"ok      {f}")
        except KnowledgeImportError as exc:
            failed += 1
            print(f"REJECT  {f}: {exc}")
    if dry_run or not parsed:
        return 1 if failed else 0
    db = Database(get_settings())
    try:
        async with db.sessionmaker() as session:
            for doc in parsed:
                await import_text(session, doc)
            await session.commit()
    finally:
        await db.dispose()
    print(f"imported {len(parsed)} text(s), rejected {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main([Path(a) for a in args], "--dry-run" in sys.argv)))
