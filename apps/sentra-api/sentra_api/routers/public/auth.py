"""EP-01 login, EP-02 logout, EP-03 profile and app context."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from sentra_api.auth import SESSION_COOKIE, SESSION_TTL_SECONDS, CurrentUser, Role, require_user
from sentra_api.errors import ApiError
from sentra_api.flags import feature_flags

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    username: str
    role: Role


class FeatureFlagsResponse(BaseModel):
    erp_execution: bool
    meeting_room: bool
    whatif: bool


class SeedInfo(BaseModel):
    """Active seed (v1.11, DEC-035): `dataset = "TEST"` makes the UI show the TEST DATA badge."""

    file: str
    version: str
    dataset: Literal["DEMO", "TEST"]


class MeResponse(BaseModel):
    username: str
    role: Role
    mode: Literal["LIVE", "DEMO"]
    d0_date: date
    feature_flags: FeatureFlagsResponse
    seed: SeedInfo


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    result = request.app.state.auth.login(body.username, body.password)
    if result is None:
        raise ApiError(401, "UNAUTHENTICATED", "Invalid username or password.")
    user, token = result
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_base_url.startswith("https://"),
        path="/",
    )
    return LoginResponse(username=user.username, role=user.role)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, _user: CurrentUser = Depends(require_user)) -> None:
    request.app.state.auth.sessions.delete(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeResponse)
def me(request: Request, user: CurrentUser = Depends(require_user)) -> MeResponse:
    state = request.app.state
    return MeResponse(
        username=user.username,
        role=user.role,
        mode=state.app_mode,
        d0_date=state.d0_date,
        feature_flags=FeatureFlagsResponse(**feature_flags(state.settings)),
        seed=SeedInfo(**state.seed_meta),
    )
