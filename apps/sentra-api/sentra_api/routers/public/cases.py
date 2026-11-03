"""EP-11 case list, EP-12 case detail (the parts that need no agent) and EP-14 supplier messages."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.case_state import CLOSED_STATUSES
from sentra_api.deps import get_session
from sentra_api.models import Case
from sentra_api.services import case_tools
from sentra_api.services.case_detail import CaseDetail
from sentra_api.services.case_summary import CaseListItem, case_list_items
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.recommendation_service import detail_with_escalation

router = APIRouter(prefix="/api", tags=["cases"], dependencies=[Depends(require_user)])


@router.get("/cases", response_model=list[CaseListItem])
def list_cases(
    status: Literal["open", "all"] = Query("open"), session: Session = Depends(get_session)
) -> list[CaseListItem]:
    query = select(Case).order_by(Case.created_at.desc(), Case.case_id.desc())
    if status == "open":
        query = query.where(Case.status.not_in(CLOSED_STATUSES))
    return case_list_items(session, list(session.scalars(query)))


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def case_detail(
    case_id: str, request: Request, version: int | None = Query(None, ge=1), session: Session = Depends(get_session)
) -> CaseDetail:
    state = request.app.state
    return detail_with_escalation(session, await load_erp_data(request.app), case_id, version, state.d0_date, state.settings)


class SupplierMessageOut(BaseModel):
    """EP-14 (also `messages` of TL-12)."""

    message_id: str
    case_id: str
    site_id: str
    direction: Literal["OUT", "IN"]
    body: str
    structured: dict[str, Any] | None
    created_at: str
    simulated: bool
    request_id: str | None  # v1.11 (DEC-038): null for a message made before supplier requests
    request_type: Literal["IMPACT", "BACKUP_ACTIVATION", "PULL_IN"]


@router.get("/cases/{case_id}/messages", response_model=list[SupplierMessageOut])
def supplier_messages(case_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return case_tools.supplier_messages(session, case_id, None)["messages"]
