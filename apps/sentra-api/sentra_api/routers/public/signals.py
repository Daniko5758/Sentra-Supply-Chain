"""EP-07 risk signals."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.models import Signal
from sentra_api.timeutil import iso

router = APIRouter(prefix="/api", tags=["signals"], dependencies=[Depends(require_user)])


class SignalOut(BaseModel):
    """API_CONTRACT G.4."""

    signal_id: str
    mode: str
    engine: str
    simulated: bool
    verification: str | None
    risk_type: str
    scope: str
    country: str
    kabupaten: str | None
    kecamatan: list[str]
    severity: int | None
    downtime_days: int | None
    effective_date: date
    tariff_pp: float | None
    category: str | None
    summary: str
    source_urls: list[str]
    source_label: str | None
    evidence: list[dict[str, Any]]
    fetched_at: str
    match: str
    case_id: str | None


def signal_out(signal: Signal) -> SignalOut:
    return SignalOut(
        signal_id=signal.id, mode=signal.mode, engine=signal.engine, simulated=signal.simulated,
        verification=signal.verification, risk_type=signal.risk_type, scope=signal.scope, country=signal.country,
        kabupaten=signal.kabupaten, kecamatan=signal.kecamatan, severity=signal.severity,
        downtime_days=signal.downtime_days, effective_date=signal.effective_date, tariff_pp=signal.tariff_pp,
        category=signal.category,
        summary=signal.summary, source_urls=signal.source_urls, source_label=signal.source_label,
        evidence=signal.evidence, fetched_at=iso(signal.fetched_at), match=signal.match, case_id=signal.case_id,
    )


@router.get("/signals", response_model=list[SignalOut])
def list_signals(limit: int = Query(50, ge=1, le=1000), session: Session = Depends(get_session)) -> list[SignalOut]:
    rows = session.scalars(select(Signal).order_by(Signal.fetched_at.desc(), Signal.id.desc()).limit(limit))
    return [signal_out(row) for row in rows]
