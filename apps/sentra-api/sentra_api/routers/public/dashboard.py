"""EP-28 dashboard."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import CurrentUser, require_user
from sentra_api.deps import get_session
from sentra_api.routers.public.signals import SignalOut, signal_out
from sentra_api.services import dashboard
from sentra_api.services.erp_data import load_erp_data

router = APIRouter(prefix="/api", tags=["dashboard"])


class DashboardKpi(BaseModel):
    open_cases: int
    high_alerts: int
    revenue_at_risk_open_cases: int


class DashboardSchedule(BaseModel):
    slipped: int
    on_time: int
    orders_short: int


class DashboardOut(BaseModel):
    kpi: DashboardKpi
    schedule: DashboardSchedule
    latest_signals: list[SignalOut]
    unread_alerts: int


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    request: Request, user: CurrentUser = Depends(require_user), session: Session = Depends(get_session)
) -> DashboardOut:
    erp = await load_erp_data(request.app)
    return DashboardOut(
        kpi=DashboardKpi(**dashboard.open_case_kpi(session)),
        schedule=DashboardSchedule(**dashboard.schedule_counters(session, erp, request.app.state.d0_date)),
        latest_signals=[signal_out(s) for s in dashboard.latest_signals(session)],
        unread_alerts=dashboard.unread_alert_count(session, user.username),
    )
