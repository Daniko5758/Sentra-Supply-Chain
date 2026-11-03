"""EP-17 report of a case (FR-15): the latest saved report, and `Generate report` (queues a `generate_report` run)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.db import utc_now
from sentra_api.deps import get_session
from sentra_api.errors import ApiError
from sentra_api.routers.public.recommendations import RecommendationOut
from sentra_api.services import case_tools, report_service
from sentra_api.services.case_detail import CaseSignalOut, KpiOut
from sentra_api.services.meeting_records import EscalationOut

router = APIRouter(prefix="/api", tags=["reports"], dependencies=[Depends(require_user)])


class ReportReplyOut(BaseModel):
    body: str
    created_at: str
    simulated: bool


class ReportVerificationOut(BaseModel):
    """One site of the case: the answer state, the confidence scores (G.6) and the latest reply of the supplier."""

    site_id: str
    supplier_name: str
    tier: str
    verification_status: str
    delay_override_days: int | None
    excluded: bool
    k_a: float | None
    k_b: float | None
    rpt_status: str
    reply: ReportReplyOut | None


class ReportScheduleOut(BaseModel):
    """`totals` of EP-04."""

    po_lines: int
    slipped: int
    on_time: int
    orders_covered: int
    orders_short: int


class ReportActionOut(BaseModel):
    """An action of the case (F-12); the list is empty while the options are only proposals."""

    action_id: str
    option: str
    type: str
    status: str
    payload: dict[str, Any]
    erp_result: dict[str, Any] | None
    error: str | None


class ReportDataOut(BaseModel):
    kpi: KpiOut
    signal: CaseSignalOut
    impact: dict[str, Any]
    verification: list[ReportVerificationOut]
    schedule: ReportScheduleOut
    recommendation: RecommendationOut
    escalation: EscalationOut  # v1.11 (DEC-039): G.7 with the meeting and the decision note
    actions: list[ReportActionOut]


class RarBySitePoint(BaseModel):
    site_id: str
    rar: int


class DelayByPartPoint(BaseModel):
    part_id: str
    days: int


class UnitsByOrderPoint(BaseModel):
    order_id: str
    units: int


class RarByVersionPoint(BaseModel):
    version: int
    rar: int


class ReportChartsOut(BaseModel):
    rar_by_site: list[RarBySitePoint]
    delay_by_part: list[DelayByPartPoint]
    units_by_order: list[UnitsByOrderPoint]
    rar_by_version: list[RarByVersionPoint]


class ReportOut(BaseModel):
    case_id: str
    version: int
    generated_at: str
    simulated: bool
    file_title: str
    narrative_md: str
    narrative_source: Literal["llm", "template"]
    sections: list[str]
    data: ReportDataOut
    charts: ReportChartsOut


class ReportQueued(BaseModel):
    run_id: str
    status: str


@router.get("/cases/{case_id}/report", response_model=ReportOut)
def get_report(case_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    case = case_tools.load_case(session, case_id)
    report = report_service.latest_report(session, case_id)
    if report is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} has no report yet.", {"case_id": case_id})
    return report_service.public_view(case, report)


@router.post("/cases/{case_id}/report/generate", status_code=202, response_model=ReportQueued)
def generate_report(case_id: str, request: Request, session: Session = Depends(get_session)) -> ReportQueued:
    run = report_service.request_generation(session, case_id, utc_now())
    queued = ReportQueued(run_id=run.run_id, status=run.status)  # before the worker can claim it
    session.commit()
    request.app.state.run_queue.wake()
    return queued
