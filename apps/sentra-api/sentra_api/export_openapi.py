"""Writes `openapi.json` (the public API for the frontend) next to `pyproject.toml`: `python -m sentra_api.export_openapi`.

Run it again whenever an endpoint or a response model changes; tests/test_b17_public_api.py fails while the file is stale.
"""

import json
from pathlib import Path
from typing import Any

from sentra_api.config import Settings
from sentra_api.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def build_openapi() -> dict[str, Any]:
    """The schema of an app with in-memory databases and no secrets: building it starts nothing and reads no environment file."""
    app = create_app(Settings(_env_file=None), database_url="sqlite://", mock_database_url="sqlite://")
    return app.openapi()


def main() -> None:
    OPENAPI_PATH.write_text(json.dumps(build_openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()
