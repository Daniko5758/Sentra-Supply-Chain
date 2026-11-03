"""Read and normalize the active JSON seed's D0-relative calendar dates (`data/<SENTRA_SEED_FILE>`, DEC-035)."""

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_SEED_FILE = "sentra_v2_1_1_seed.json"
KNOWN_DATASETS = ("DEMO", "TEST")


def seed_file_name() -> str:
    """The active seed file: env `SENTRA_SEED_FILE`, else the DEMO seed. Only a plain `*.json` name inside `data/` is allowed."""
    name = os.environ.get("SENTRA_SEED_FILE") or DEFAULT_SEED_FILE
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".json"):
        raise ValueError("SENTRA_SEED_FILE must be a file name inside data/")
    return name


def seed_path() -> Path:
    name = seed_file_name()
    path = Path(__file__).resolve().parents[3] / "data" / name
    if not path.is_file():
        raise RuntimeError(f"Seed file data/{name} not found (SENTRA_SEED_FILE)")
    return path


def seed_meta(bundle: dict[str, Any], file_name: str | None = None) -> dict[str, str]:
    """`{"file", "version", "dataset"}` of a loaded seed (EP-03, EP-26, E-10)."""
    meta = bundle["meta"]
    return {"file": file_name or seed_file_name(), "version": meta["version"], "dataset": meta["dataset"]}


def jakarta_today() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo("Asia/Jakarta")).date()


def _date_offset(value: Any, *, field: str, d0_date: date) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Seed date offset {field} must be an integer.")
    return (d0_date + timedelta(days=value)).isoformat()


def load_source_seed(d0_date: date, path: Path | None = None) -> dict[str, Any]:
    """Load the canonical seed and convert every business date offset to ISO."""
    source = path or seed_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read the SENTRA seed data {source.name}.") from exc

    date_columns = {
        "po_headers": ("created_on",),
        "po_items": ("requested_date",),
        "po_schedule_lines": ("confirmed_date",),
        "production_orders": ("start_date",),
        "sales_orders": ("requested_delivery_date", "confirmed_delivery_date"),
    }
    meta = raw.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("version"), str) or not meta["version"]:
        raise ValueError("Seed meta.version is required.")
    if meta.get("dataset") not in KNOWN_DATASETS:
        raise ValueError(f"Seed meta.dataset must be one of {', '.join(KNOWN_DATASETS)}.")
    mock_s4 = raw.get("mock_s4")
    sentra = raw.get("sentra")
    if not isinstance(mock_s4, dict) or not isinstance(sentra, dict):
        raise ValueError("Seed must contain mock_s4 and sentra objects.")

    for collection, columns in date_columns.items():
        rows = mock_s4.get(collection)
        if not isinstance(rows, list):
            raise ValueError(f"Seed collection mock_s4.{collection} must be a list.")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Seed row mock_s4.{collection}[{index}] must be an object.")
            for column in columns:
                row[column] = _date_offset(row.get(column), field=f"mock_s4.{collection}.{column}", d0_date=d0_date)

    events = sentra.get("demo_events")
    if not isinstance(events, dict):
        raise ValueError("Seed object sentra.demo_events must be an object.")
    for name, event in events.items():
        if not isinstance(event, dict):
            raise ValueError(f"Seed event sentra.demo_events.{name} must be an object.")
        event["effective_date"] = _date_offset(
            event.get("effective_date"), field=f"sentra.demo_events.{name}.effective_date", d0_date=d0_date
        )

    return raw
