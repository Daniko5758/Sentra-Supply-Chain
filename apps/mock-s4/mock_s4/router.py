from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel

from mock_s4.read_api import router as read_router
from mock_s4.seed import load_mock_seed
from mock_s4.seed_data import jakarta_today, seed_meta


router = APIRouter()
router.include_router(read_router)


class SeedInfo(BaseModel):
    file: str
    version: str
    dataset: str


class MockResetResponse(BaseModel):
    status: str
    d0_date: date
    seed: SeedInfo


@router.post("/admin/reset", response_model=MockResetResponse, tags=["admin"])
def reset(request: Request) -> MockResetResponse:
    d0_date = getattr(request.app.state, "seed_d0_date", None) or jakarta_today()
    request.app.state.seed_bundle = load_mock_seed(d0_date, request.app.state.database)
    request.app.state.d0_date = d0_date
    return MockResetResponse(status="OK", d0_date=d0_date, seed=SeedInfo(**seed_meta(request.app.state.seed_bundle)))
