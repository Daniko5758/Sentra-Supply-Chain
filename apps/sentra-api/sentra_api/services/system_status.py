"""EP-26 System status: integration calls are recorded (RPT-1.6, AgentCore, the model behind an agent answer, the search tools of
the agent trace, TL-21) and read back as the latest call per name."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.models import IntegrationCall
from sentra_api.timeutil import iso

# Order of `integrations` in EP-26 (contract v1.10, DEC-032: `currents` and `gdelt` are no longer called by the agent, so they are
# not listed; their old rows stay in `integration_call`). `zoom` and `gmail` follow them while FEATURE_MEETING_ROOM is on (v1.11).
INTEGRATIONS = ("claude-4.6-sonnet", "sap-rpt-1.6", "sonar", "brave", "agentcore")
MEETING_INTEGRATIONS = ("zoom", "gmail")
MODEL_INTEGRATION = "claude-4.6-sonnet"
MODEL_ERROR = "AGENT_ERROR"  # the agent answered, but not with a model reply (never the text of its error)

# Agent search tools whose trace (TL-21 `tool`) counts as a call of an external integration (DEC-015).
TRACE_TOOL_INTEGRATIONS = {
    "search_web_sonar": "sonar",
    "search_brave_news": "brave",
}

FINISHED_TRACE_STATUSES = {"OK": "OK", "ERROR": "FAILED", "AGENT_TIMEOUT": "FAILED"}


def record_integration_call(
    session: Session, name: str, status: str, duration_ms: int | None, message: str | None, now: datetime
) -> None:
    """The caller commits. Never put secrets in `message`."""
    session.add(IntegrationCall(name=name, status=status, duration_ms=duration_ms, message=message, called_at=now))


def record_model_call(session: Session, *, answered: bool, duration_ms: int | None, now: datetime) -> None:
    """The model behind an agent answer: the trace carries no model marker, so it is recorded only for the tasks that are one model
    call (`ping`) or end in a model reply (`chat`). `duration_ms` is that of the whole agent call (it includes AgentCore)."""
    record_integration_call(session, MODEL_INTEGRATION, "OK" if answered else "FAILED", duration_ms,
                            None if answered else MODEL_ERROR, now)


def record_trace_integration(
    session: Session, *, tool: str | None, trace_status: str, duration_ms: int | None, message: str | None, now: datetime
) -> bool:
    """Record the integration behind a finished search-tool trace; False when the trace is not one (or still RUNNING)."""
    name = TRACE_TOOL_INTEGRATIONS.get(tool or "")
    status = FINISHED_TRACE_STATUSES.get(trace_status)
    if name is None or status is None:
        return False
    record_integration_call(session, name, status, duration_ms, message, now)
    return True


def integration_statuses(session: Session, *, meeting_room: bool = False) -> list[dict[str, Any]]:
    """One row per integration with its latest call; `UNKNOWN` until the first call. `meeting_room`: F-07 is on, so Zoom and Gmail
    are listed too."""
    latest: dict[str, IntegrationCall] = {}
    for call in session.scalars(select(IntegrationCall).order_by(IntegrationCall.called_at, IntegrationCall.id)):
        latest[call.name] = call
    rows = []
    for name in INTEGRATIONS + (MEETING_INTEGRATIONS if meeting_room else ()):
        call = latest.get(name)
        rows.append({
            "name": name, "status": call.status if call else "UNKNOWN",
            "last_call_at": iso(call.called_at) if call else None,
            "duration_ms": call.duration_ms if call else None, "message": call.message if call else None,
        })
    return rows
