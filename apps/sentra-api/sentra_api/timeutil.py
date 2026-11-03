"""Timestamps leave the API as ISO 8601 in Asia/Jakarta with the +07:00 offset (contract A.2)."""

from datetime import datetime
from zoneinfo import ZoneInfo

JAKARTA = ZoneInfo("Asia/Jakarta")


def iso(value: datetime) -> str:
    return value.astimezone(JAKARTA).isoformat(timespec="seconds")


def iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else iso(value)
