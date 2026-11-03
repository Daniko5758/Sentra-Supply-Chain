"""EP-15 recommendations of a case (FR-13)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from sentra_api.auth import require_user
from sentra_api.flags import flag_enabled
from sentra_api.services import case_tools, recommendation_service
from sentra_api.services.erp_data import load_erp_data

router = APIRouter(prefix="/api", tags=["recommendations"], dependencies=[Depends(require_user)])


class MetricsOut(BaseModel):
    """API_CONTRACT G.2."""

    units_blocked: int
    output_value: int
    po_value: int
    rar: int
    so_delayed: int
    premium: int
    extra_duty: int | None


class ActionOut(BaseModel):
    seq: int
    type: Literal["CHANGE_PO_DATE", "CREATE_PR_PO", "REDUCE_OR_CANCEL_PO", "RESCHEDULE_PRODUCTION_ORDER",
                  "RESCHEDULE_SALES_ORDER", "EXPEDITE_SHIPMENT"]
    object: str
    payload: dict[str, Any]


class ConfirmationSiteOut(BaseModel):
    site_id: str
    status: Literal["REQUESTED", "ANSWERED", "NO_RESPONSE"] | None  # null = not requested yet
    answer: str | None


class ConfirmationOut(BaseModel):
    request_type: Literal["BACKUP_ACTIVATION", "PULL_IN"]
    sites: list[ConfirmationSiteOut]


class OptionOut(BaseModel):
    option: Literal["A", "B", "C", "D", "E", "R", "S"]
    title: str
    available: bool
    unavailable_reason: str | None
    label: str | None
    pending_confirmation: bool
    confirmation: ConfirmationOut | None
    saving: int | None
    before: MetricsOut
    after: MetricsOut | None
    actions: list[ActionOut]
    rationale: str | None
    rationale_source: Literal["llm", "template"] | None


class ConfirmationNeededOut(BaseModel):
    site_id: str
    request_type: Literal["BACKUP_ACTIVATION", "PULL_IN"]


class RecommendationOut(BaseModel):
    case_id: str
    version: int
    preliminary: bool
    recommended_option: Literal["A", "B", "C", "D", "E", "R", "S"] | None
    rule_reason: str
    execution_enabled: bool
    options: list[OptionOut]
    non_erp_steps: list[str]
    confirmations_needed: list[ConfirmationNeededOut]
    escalation_tier: Literal[0, 1, 2]
    executive_decision_required: bool
    decision: dict[str, Any] | None  # G.9


@router.get("/cases/{case_id}/recommendations", response_model=RecommendationOut)
async def case_recommendations(case_id: str, request: Request) -> dict[str, Any]:
    state = request.app.state
    with state.database.session_factory() as session:
        case_tools.load_case(session, case_id)  # 404 before the ERP is read
        built = recommendation_service.build_recommendation(session, await load_erp_data(request.app), case_id, state.d0_date)
        return recommendation_service.public_view(
            session, built, execution_enabled=flag_enabled(state.settings, "FEATURE_ERP_EXECUTION")
        )
