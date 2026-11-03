"""Persist case status changes. The rules themselves live in sentra_api.case_state (pure)."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import Session

from sentra_api.case_state import CaseEvent, CaseStatus, InvalidTransition, next_status, settle_approval
from sentra_api.db import utc_now
from sentra_api.errors import ApiError
from sentra_api.models import Case


def _load(session: Session, case_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} was not found.")
    return case


def _set_status(case: Case, status: CaseStatus, now: datetime | None) -> CaseStatus:
    case.status = status.value
    case.updated_at = now or utc_now()
    return status


def apply_event(session: Session, case_id: str, event: CaseEvent, *, now: datetime | None = None) -> CaseStatus:
    """Move a case along PRD 8.3. Illegal moves are 409 CONFLICT and change nothing. The caller commits."""
    case = _load(session, case_id)
    try:
        status = next_status(CaseStatus(case.status), event)
    except InvalidTransition as exc:
        raise ApiError(
            409, "CONFLICT", "The current case status does not allow this action.",
            {"status": exc.status.value, "event": exc.event.value},
        ) from exc
    return _set_status(case, status, now)


def settle_actions(
    session: Session, case_id: str, action_statuses: Iterable[str], *, now: datetime | None = None
) -> CaseStatus:
    """After an F-12 action changed: AWAITING_APPROVAL -> EXECUTED / READY_FOR_DECISION / unchanged.

    Cases in any other status are returned unchanged, so a late action update cannot skip the approval step.
    """
    case = _load(session, case_id)
    current = CaseStatus(case.status)
    if current is not CaseStatus.AWAITING_APPROVAL:
        return current
    settled = settle_approval(action_statuses)
    return current if settled is current else _set_status(case, settled, now)
