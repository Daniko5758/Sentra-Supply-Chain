"""EP-18 chat (FR-16): the question goes to the agent synchronously (up to 150 s); the conversation is stored."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator

from sentra_api.agent_runner import callback_base_url
from sentra_api.auth import CurrentUser, require_user
from sentra_api.db import utc_now
from sentra_api.errors import ApiError
from sentra_api.services import chat_service

router = APIRouter(prefix="/api", tags=["chat"])

MAX_MESSAGE_CHARS = 4000


class ChatIn(BaseModel):
    case_id: str | None = None
    message: str
    chat_session_id: str | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message must have 1 to {MAX_MESSAGE_CHARS} characters")
        return value


class CitationOut(BaseModel):
    type: Literal["record", "url"]
    id: str


class ChatOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    chat_session_id: str
    validated: bool


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """A plain `def`: FastAPI runs it in a worker thread, so the blocking agent call does not stall the server."""
    state = request.app.state
    runner = state.agent_runner
    if runner is None:
        raise ApiError(502, "UPSTREAM_ERROR", "The agent is not available.", {"reason": "AGENT_RUNNER_OFF"})
    with state.database.session_factory() as session:
        turn = chat_service.prepare_turn(
            session, username=user.username, case_id=body.case_id, message=body.message,
            chat_session_id=body.chat_session_id, app_mode=state.app_mode,
            callback_base_url=callback_base_url(state.settings), now=utc_now(),
        )
        session.commit()
    result, failure, duration_ms = chat_service.invoke_agent(runner, turn)
    with state.database.session_factory() as session:
        return chat_service.finish_turn(session, turn, runner, result, failure, duration_ms, utc_now())
