from datetime import date

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from mock_s4.seed_data import load_source_seed, seed_meta

from sentra_api.db import Base, Database
from sentra_api.errors import ApiError
from sentra_api.priority import bcp_v2
from sentra_api.models import (
    RESET_TRANSACTION_TABLES,
    AlternateSource,
    RptHistoryAffected,
    RptHistoryDelivery,
    SupplierSite,
    TierLink,
)


DELETE_MASTER_ORDER = (AlternateSource, TierLink, RptHistoryDelivery, RptHistoryAffected, SupplierSite)


def load_seed(d0_date: date, database: Database) -> dict:
    """Load the SENTRA master seed (`data/<SENTRA_SEED_FILE>`) and return its D0-normalized scenario data."""
    bundle = load_source_seed(d0_date)
    _check_bcp_scores(bundle)
    with Session(database.engine) as session, session.begin():
        _clear_transaction_tables(session)
        for model in DELETE_MASTER_ORDER:
            session.execute(delete(model))
        session.add_all(SupplierSite(**row) for row in bundle["sentra"]["supplier_sites"])
        session.add_all(TierLink(**row) for row in bundle["sentra"]["tier_links"])
        session.add_all(AlternateSource(**row) for row in bundle["sentra"]["alternate_sources"])
        session.add_all(RptHistoryAffected(**row) for row in bundle["sentra"]["rpt_history_affected"])
        session.add_all(RptHistoryDelivery(**row) for row in bundle["sentra"]["rpt_history_delivery"])
    return bundle


def _check_bcp_scores(bundle: dict) -> None:
    """DATA_MODEL 4: a seed whose `bcp_score` differs from R-07a v2 of its `bcp_answers` is refused."""
    for row in bundle["sentra"]["supplier_sites"]:
        if bcp_v2(row["bcp_answers"]) != row["bcp_score"]:
            raise ValueError(f"bcp_score {row['site_id']} does not match bcp_answers (R-07a v2)")


def _clear_transaction_tables(session: Session) -> None:
    for name in RESET_TRANSACTION_TABLES:
        table = Base.metadata.tables.get(name)
        if table is not None:
            session.execute(delete(table))


async def reset_demo_data(*, d0_date: date, database: Database, application, settings) -> dict[str, str]:
    """Reset mock-s4 over E-10, then reset SENTRA transactions and master data."""
    if settings.mock_s4_token is None:
        raise ApiError(502, "UPSTREAM_ERROR", "The mock S/4 reset service is not configured.")

    application.state.mock_s4.state.seed_d0_date = d0_date
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://sentra.internal") as client:
        try:
            response = await client.post(
                "/mock-s4/admin/reset",
                headers={"Authorization": f"Bearer {settings.mock_s4_token.get_secret_value()}"},
            )
        except httpx.HTTPError as exc:
            raise ApiError(502, "UPSTREAM_ERROR", "The mock S/4 reset service is unavailable.") from exc

    if response.status_code != 200:
        raise ApiError(502, "UPSTREAM_ERROR", "The mock S/4 reset request failed.")
    if response.json().get("d0_date") != d0_date.isoformat():
        raise ApiError(409, "CONFLICT", "The reset date changed; retry the reset.")

    application.state.seed_data = load_seed(d0_date, database)
    application.state.seed_meta = seed_meta(application.state.seed_data)
    application.state.d0_date = d0_date
    return {"status": "OK", "d0_date": d0_date.isoformat()}
