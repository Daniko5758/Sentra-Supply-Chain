from contextlib import asynccontextmanager

from fastapi import FastAPI

from mock_s4.config import Settings
from mock_s4.db import Database
from mock_s4.errors import install_error_handlers
from mock_s4.middleware import SimulatedAuthMiddleware
from mock_s4.router import router
from mock_s4.seed import load_mock_seed
from mock_s4.seed_data import jakarta_today
from mock_s4.write_api import router as write_router


def create_app(settings: Settings | None = None, *, database_url: str = "sqlite:///mock_s4.db") -> FastAPI:
    settings = settings if settings is not None else Settings()
    database = Database(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.create_all()
        d0_date = getattr(app.state, "seed_d0_date", None) or jakarta_today()
        app.state.seed_bundle = load_mock_seed(d0_date, database)
        app.state.d0_date = d0_date
        try:
            yield
        finally:
            database.dispose()

    app = FastAPI(title="Mock SAP S/4HANA — SIMULATED", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(SimulatedAuthMiddleware, token=settings.mock_s4_token)
    app.include_router(router)
    if settings.feature_erp_execution:  # E-11..E-17 exist only for F-12 (CLAUDE.md of mock-s4, rule 5)
        app.include_router(write_router)
    return app


app = create_app()
