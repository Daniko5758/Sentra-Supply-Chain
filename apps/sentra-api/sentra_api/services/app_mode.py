"""Application mode DEMO/LIVE (DEC-024, EP-29): process memory of the one sentra-api instance, never the database.

`app.state.app_mode` is the mode itself (read by EP-03, EP-26, EP-08 and the payload builder); `app.state.mode_info`
holds who changed it and since when. The mode decides only whether a live scan may run (EP-08, going LIVE, the
scheduler of B-30) and what the UI shows; the numbers never depend on it.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from sentra_api.services.runs import start_scan
from sentra_api.timeutil import iso_or_none

DEMO, LIVE = "DEMO", "LIVE"
START_MODE = DEMO


@dataclass
class ModeInfo:
    """`live_since` is set only while LIVE; `changed_by` is the username of the last change (None since the start)."""

    live_since: datetime | None = None
    changed_by: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def init_mode(app: FastAPI) -> None:
    app.state.app_mode = START_MODE
    app.state.mode_info = ModeInfo()


def mode_view(app: FastAPI) -> dict[str, Any]:
    """EP-29 GET body. `next_scan_at` stays null until the scheduler (B-30) exists."""
    state = app.state
    return {
        "mode": state.app_mode,
        "live_since": iso_or_none(state.mode_info.live_since),
        "changed_by": state.mode_info.changed_by,
        "scan_interval_min": state.settings.scan_interval_min,
        "next_scan_at": None,
    }


def change_mode(app: FastAPI, session: Session, target: str, *, username: str, now: datetime) -> dict[str, Any]:
    """EP-29 PUT. The caller wakes the run queue afterwards (the scan is committed here).

    Same mode: nothing changes. DEMO -> LIVE: one scan (`trigger = auto`) is queued first, so a failure leaves the mode
    as it was; another scan still busy gives a SKIPPED run and `scan_run_id = null`, the mode still becomes LIVE.
    LIVE -> DEMO: a running scan is left to finish; no data is deleted. B-30 starts and stops its timer here.
    """
    state = app.state
    info: ModeInfo = state.mode_info
    with info.lock:
        scan_run_id = None
        if target != state.app_mode:
            if target == LIVE:
                run = start_scan(session, trigger="auto", now=now, record_skipped=True)
                scan_run_id = run.run_id if run.status == "QUEUED" else None
                session.commit()
                info.live_since = now
            else:
                info.live_since = None
            info.changed_by = username
            state.app_mode = target
        return {**mode_view(app), "scan_run_id": scan_run_id}
