"""EP-19: the SCM Manager closes or dismisses a case (PRD 8.3). The comment is kept as a step of the case trace."""

from datetime import datetime

from sqlalchemy.orm import Session

from sentra_api.case_state import CaseEvent
from sentra_api.errors import ApiError
from sentra_api.models import Case
from sentra_api.services.case_service import apply_event
from sentra_api.services.case_tools import add_trace


def close_or_dismiss(
    session: Session, case_id: str, event: CaseEvent, *, comment: str | None, username: str, now: datetime
) -> Case:
    """`CLOSE` (comment optional) or `DISMISS` (comment required). 404 for an unknown case, 409 when the status does not allow it."""
    if event is CaseEvent.DISMISS and not (comment and comment.strip()):
        raise ApiError(400, "VALIDATION_FAILED", "A comment is required to dismiss a case.", {"field": "comment"})
    status = apply_event(session, case_id, event, now=now)
    add_trace(
        session, run_id=None, case_id=case_id, task=None, step=f"{event.value.lower()}_case", tool=None,
        input_summary=f"by {username}", output_summary=f"status={status.value}",
        decision=comment.strip() if comment and comment.strip() else None, duration_ms=None, status="OK", now=now,
    )
    return session.get(Case, case_id)
