"""EP-16 / TL-22: load sites and open cases, then call the pure R-21 function."""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.case_state import CLOSED_STATUSES
from sentra_api.erp import ErpData
from sentra_api.heatmap import HeatmapSite, OpenCase, build_heatmap
from sentra_api.models import Case, CaseSite, SupplierSite


def country_heatmap(session: Session, erp: ErpData, d0: date) -> dict[str, Any]:
    sites = [HeatmapSite(s.site_id, s.supplier_name, s.tier, s.kabupaten, s.country) for s in session.scalars(select(SupplierSite))]
    candidates: dict[str, list[str]] = {}
    for row in session.scalars(select(CaseSite).order_by(CaseSite.case_id, CaseSite.site_id)):
        candidates.setdefault(row.case_id, []).append(row.site_id)
    open_cases = [
        OpenCase(c.case_id, c.title, c.level, c.score, tuple(candidates.get(c.case_id, ())), c.mode == "DEMO")
        for c in session.scalars(select(Case).where(Case.status.not_in(CLOSED_STATUSES)).order_by(Case.case_id))
        if c.level is not None and c.score is not None  # a case without a score has no level to show yet
    ]
    return build_heatmap(erp, sites, open_cases, d0).to_dict()
