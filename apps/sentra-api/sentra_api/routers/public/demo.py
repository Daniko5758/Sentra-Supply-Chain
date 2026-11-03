"""EP-24 demo controls. Run in DEMO and LIVE mode alike (DEC-024): the application mode is not consulted here."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from mock_s4.seed_data import jakarta_today
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.orm import Session

from sentra_api.auth import Role, require_role, require_user
from sentra_api.db import utc_now
from sentra_api.errors import ApiError
from sentra_api.heatmap import country_name
from sentra_api.routers.public.auth import SeedInfo
from sentra_api.seed import reset_demo_data
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.runs import enqueue_run
from sentra_api.services.signal_service import ingest_signals
from sentra_api.signals import DEMO_ENGINE

router = APIRouter(prefix="/api/demo", tags=["demo"])
SCM_ONLY = [Depends(require_role(Role.SCM_MANAGER))]  # EP-24; EP-31 is open to every signed-in role

CUSTOM = "custom"


class ResetResponse(BaseModel):
    status: Literal["OK"]
    d0_date: date


class CustomSignal(BaseModel):
    """Free-form signal (EP-24). Enum and range rules beyond the types are applied by TL-05 validation."""

    risk_type: Literal["FLOOD", "LANDSLIDE", "FIRE", "EARTHQUAKE", "LOGISTICS", "TARIFF", "POLITICAL"]
    scope: Literal["LOCATION", "COUNTRY"]
    country: str = Field(min_length=2, max_length=2)
    kabupaten: str | None = None
    kecamatan: list[str] = []
    severity: int | None = None
    downtime_days: StrictInt | None = None  # v1.11: >= 1 is checked by TL-05 validation (400 VALIDATION_FAILED)
    effective_date: date
    tariff_pp: float | None = None
    category: str | None = None
    summary: str = Field(min_length=1)
    source_urls: list[str] = []
    source_label: str | None = None


class InjectRequest(BaseModel):
    scenario: str
    signal: CustomSignal | None = None


class InjectResponse(BaseModel):
    signal_id: str
    case_id: str | None
    run_id: str | None
    status: str


class ScenarioOut(BaseModel):
    key: str
    risk_type: str
    scope: str
    country: str
    location: str | None
    downtime_days: int | None
    summary: str


class ScenariosResponse(BaseModel):
    seed: SeedInfo
    scenarios: list[ScenarioOut]


@router.get("/scenarios", response_model=ScenariosResponse, dependencies=[Depends(require_user)])
def list_scenarios(request: Request) -> ScenariosResponse:
    """EP-31: the `demo_events` of the active seed in file order; the UI builds its demo controls from this list."""
    state = request.app.state
    scenarios = []
    for key, event in state.seed_data["sentra"]["demo_events"].items():
        location = event.get("kabupaten") if event["scope"] == "LOCATION" else country_name(event["country"])
        scenarios.append(ScenarioOut(
            key=key, risk_type=event["risk_type"], scope=event["scope"], country=event["country"], location=location,
            downtime_days=event.get("downtime_days"), summary=event["summary"],
        ))
    return ScenariosResponse(seed=SeedInfo(**state.seed_meta), scenarios=scenarios)


@router.post("/reset", response_model=ResetResponse, dependencies=SCM_ONLY)
async def reset(request: Request) -> dict[str, str]:
    state = request.app.state
    return await reset_demo_data(
        d0_date=jakarta_today(), database=state.database, application=request.app, settings=state.settings
    )


def _scenario_signal(request: Request, body: InjectRequest) -> dict[str, Any]:
    if body.scenario == CUSTOM:
        if body.signal is None:
            raise ApiError(400, "VALIDATION_FAILED", "scenario custom needs a signal.", {"field": "signal"})
        raw = body.signal.model_dump(mode="json")
    else:
        events = request.app.state.seed_data["sentra"]["demo_events"]
        if body.scenario not in events:
            raise ApiError(400, "VALIDATION_FAILED", "Unknown scenario.", {"field": "scenario", "allowed": [*events, CUSTOM]})
        raw = dict(events[body.scenario])
    raw["engine"] = DEMO_ENGINE  # injected signals are always DEMO / simulated, whatever the application mode
    return raw


@router.post("/inject", status_code=202, response_model=InjectResponse, dependencies=SCM_ONLY)
async def inject(body: InjectRequest, request: Request) -> InjectResponse:
    raw = _scenario_signal(request, body)
    erp = await load_erp_data(request.app)
    now = utc_now()
    with Session(request.app.state.database.engine) as session, session.begin():
        pool = request.app.state.session_pool
        result = ingest_signals(session, erp, [raw], d0=request.app.state.d0_date, now=now,
                                new_session_id=lambda: pool.take("new case"))
        if result.dropped:
            dropped = result.dropped[0]
            raise ApiError(400, "VALIDATION_FAILED", f"The signal is not valid: {dropped.detail or dropped.reason}.",
                           {"reason": dropped.reason})
        saved = result.saved[0]
        if saved.case_id is None:
            return InjectResponse(signal_id=saved.signal_id, case_id=None, run_id=None, status=saved.match)
        run = enqueue_run(session, task="analyze_case", trigger="manual", case_id=saved.case_id, now=now)
        response = InjectResponse(signal_id=saved.signal_id, case_id=saved.case_id, run_id=run.run_id, status=run.status)
    request.app.state.run_queue.wake()  # after the commit, so the worker sees the run
    return response
