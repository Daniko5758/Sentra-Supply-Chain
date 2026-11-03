"""EP-10 alert feed. `read` is per user (DATA_MODEL: `alert.read_by`)."""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.auth import CurrentUser, require_user
from sentra_api.deps import get_session
from sentra_api.errors import ApiError
from sentra_api.models import Alert
from sentra_api.timeutil import iso

router = APIRouter(prefix="/api", tags=["alerts"])


class AlertOut(BaseModel):
    alert_id: str
    case_id: str
    level: str
    title: str
    location: str | None
    score: float
    units_at_risk: int
    revenue_at_risk: int
    mode: str
    simulated: bool
    created_at: str
    read: bool


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(user: CurrentUser = Depends(require_user), session: Session = Depends(get_session)) -> list[AlertOut]:
    rows = session.scalars(select(Alert).order_by(Alert.created_at.desc(), Alert.alert_id.desc()))
    return [
        AlertOut(
            alert_id=a.alert_id, case_id=a.case_id, level=a.level, title=a.title, location=a.location, score=a.score,
            units_at_risk=a.units_at_risk, revenue_at_risk=a.revenue_at_risk, mode=a.mode, simulated=a.mode == "DEMO",
            created_at=iso(a.created_at), read=user.username in a.read_by,
        )
        for a in rows
    ]


@router.post("/alerts/{alert_id}/read", status_code=204)
def mark_alert_read(alert_id: str, user: CurrentUser = Depends(require_user), session: Session = Depends(get_session)) -> Response:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise ApiError(404, "NOT_FOUND", f"Alert {alert_id} was not found.")
    if user.username not in alert.read_by:
        alert.read_by = [*alert.read_by, user.username]  # reassign: in-place changes to a JSON column are not tracked
        session.commit()
    return Response(status_code=204)
