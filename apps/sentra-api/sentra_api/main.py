from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from mock_s4.config import Settings as MockSettings
from mock_s4.main import create_app as create_mock_app
from mock_s4.seed_data import jakarta_today, seed_meta

from sentra_api.agent_runner import RunQueue, WarmSessionPool, make_runner
from sentra_api.auth import ApiAuthGate, AuthService
from sentra_api.confidence import SapRptGateway
from sentra_api.config import Settings
from sentra_api.db import Database
from sentra_api.errors import install_error_handlers
from sentra_api.integrations.zoom import ZoomClient
from sentra_api.openapi_errors import install_error_schema
from sentra_api.routers import internal_tools
from sentra_api.routers.public import (
    alerts, auth, case_actions, cases, chat, dashboard, demo, heatmap, network, optional, recommendations, reports, runs,
    schedule, signals, system, trace,
)
from sentra_api.seed import load_seed
from sentra_api.services.app_mode import init_mode
from sentra_api.services.system_checks import SystemChecks
from sentra_api.services.validation_attempts import ValidationAttempts
from sentra_api.supplier_sim import SupplierReplyWorker
from sentra_api.version import git_version
from sentra_api.web import SpaStaticFiles, default_web_dist, has_build


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


def create_app(
    settings: Settings | None = None,
    *,
    database_url: str = "sqlite:///sentra.db",
    mock_database_url: str = "sqlite:///mock_s4.db",
    web_dist: Path | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    database = Database(database_url)
    mock_app = create_mock_app(
        MockSettings(_env_file=None, mock_s4_token=settings.mock_s4_token,
                     feature_erp_execution=settings.feature_erp_execution),  # E-11..E-17 only for F-12
        database_url=mock_database_url,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.create_all()
        d0_date = jakarta_today()
        app.state.seed_data = load_seed(d0_date, database)
        app.state.seed_meta = seed_meta(app.state.seed_data)  # EP-03, EP-26: file, version, dataset of the active seed
        app.state.d0_date = d0_date
        mock_app.state.seed_d0_date = d0_date
        try:
            # Mounted applications do not receive lifespan events automatically.
            async with mock_app.router.lifespan_context(mock_app):
                app.state.run_queue.start()  # B-13: the worker that calls the agent (no-op for AGENT_RUNNER=off)
                app.state.session_pool.start()  # B-13b: warm-up and keep-alive, only with AGENT_KEEPALIVE_ENABLED
                app.state.supplier_sim.start()  # B-15: the scripted supplier answers (R-12)
                try:
                    yield
                finally:
                    app.state.supplier_sim.stop()
                    app.state.session_pool.stop()
                    app.state.run_queue.stop()
        finally:
            database.dispose()

    app = FastAPI(title="SENTRA API", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.mock_s4 = mock_app
    app.state.auth = AuthService.from_settings(settings)
    app.state.zoom = ZoomClient(settings)  # B-45: Server-to-Server OAuth token kept in memory; tests replace it with a mock transport
    app.state.rpt_gateway = SapRptGateway.from_settings(settings)  # B-14: RPT-1.6; the SDK client is created at the first request
    init_mode(app)  # always DEMO at start, kept in memory only (DEC-024, EP-29)
    app.state.agent_runner = make_runner(app)  # also used by EP-18, the one call the user's request waits for
    app.state.run_queue = RunQueue(app, app.state.agent_runner)
    app.state.session_pool = WarmSessionPool(app)  # B-13b: warm AgentCore sessions (contract D.1 v1.3)
    app.state.system_checks = SystemChecks(app)  # B-19: the light checks of POST /api/system/checks
    app.state.supplier_sim = SupplierReplyWorker(app)  # B-15: scripted supplier answers after SIM_REPLY_DELAY_S
    app.state.validation_attempts = ValidationAttempts()  # B-16, B-18: G-04, the first failed text goes back to the agent
    install_error_handlers(app)
    install_error_schema(app)
    version = git_version()

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(version=version)

    for module in (auth, schedule, network, signals, alerts, cases, case_actions, trace, recommendations, reports, heatmap,
                   chat, dashboard, demo, system, runs):
        app.include_router(module.router)
    for router in (optional.meeting_room, optional.approvals, optional.whatif):  # EP-20..EP-23, EP-25, EP-30: off by default
        app.include_router(router)
    app.include_router(internal_tools.router)
    app.add_middleware(ApiAuthGate)
    app.mount("/mock-s4", mock_app)
    if web_dist is not None and has_build(web_dist):
        # B-21: mounted last, so every route above (and /health, /docs, /openapi.json) wins over the UI. Only the served app
        # asks for it (below); tests that add probe routes after create_app() keep an app without the "/" catch-all.
        app.mount("/", SpaStaticFiles(web_dist), name="web")
    return app


app = create_app(web_dist=default_web_dist())
