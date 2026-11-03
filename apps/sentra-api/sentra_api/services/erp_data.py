"""Read the simulated ERP for the pure engines. Same process as mock-s4, so no network hop (CLAUDE.md, sentra-api)."""

import httpx
from fastapi import FastAPI

from sentra_api.erp import ErpClient, ErpData
from sentra_api.errors import ApiError

IN_PROCESS_BASE_URL = "http://sentra.internal/mock-s4"


def erp_client(application: FastAPI) -> ErpClient:
    """A client of the mock S/4 in this process; use it as `async with`."""
    token = application.state.settings.mock_s4_token
    if token is None:
        raise ApiError(502, "UPSTREAM_ERROR", "The mock S/4 service is not configured.")
    return ErpClient(IN_PROCESS_BASE_URL, token.get_secret_value(), transport=httpx.ASGITransport(app=application))


async def load_erp_data(application: FastAPI) -> ErpData:
    async with erp_client(application) as client:
        return await client.load()
