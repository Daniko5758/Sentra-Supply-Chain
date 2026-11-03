"""EP-04 schedule (Schedule Agent, FR-02): PO lines vs production orders, the numbers computed from the ERP (R-19)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.services import schedule_service, trace_service
from sentra_api.services.erp_data import load_erp_data

router = APIRouter(prefix="/api", tags=["schedule"], dependencies=[Depends(require_user)])


class AgentSummary(BaseModel):
    text: str
    generated_at: str
    source: str


class ScheduleTotals(BaseModel):
    po_lines: int
    slipped: int
    on_time: int
    orders_covered: int
    orders_short: int


class PoLineOut(BaseModel):
    po_id: str
    item_no: int
    material_id: str
    material_name: str
    supplier_id: str
    supplier_name: str
    qty: int
    requested_date: date
    confirmed_date: date
    slip_days: int
    status: Literal["Slipped", "On time"]


class ProductionOrderOut(BaseModel):
    order_id: str
    product_id: str
    qty: int
    start_date: date
    sales_order_id: str | None
    coverage: Literal["Covered", "Short"]
    short_parts: list[str]


class ScheduleOut(BaseModel):
    d0_date: date
    agent_summary: AgentSummary | None
    totals: ScheduleTotals
    po_lines: list[PoLineOut]
    production_orders: list[ProductionOrderOut]


@router.get("/schedule", response_model=ScheduleOut)
async def schedule(request: Request, session: Session = Depends(get_session)) -> ScheduleOut:
    d0 = request.app.state.d0_date
    erp = await load_erp_data(request.app)
    return ScheduleOut(
        d0_date=d0, agent_summary=trace_service.schedule_summary(session),
        production_orders=schedule_service.production_orders(erp, d0), **schedule_service.po_schedule(session, erp, d0),
    )
