"""The row of a case in lists (EP-11, EP-19, EP-28): case fields plus the totals of its latest impact snapshot."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.models import Case, ImpactSnapshot
from sentra_api.timeutil import iso


class CaseListItem(BaseModel):
    case_id: str
    title: str
    risk_type: str
    scope: str
    location: str | None
    country: str
    mode: str
    simulated: bool
    status: str
    score: float | None
    level: str | None
    units_at_risk: int
    revenue_at_risk: int
    updated_at: str


def latest_totals(session: Session, case_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`totals` of the newest snapshot of each case that has one."""
    latest: dict[str, ImpactSnapshot] = {}
    for snapshot in session.scalars(select(ImpactSnapshot).where(ImpactSnapshot.case_id.in_(case_ids)).order_by(ImpactSnapshot.version)):
        latest[snapshot.case_id] = snapshot
    return {case_id: snapshot.payload["totals"] for case_id, snapshot in latest.items()}


def case_list_items(session: Session, cases: list[Case]) -> list[CaseListItem]:
    totals = latest_totals(session, [c.case_id for c in cases])
    return [
        CaseListItem(
            case_id=c.case_id, title=c.title, risk_type=c.risk_type, scope=c.scope, location=c.location,
            country=c.country, mode=c.mode, simulated=c.mode == "DEMO", status=c.status, score=c.score, level=c.level,
            units_at_risk=totals.get(c.case_id, {}).get("units_blocked", 0),
            revenue_at_risk=totals.get(c.case_id, {}).get("rar", 0), updated_at=iso(c.updated_at),
        )
        for c in cases
    ]
