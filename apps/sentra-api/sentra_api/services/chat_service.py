"""EP-18 chat: one synchronous `chat` task per question (PRD 6.4, contract D.1/D.2).

The request creates an `agent_run` (RUNNING, so the queue worker leaves it alone), calls the agent with the last
messages of the conversation, and stores the question and the answer in `chat_message` only when the agent answered.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.agent_runner import AgentInvokeError, AgentRunner, build_payload
from sentra_api.errors import ApiError
from sentra_api.models import AgentRun, Case, ChatMessage
from sentra_api.services.case_tools import add_trace
from sentra_api.services.runs import start_inline_run
from sentra_api.services.system_status import record_integration_call, record_model_call

CHAT_TASK = "chat"
HISTORY_LIMIT = 10  # contract D.2: at most the last 10 messages
CITATION_TYPES = ("record", "url")


@dataclass(frozen=True)
class ChatTurn:
    run_id: str
    chat_session_id: str  # also the `runtimeSessionId` of the agent call
    payload: dict[str, Any]
    asked_at: datetime
    username: str
    case_id: str | None
    message: str


def parse_session_id(value: str | None) -> str | None:
    """The UUID of an existing conversation in its canonical form; None for a new one. Anything else is a 400."""
    if value is None:
        return None
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        canonical = None
    if canonical != value.lower():
        raise ApiError(400, "VALIDATION_FAILED", "chat_session_id must be a UUID.", {"field": "chat_session_id"})
    return canonical


def prepare_turn(
    session: Session, *, username: str, case_id: str | None, message: str, chat_session_id: str | None, app_mode: str,
    callback_base_url: str, now: datetime,
) -> ChatTurn:
    """Check the request, read the history and create the run. The caller commits."""
    case = None
    if case_id is not None:
        case = session.get(Case, case_id)
        if case is None:
            raise ApiError(404, "NOT_FOUND", f"Case {case_id} was not found.", {"case_id": case_id})
    session_id = parse_session_id(chat_session_id)
    history: list[ChatMessage] = []
    if session_id is not None:
        history = list(session.scalars(
            select(ChatMessage).where(ChatMessage.chat_session_id == session_id).order_by(ChatMessage.id)))
        if any(row.username != username for row in history):  # a conversation is private to its user
            raise ApiError(404, "NOT_FOUND", "Chat session was not found.", {"chat_session_id": session_id})
    session_id = session_id or str(uuid.uuid4())
    run = start_inline_run(session, task=CHAT_TASK, trigger="manual", case_id=case_id, now=now)
    payload = build_payload(run, case, app_mode=app_mode, callback_base_url=callback_base_url)
    payload["message"] = message
    payload["history"] = [{"role": row.role, "content": row.content} for row in history[-HISTORY_LIMIT:]]
    return ChatTurn(run.run_id, session_id, payload, now, username, case_id, message)


def invoke_agent(runner: AgentRunner, turn: ChatTurn) -> tuple[dict[str, Any] | None, AgentInvokeError | None, int]:
    """Call the agent (blocks up to 150 s). Returns the D.3 answer or the failure, and the duration in ms."""
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    failure: AgentInvokeError | None = None
    try:
        result = runner.invoke(turn.payload, turn.chat_session_id)
    except AgentInvokeError as error:
        failure = error
    except Exception as error:
        failure = AgentInvokeError("ERROR", f"Unexpected error: {type(error).__name__}", code=type(error).__name__)
    return result, failure, int((time.perf_counter() - started) * 1000)


def _citations(raw: Any) -> list[dict[str, str]]:
    """Keep the well-formed citations (`type` record|url and a text `id`); other keys of the agent are ignored."""
    if not isinstance(raw, list):
        return []
    return [{"type": item["type"], "id": item["id"]} for item in raw
            if isinstance(item, dict) and item.get("type") in CITATION_TYPES and isinstance(item.get("id"), str)]


def _reply(result: dict[str, Any]) -> dict[str, Any] | None:
    data = result.get("data")
    answer = data.get("answer") if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return None
    return {"answer": answer, "citations": _citations(data.get("citations")), "validated": data.get("validated") is True}


def _outcome(
    result: dict[str, Any] | None, failure: AgentInvokeError | None
) -> tuple[dict[str, Any] | None, str, str | None, str | None, str | None]:
    """(reply, run status, short reason, error text, summary) of the agent call."""
    if failure is not None:
        return None, ("AGENT_TIMEOUT" if failure.kind == "TIMEOUT" else "ERROR"), failure.code, str(failure), None
    summary = result.get("summary") if isinstance(result.get("summary"), str) else None
    if result["status"] == "OK":
        reply = _reply(result)
        if reply is not None:
            return reply, "OK", None, None, summary
        return None, "ERROR", "BAD_RESPONSE", "The agent answered without an answer text.", summary
    if result["status"] == "AGENT_TIMEOUT":
        return None, "AGENT_TIMEOUT", "TIMEOUT", summary or "The agent timed out.", summary
    return None, "ERROR", "AGENT_ERROR", summary or "The agent reported an error.", summary


def finish_turn(
    session: Session, turn: ChatTurn, runner: AgentRunner, result: dict[str, Any] | None,
    failure: AgentInvokeError | None, duration_ms: int, now: datetime,
) -> dict[str, Any]:
    """Settle the run, record the integration call and, when the agent answered, store both messages.

    A failed turn stores no message and raises `502 UPSTREAM_ERROR` (after the failure itself was committed).
    """
    reply, status, reason, error, summary = _outcome(result, failure)
    run = session.get(AgentRun, turn.run_id)
    if run is not None:  # Reset demo data may have removed the run while the agent worked
        run.status, run.finished_at, run.summary, run.error, run.result = status, now, summary, error, result
    if runner.integration is not None and (failure is None or failure.called):
        record_integration_call(session, runner.integration, "OK" if status == "OK" else "FAILED", duration_ms, reason, now)
        if reply is not None:  # a reply text was written by the model; no reply says nothing about it
            record_model_call(session, answered=True, duration_ms=duration_ms, now=now)
    if reply is not None:
        session.add(ChatMessage(case_id=turn.case_id, chat_session_id=turn.chat_session_id, username=turn.username,
                                role="user", content=turn.message, citations=[], created_at=turn.asked_at))
        session.add(ChatMessage(case_id=turn.case_id, chat_session_id=turn.chat_session_id, username=turn.username,
                                role="assistant", content=reply["answer"], citations=reply["citations"], created_at=now))
        session.commit()
        return reply | {"chat_session_id": turn.chat_session_id}
    if turn.case_id is not None:
        add_trace(session, run_id=turn.run_id, case_id=turn.case_id, task=CHAT_TASK, step="agent_run", tool=None,
                  input_summary=None, output_summary=error, decision=None, duration_ms=duration_ms, status=status, now=now)
    session.commit()
    raise ApiError(502, "UPSTREAM_ERROR", "The agent could not answer. Please try again.",
                   {"run_id": turn.run_id, "reason": reason})
