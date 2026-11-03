"""EP-13 agent trace (FR-19): per case and global."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.services import case_tools, trace_service

router = APIRouter(prefix="/api", tags=["trace"], dependencies=[Depends(require_user)])

GLOBAL_TRACE_LIMIT = 100


class TraceOut(BaseModel):
    trace_id: str
    run_id: str | None
    task: str | None
    case_id: str | None
    step: str
    tool: str | None
    input_summary: str | None
    output_summary: str | None
    decision: str | None
    duration_ms: int | None
    status: Literal["RUNNING", "OK", "ERROR", "SKIPPED", "AGENT_TIMEOUT", "LLM_OVERRIDDEN"]
    created_at: str


@router.get("/cases/{case_id}/trace", response_model=list[TraceOut])
def case_trace(case_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Every step of the case, oldest first."""
    case_tools.load_case(session, case_id)
    return trace_service.trace_rows(session, case_id=case_id, limit=None)


@router.get("/trace", response_model=list[TraceOut])
def global_trace(limit: int = Query(GLOBAL_TRACE_LIMIT, ge=1, le=1000), session: Session = Depends(get_session)) -> list[dict]:
    """The latest `limit` steps of all runs, oldest first."""
    return trace_service.trace_rows(session, case_id=None, limit=limit)
