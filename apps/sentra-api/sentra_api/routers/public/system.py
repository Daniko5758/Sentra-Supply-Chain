"""EP-26 system status (FR-27, B-19) and EP-29 application mode. The `scheduler` object arrives with B-30."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentra_api.auth import CurrentUser, Role, require_role, require_user
from sentra_api.db import utc_now
from sentra_api.deps import get_session
from sentra_api.flags import feature_flags
from sentra_api.routers.public.auth import FeatureFlagsResponse, SeedInfo
from sentra_api.services.app_mode import change_mode, mode_view
from sentra_api.services.system_status import integration_statuses

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_user)])


class IntegrationOut(BaseModel):
    name: str
    status: Literal["OK", "FAILED", "UNKNOWN"]
    last_call_at: str | None
    duration_ms: int | None
    message: str | None


class SystemStatusOut(BaseModel):
    mode: Literal["LIVE", "DEMO"]
    d0_date: date
    integrations: list[IntegrationOut]
    feature_flags: FeatureFlagsResponse
    scheduler: dict[str, Any] | None  # null until O-03 (B-30)
    seed: SeedInfo


def _status(request: Request, session: Session) -> SystemStatusOut:
    state = request.app.state
    return SystemStatusOut(
        mode=state.app_mode, d0_date=state.d0_date, integrations=integration_statuses(session, meeting_room=state.settings.feature_meeting_room),
        feature_flags=FeatureFlagsResponse(**feature_flags(state.settings)), scheduler=None,
        seed=SeedInfo(**state.seed_meta),
    )


@router.get("/status", response_model=SystemStatusOut)
def system_status(request: Request, session: Session = Depends(get_session)) -> SystemStatusOut:
    return _status(request, session)


@router.post("/checks", response_model=SystemStatusOut)
def run_checks(
    request: Request,
    user: CurrentUser = Depends(require_role(Role.SCM_MANAGER)),
    session: Session = Depends(get_session),
) -> SystemStatusOut:
    """The light checks (RPT-1.6, `ping` of the agent), then the status they leave. A plain `def`, so FastAPI runs it in a worker
    thread; like the chat (EP-18), the user waits for the agent here (up to 150 s)."""
    request.app.state.system_checks.run()
    return _status(request, session)


class ModeOut(BaseModel):
    mode: Literal["LIVE", "DEMO"]
    live_since: str | None
    changed_by: str | None
    scan_interval_min: int
    next_scan_at: str | None


class ModeChanged(ModeOut):
    scan_run_id: str | None


class ModeIn(BaseModel):
    mode: Literal["LIVE", "DEMO"]


@router.get("/mode", response_model=ModeOut)
def get_mode(request: Request) -> dict[str, Any]:
    return mode_view(request.app)


@router.put("/mode", response_model=ModeChanged)
def put_mode(
    body: ModeIn,
    request: Request,
    user: CurrentUser = Depends(require_role(Role.SCM_MANAGER)),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    changed = change_mode(request.app, session, body.mode, username=user.username, now=utc_now())
    request.app.state.run_queue.wake()  # after the commit, so the worker sees the scan
    return changed
