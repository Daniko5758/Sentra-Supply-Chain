"""EP-16 country heatmap (R-21, FR-28)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.heatmap_service import country_heatmap

router = APIRouter(prefix="/api", tags=["heatmap"], dependencies=[Depends(require_user)])

Level = Literal["HIGH", "MEDIUM", "LOW"]


class CountryCaseOut(BaseModel):
    case_id: str
    title: str
    level: Level
    score: float


class CountrySiteOut(BaseModel):
    site_id: str
    supplier_name: str
    tier: str
    kabupaten: str | None


class CountryOut(BaseModel):
    country: str
    name: str
    sites_t1: int
    sites_t2: int
    open_po_lines: int
    po_exposure: int
    share: float
    exposure_tier: Literal[1, 2, 3, 4]
    highest_level: Level | None
    open_cases: list[CountryCaseOut]
    sites: list[CountrySiteOut]


class HeatmapOut(BaseModel):
    total_po_exposure: int
    simulated_cases_present: bool
    countries: list[CountryOut]


@router.get("/heatmap/countries", response_model=HeatmapOut)
async def heatmap(request: Request, session: Session = Depends(get_session)) -> dict[str, Any]:
    return country_heatmap(session, await load_erp_data(request.app), request.app.state.d0_date)
