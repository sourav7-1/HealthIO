"""Import a trusted structured drug dataset for the medication safety engine.

Usage: uv run python -m scripts.import_drug_reference path/to/dataset.json [--dry-run]

The file format is documented in app/modules/safety/reference.py. The dataset must come
from a source on TRUSTED_SOURCES, name its version, source URL (on the source's own
domain), licence, and the person who reviewed the conversion. Importing a new version of
a dataset retires the previous one. After importing, run a re-check for patients
(POST /patients/{id}/safety-warnings/recheck) or let the next change trigger it.

Health Io ships no interaction or contraindication data: it only reports what an
imported dataset contains.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.db import Database
from app.modules.safety.reference import ReferenceImportError, import_dataset, validate_dataset

SECTIONS = ("products", "interactions", "contraindications", "allergy_classes")


def load(path: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        validate_dataset(data)
    except (OSError, json.JSONDecodeError, ReferenceImportError) as exc:
        print(f"REJECTED {path}: {exc}")
        return None
    return data


async def main(path: Path, data: dict[str, Any], dry_run: bool) -> int:
    counts = {k: len(data.get(k, [])) for k in SECTIONS}
    print(f"ok {data['dataset']['key']} {data['dataset']['version']}: {counts}")
    if dry_run:
        return 0
    db = Database(get_settings())
    try:
        async with db.sessionmaker() as session:
            await import_dataset(session, data)
            await session.commit()
    except ReferenceImportError as exc:
        print(f"REJECTED {path}: {exc}")
        return 1
    finally:
        await db.dispose()
    print("imported")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    source = Path(args[0])
    loaded = load(source)
    if loaded is None:
        sys.exit(1)
    sys.exit(asyncio.run(main(source, loaded, "--dry-run" in sys.argv)))
