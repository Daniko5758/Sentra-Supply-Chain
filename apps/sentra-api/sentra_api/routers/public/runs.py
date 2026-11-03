"""EP-08 manual scan and EP-09 run status. The worker that executes the runs is agent_runner (B-13)."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import Role, require_role, require_user
from sentra_api.db import utc_now
from sentra_api.deps import get_session
from sentra_api.errors import ApiError
from sentra_api.models import AgentRun
from sentra_api.services.runs import start_scan
from sentra_api.timeutil import iso_or_none

router = APIRouter(prefix="/api", tags=["runs"])


class ScanQueued(BaseModel):
    run_id: str
    task: Literal["scan_external"]
    status: str


class RunOut(BaseModel):
    run_id: str
    task: str
    trigger: Literal["manual", "auto"]
    case_id: str | None
    status: Literal["QUEUED", "RUNNING", "OK", "ERROR", "AGENT_TIMEOUT", "SKIPPED"]
    started_at: str | None
    finished_at: str | None
    summary: str | None
    error: str | None


@router.post("/risk/scan", status_code=202, response_model=ScanQueued,
             dependencies=[Depends(require_role(Role.BUYER, Role.SCM_MANAGER))])
def scan_now(request: Request, session: Session = Depends(get_session)) -> ScanQueued:
    """`Scan now`: only in LIVE mode (DEC-024), one scan at a time."""
    mode = request.app.state.app_mode
    if mode != "LIVE":
        raise ApiError(409, "CONFLICT", "A live scan is only available in LIVE mode.", {"mode": mode})
    run = start_scan(session, trigger="manual", now=utc_now())
    if run is None:
        raise ApiError(409, "CONFLICT", "A scan is already running.")
    queued = ScanQueued(run_id=run.run_id, task=run.task, status=run.status)  # before the worker can claim it
    session.commit()
    request.app.state.run_queue.wake()
    return queued


@router.get("/runs/{run_id}", response_model=RunOut, dependencies=[Depends(require_user)])
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise ApiError(404, "NOT_FOUND", f"Run {run_id} was not found.", {"run_id": run_id})
    return RunOut(
        run_id=run.run_id, task=run.task, trigger=run.trigger, case_id=run.case_id, status=run.status,
        started_at=iso_or_none(run.started_at), finished_at=iso_or_none(run.finished_at), summary=run.summary,
        error=run.error,
    )
