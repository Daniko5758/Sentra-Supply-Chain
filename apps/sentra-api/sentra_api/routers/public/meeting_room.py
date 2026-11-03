"""EP-20 Meeting Room of a case and EP-30 decision note of an Executive Meeting Room (F-07, DEC-039).

Both answer `404 FEATURE_DISABLED` while FEATURE_MEETING_ROOM is off (contract A.5). The meeting itself is made by TL-13 (the agent
asks after each calculation), not by these endpoints.

Custom ERP actions (Option X): the meeting decides on its own ERP actions; they are checked with R-17, saved with the decision note
and executed at once. These two routes also need FEATURE_ERP_EXECUTION.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from sentra_api.auth import CurrentUser, Role, require_role, require_user
from sentra_api.db import utc_now
from sentra_api.flags import require_flag
from sentra_api.routers.public.approvals import ActionOut
from sentra_api.services import case_tools, custom_actions, executor, meeting_service
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.meeting_records import DecisionOut, NotificationOut
from sentra_api.services.custom_actions import CustomCatalogOut, CustomDecisionIn
from sentra_api.services.meeting_service import DecisionIn, MeetingRoomOut
from sentra_api.services.recommendation_service import CaseRecommendation, build_recommendation

router = APIRouter(
    prefix="/api", tags=["meeting-room [F-07]"], dependencies=[Depends(require_flag("FEATURE_MEETING_ROOM")), Depends(require_user)]
)
manager_only = [Depends(require_role(Role.SCM_MANAGER))]
erp_execution = [Depends(require_flag("FEATURE_ERP_EXECUTION"))]


class CustomDecisionOut(BaseModel):
    """The decision note (G.9, `option` = `X`; null for a tier-1 Meeting Room, which has no note), the case status after the
    execution, the extra cost and the executed actions."""

    decision: DecisionOut | None
    case_status: str
    premium: int
    actions: list[ActionOut]


async def _built(request: Request, session, case_id: str) -> CaseRecommendation:
    state = request.app.state
    case_tools.load_case(session, case_id)  # 404 before the ERP is read
    return build_recommendation(session, await load_erp_data(request.app), case_id, state.d0_date, settings=state.settings)


@router.get("/cases/{case_id}/meeting-room", response_model=MeetingRoomOut, summary="EP-20 Meeting Room of a case")
async def meeting_room(case_id: str, request: Request) -> MeetingRoomOut:
    with request.app.state.database.session_factory() as session:
        return meeting_service.meeting_room_view(session, await _built(request, session, case_id))


@router.post("/cases/{case_id}/meeting-room/resend", response_model=NotificationOut, dependencies=manager_only,
             summary="EP-20 Resend the e-mail of the latest meeting (only when it failed)")
async def resend(case_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        result = await meeting_service.resend_email(request.app, session, await _built(request, session, case_id), now=utc_now())
        session.commit()
        return result


@router.post("/cases/{case_id}/decision", response_model=DecisionOut, status_code=201, dependencies=manager_only,
             summary="EP-30 Record the decision of an Executive Meeting Room")
async def decision(case_id: str, body: DecisionIn, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        built = await _built(request, session, case_id)
        result = meeting_service.record_decision(session, built, body, username=user.username, now=utc_now())
        session.commit()
        return result


@router.get("/cases/{case_id}/decision/custom-actions/catalog", response_model=CustomCatalogOut, dependencies=erp_execution,
            summary="ERP objects a custom meeting action can change (SIMULATED)")
async def custom_catalog(case_id: str, request: Request) -> CustomCatalogOut:
    with request.app.state.database.session_factory() as session:
        case_tools.load_case(session, case_id)
        erp = await load_erp_data(request.app)
        return custom_actions.catalog(session, erp, executor.alternates_of(session), case_id, request.app.state.d0_date)


@router.post("/cases/{case_id}/decision/custom-actions", response_model=CustomDecisionOut, status_code=201,
             dependencies=[*erp_execution, *manager_only],
             summary="Custom ERP actions of a tier-1 or tier-2 Meeting Room, executed at once (Option X)")
async def custom_decision(case_id: str, body: CustomDecisionIn, request: Request,
                          user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        built = await _built(request, session, case_id)
        return await custom_actions.decide_and_execute(request.app, session, built, body, username=user.username, now=utc_now())
