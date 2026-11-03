"""EP-19 close / dismiss a case (SCM Manager)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import CurrentUser, Role, require_role
from sentra_api.case_state import CaseEvent
from sentra_api.db import utc_now
from sentra_api.deps import get_session
from sentra_api.services.case_actions import close_or_dismiss
from sentra_api.services.case_summary import CaseListItem, case_list_items

router = APIRouter(prefix="/api/cases", tags=["cases"])


class CommentIn(BaseModel):
    comment: str | None = None


def _decide(session: Session, case_id: str, event: CaseEvent, body: CommentIn | None, user: CurrentUser) -> CaseListItem:
    case = close_or_dismiss(session, case_id, event, comment=body.comment if body else None, username=user.username, now=utc_now())
    session.commit()
    return case_list_items(session, [case])[0]


@router.post("/{case_id}/close", response_model=CaseListItem)
def close_case(
    case_id: str, body: CommentIn | None = None, user: CurrentUser = Depends(require_role(Role.SCM_MANAGER)),
    session: Session = Depends(get_session),
) -> CaseListItem:
    """Comment optional. Only from READY_FOR_DECISION or EXECUTED; otherwise 409."""
    return _decide(session, case_id, CaseEvent.CLOSE, body, user)


@router.post("/{case_id}/dismiss", response_model=CaseListItem)
def dismiss_case(
    case_id: str, body: CommentIn | None = None, user: CurrentUser = Depends(require_role(Role.SCM_MANAGER)),
    session: Session = Depends(get_session),
) -> CaseListItem:
    """Comment required. From any status except CLOSED and DISMISSED."""
    return _decide(session, case_id, CaseEvent.DISMISS, body, user)
