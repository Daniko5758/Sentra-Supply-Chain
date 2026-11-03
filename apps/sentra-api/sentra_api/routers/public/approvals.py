"""EP-21..EP-23 (F-12): submit an option, approvals, execution in the mock S/4, ERP log. All answer `404 FEATURE_DISABLED` while
FEATURE_ERP_EXECUTION is off (contract A.5); approve, reject, retry and approve-all are for the SCM Manager only (UT-15)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from sentra_api.auth import CurrentUser, Role, require_role, require_user
from sentra_api.db import utc_now
from sentra_api.flags import require_flag
from sentra_api.services import executor

router = APIRouter(
    prefix="/api", tags=["approvals [F-12]"], dependencies=[Depends(require_flag("FEATURE_ERP_EXECUTION")), Depends(require_user)]
)
manager_only = [Depends(require_role(Role.SCM_MANAGER))]

ActionType = Literal["CHANGE_PO_DATE", "CREATE_PR_PO", "REDUCE_OR_CANCEL_PO", "RESCHEDULE_PRODUCTION_ORDER",
                     "RESCHEDULE_SALES_ORDER", "EXPEDITE_SHIPMENT"]
ActionStatus = Literal["PROPOSED", "APPROVED", "EXECUTED", "FAILED", "REJECTED"]


class ErpMessageOut(BaseModel):
    """A message of the ERP in the shape of BAPIRET2 (EP-22 v1.12): `type` S/W/E/I, `id` the message class, `number` 3 digits."""

    type: Literal["S", "W", "E", "I"]
    id: str
    number: str
    message: str


class ErpResultOut(BaseModel):
    """`erp_result` (EP-22 v1.12). A `FAILED` action has `document` null and the messages of the refusal; an action stored
    before v1.12 has no messages (`[]`)."""

    document: str | None
    messages: list[ErpMessageOut]


class ActionOut(BaseModel):
    """Schema G.3 (`action_id`, `seq`, `type`, `object`, `payload`, `status`, `idempotency_key`) with the decision and the ERP answer."""

    action_id: str
    case_id: str
    option: str
    seq: int
    type: ActionType
    object: str
    payload: dict[str, Any]
    status: ActionStatus
    idempotency_key: str
    proposed_by: str
    created_at: str | None  # when the option was submitted
    decided_by: str | None
    decided_at: str | None
    executed_at: str | None
    erp_result: ErpResultOut | None  # {"document": "PR 0010000001 / PO 4500001019", "messages": [...]}; FAILED: document null
    error: str | None  # why a FAILED action failed


class SubmitOut(BaseModel):
    case_id: str
    option: str
    status: str
    actions: list[ActionOut]


class ApprovalOut(BaseModel):
    """EP-22 list: the fields of the contract plus the answer of the ERP for a `FAILED` action."""

    action_id: str
    case_id: str
    case_title: str
    option: str
    seq: int
    type: ActionType
    object: str
    payload: dict[str, Any]
    status: ActionStatus
    proposed_by: str
    created_at: str | None
    erp_result: ErpResultOut | None
    error: str | None


class RejectIn(BaseModel):
    comment: str


class ChangeDocumentOut(BaseModel):
    """E.2 E-17 (`changed_at` in +07:00). v1.12 (DEC-049): the columns of CDHDR/CDPOS; the rows of one write share `change_number`."""

    id: int
    object_type: str
    object_id: str
    field: str
    old_value: str | None
    new_value: str | None
    changed_by: str
    changed_at: str
    source: str
    change_number: str
    object_class: str
    object_key: str
    table_name: str
    table_key: str
    field_name: str
    field_label: str
    change_ind: str
    tcode: str
    username: str


@router.post("/cases/{case_id}/recommendations/{option}/submit", response_model=SubmitOut, summary="EP-21 Submit an option for approval")
async def submit_option(case_id: str, option: str, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        result = await executor.submit_option(request.app, session, case_id, option, username=user.username, now=utc_now())
        session.commit()
        return result


@router.get("/approvals", response_model=list[ApprovalOut], summary="EP-22 Actions waiting for approval")
def approvals(request: Request) -> list[dict[str, Any]]:
    with request.app.state.database.session_factory() as session:
        return executor.list_approvals(session)


@router.post("/actions/{action_id}/approve", response_model=ActionOut, dependencies=manager_only,
             summary="EP-22 Approve an action (it is executed at once)")
async def approve(action_id: str, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        return await executor.approve_action(request.app, session, action_id, username=user.username, now=utc_now())


@router.post("/actions/{action_id}/reject", response_model=ActionOut, dependencies=manager_only, summary="EP-22 Reject an action")
def reject(action_id: str, body: RejectIn, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        return executor.reject_action(request.app, session, action_id, body.comment, username=user.username, now=utc_now())


@router.post("/actions/{action_id}/retry", response_model=ActionOut, dependencies=manager_only, summary="EP-22 Retry a failed action")
async def retry(action_id: str, request: Request, user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    with request.app.state.database.session_factory() as session:
        return await executor.retry_action(request.app, session, action_id, username=user.username, now=utc_now())


@router.post("/cases/{case_id}/options/{option}/approve-all", response_model=list[ActionOut], dependencies=manager_only,
             summary="EP-22 Approve every action of an option")
async def approve_all(case_id: str, option: str, request: Request, user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    with request.app.state.database.session_factory() as session:
        return await executor.approve_all(request.app, session, case_id, option, username=user.username, now=utc_now())


@router.get("/erp-log", response_model=list[ChangeDocumentOut], summary="EP-23 ERP change log (SIMULATED)")
async def erp_log(request: Request) -> list[dict[str, Any]]:
    return await executor.erp_log(request.app)
