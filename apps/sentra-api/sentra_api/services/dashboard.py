"""EP-28 dashboard numbers: KPI of the open cases, the schedule counters, the latest signals and the unread alerts."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.case_state import CLOSED_STATUSES
from sentra_api.erp import ErpData
from sentra_api.models import Alert, Case, Signal
from sentra_api.services.case_summary import latest_totals
from sentra_api.services.schedule_service import po_schedule

LATEST_SIGNALS = 5


def open_case_kpi(session: Session) -> dict[str, int]:
    """`open_cases`, `high_alerts` (HIGH alerts of the open cases) and the summed RAR of the open cases."""
    open_ids = list(session.scalars(select(Case.case_id).where(Case.status.not_in(CLOSED_STATUSES))))
    high = session.scalars(select(Alert.case_id).where(Alert.case_id.in_(open_ids), Alert.level == "HIGH")).all()
    return {
        "open_cases": len(open_ids), "high_alerts": len(high),
        "revenue_at_risk_open_cases": sum(totals["rar"] for totals in latest_totals(session, open_ids).values()),
    }


def schedule_counters(session: Session, erp: ErpData, d0: date) -> dict[str, int]:
    totals = po_schedule(session, erp, d0)["totals"]
    return {"slipped": totals["slipped"], "on_time": totals["on_time"], "orders_short": totals["orders_short"]}


def latest_signals(session: Session) -> list[Signal]:
    return list(session.scalars(select(Signal).order_by(Signal.fetched_at.desc(), Signal.id.desc()).limit(LATEST_SIGNALS)))


def unread_alert_count(session: Session, username: str) -> int:
    return sum(1 for read_by in session.scalars(select(Alert.read_by)) if username not in read_by)
