"""TL-10 assess_confidence: load what R-10 / R-11 need, ask RPT-1.6 through `confidence.assess_sites`, store K per site.

Same path for every case, whatever the origin of its signal (CLAUDE.md rule 2). The RPT requests block, so they run in a
worker thread; the database is read before and written after, never inside the thread.
"""

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.confidence import Assessment, ConfidenceError, EventFacts, Rpt, SiteFacts, assess_sites
from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.impact_engine.engine import is_open
from sentra_api.models import Case, CaseSite, RptHistoryAffected, RptHistoryDelivery, Signal, SupplierSite
from sentra_api.services import case_tools
from sentra_api.services.system_status import record_integration_call

INTEGRATION = "sap-rpt-1.6"


def history_rows(session: Session, model: type[RptHistoryAffected] | type[RptHistoryDelivery]) -> list[dict[str, Any]]:
    """The labelled rows of the table, as they are (columns in table order)."""
    return [{column.name: getattr(row, column.key) for column in model.__table__.columns}
            for row in session.scalars(select(model).order_by(model.ID))]


def open_po_slip(erp: ErpData, site_id: str) -> int | None:
    """R-11: the largest `confirmed - requested` of the open PO lines of the site, at least 0; None without an open line."""
    slips = [(line.confirmed_date - line.requested_date).days
             for line in erp.po_lines if line.supplier_id == site_id and is_open(line)]
    return max(0, max(slips)) if slips else None


def event_facts(session: Session, case: Case) -> EventFacts:
    """The event of the case: its type, and the highest severity of its signals (R-01 merges by maximum)."""
    severities = [s for s in session.scalars(select(Signal.severity).where(Signal.case_id == case.case_id)) if s is not None]
    return EventFacts(event_type=case.risk_type, severity=max(severities, default=None))


def site_facts(session: Session, erp: ErpData, case: Case) -> list[SiteFacts]:
    """The candidate sites of the case in `site_id` order (as TL-07 lists them)."""
    mentioned = case_tools.mentioned_districts(session, case.case_id)
    facts = []
    for case_site in session.scalars(select(CaseSite).where(CaseSite.case_id == case.case_id).order_by(CaseSite.site_id)):
        site = session.get(SupplierSite, case_site.site_id)
        facts.append(SiteFacts(
            site_id=site.site_id, tier=site.tier, kecamatan_mentioned=case_tools.district_mentioned(case, site, mentioned),
            hazard_zone=site.hazard_zone_flood, has_backup_site=site.backup_site is not None, otd_12m=site.otd_12m,
            slip_days=open_po_slip(erp, site.site_id),
        ))
    return facts


def store(session: Session, case: Case, assessment: Assessment, now: datetime) -> dict[str, Any]:
    """Write K, P and `rpt_status` on the case sites and the RPT requests in `integration_call`; return the body of TL-10.

    The state of the supplier request (`verification_status`, R-13 values) is not touched.
    """
    rows = {row.site_id: row for row in session.scalars(select(CaseSite).where(CaseSite.case_id == case.case_id))}
    for site in assessment.sites:
        row = rows[site.site_id]
        row.k_a_formula, row.p_rpt_a, row.k_a = site.k_a_formula, site.p_rpt_a, site.k_a
        row.k_b_formula, row.p_rpt_b, row.k_b = site.k_b_formula, site.p_rpt_b, site.k_b
        row.rpt_status = site.rpt_status
    for call in assessment.calls:
        record_integration_call(session, INTEGRATION, call.status, call.duration_ms, call.message, now)
    session.flush()
    return {"case_id": case.case_id, "rpt_status": assessment.rpt_status,
            "sites": [site.to_tl10() for site in assessment.sites]}


async def assess_case(session: Session, erp: ErpData, case: Case, rpt: Rpt, now: datetime) -> dict[str, Any]:
    event = event_facts(session, case)
    sites = site_facts(session, erp, case)
    history_affected, history_delivery = history_rows(session, RptHistoryAffected), history_rows(session, RptHistoryDelivery)
    try:
        assessment = await asyncio.to_thread(
            assess_sites, event, sites, history_affected=history_affected, history_delivery=history_delivery, rpt=rpt,
        )
    except ConfidenceError as error:
        raise ApiError(409, "CONFLICT", str(error), {"case_id": case.case_id}) from None
    return store(session, case, assessment, now)
