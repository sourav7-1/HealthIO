"""Write the OpenAPI schema to a file so the TS client can be generated from it.

Usage: uv run python -m scripts.export_openapi ../../packages/api-client/openapi.json
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    schema = create_app().openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
