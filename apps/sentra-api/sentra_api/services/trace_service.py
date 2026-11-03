"""Reads of the agent trace (`agent_trace`): the schedule summary of EP-04 and the timeline of EP-13."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentra_api.models import AgentTrace
from sentra_api.timeutil import iso

SCHEDULE_STEPS = ("schedule_check", "daily_schedule_check")

# Newest first; `TR-10000` sorts after `TR-9999` because the id is compared by length first (see services/ids.py).
_NEWEST_FIRST = (AgentTrace.created_at.desc(), func.length(AgentTrace.id).desc(), AgentTrace.id.desc())


def schedule_summary(session: Session) -> dict[str, Any] | None:
    """EP-04 `agent_summary`: the output of the latest finished `schedule_check` / `daily_schedule_check` step."""
    trace = session.scalars(
        select(AgentTrace)
        .where(AgentTrace.step.in_(SCHEDULE_STEPS), AgentTrace.status == "OK", AgentTrace.output_summary.is_not(None))
        .order_by(*_NEWEST_FIRST)
        .limit(1)
    ).first()
    if trace is None:
        return None
    return {"text": trace.output_summary, "generated_at": iso(trace.created_at), "source": trace.step}


def trace_rows(session: Session, *, case_id: str | None, limit: int | None) -> list[dict[str, Any]]:
    """EP-13: the latest `limit` steps (all when None) of one case, or of every run, oldest first (a timeline)."""
    query = select(AgentTrace).order_by(*_NEWEST_FIRST)
    if case_id is not None:
        query = query.where(AgentTrace.case_id == case_id)
    if limit is not None:
        query = query.limit(limit)
    return [
        {
            "trace_id": t.id, "run_id": t.run_id, "task": t.task, "case_id": t.case_id, "step": t.step, "tool": t.tool,
            "input_summary": t.input_summary, "output_summary": t.output_summary, "decision": t.decision,
            "duration_ms": t.duration_ms, "status": t.status, "created_at": iso(t.created_at),
        }
        for t in reversed(list(session.scalars(query)))
    ]
