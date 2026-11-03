"""B-33: read-only R-16 simulations shared by EP-25 and TL-18."""

from datetime import date
from typing import Any, Literal

from fastapi import FastAPI
from mock_s4.seed_data import jakarta_today
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.impact_engine import ImpactData, TierLinkInfo, compute_impact
from sentra_api.impact_engine.models import (
    BaselineShort, DisruptionInfo, ExposureInfo, ImpactPoLine, ImpactProductionOrder, ImpactSalesOrder,
    ImpactTotals, PartDelay, TariffInfo,
)
from sentra_api.models import SupplierSite, TierLink
from sentra_api.services.app_mode import LIVE

BANNER = "What-if only — no case, no email, no ERP change."


class WhatIfImpact(BaseModel):
    """G.1, excluding the engine's private per-part shortage information."""

    version: int
    scope: Literal["LOCATION"]
    part_delays: list[PartDelay]
    po_lines: list[ImpactPoLine]
    production_orders: list[ImpactProductionOrder]
    sales_orders: list[ImpactSalesOrder]
    totals: ImpactTotals
    tariff: TariffInfo | None
    exposure: ExposureInfo | None
    disruption: DisruptionInfo | None
    baseline_short: list[BaselineShort]


class SiteRar(BaseModel):
    site_id: str
    rar: int


class PartDays(BaseModel):
    part_id: str
    days: int


class ProductUnits(BaseModel):
    product_id: str
    units: int


class WhatIfCharts(BaseModel):
    rar_by_site: list[SiteRar]
    delay_by_part: list[PartDays]
    units_by_product: list[ProductUnits]


class WhatIfResponse(BaseModel):
    location: str
    banner: str
    sites_t1: list[str]
    sites_t2: list[str]
    kpi: ImpactTotals
    impact: WhatIfImpact
    charts: WhatIfCharts


def simulation_d0(application: FastAPI) -> date:
    """RULES section 3: reset date until the next reset; current Jakarta date for a live simulation.

    Only the input date is selected here; R-02..R-06 never consult application mode or the clock.
    `jakarta_today` is also the date source used by application startup and demo reset.
    """
    return jakarta_today() if application.state.app_mode == LIVE else application.state.d0_date


def locations(session: Session) -> list[str]:
    """R-16 dropdown from the active tier-1/2 network, excluding alternate-only sites."""
    return list(session.scalars(select(SupplierSite.kabupaten).where(
        SupplierSite.tier.in_(("1", "2")),
    ).distinct().order_by(SupplierSite.kabupaten)))


def run_what_if(session: Session, erp: ErpData, location: str, d0: date) -> dict[str, Any]:
    """No case, signal, snapshot, integration call, or ERP change is created by this calculation."""
    allowed = locations(session)
    if location not in allowed:
        raise ApiError(400, "VALIDATION_FAILED", "Unknown location.", {"field": "location", "allowed": allowed})
    sites = list(session.scalars(select(SupplierSite).where(
        SupplierSite.tier.in_(("1", "2")), SupplierSite.kabupaten == location,
    ).order_by(SupplierSite.site_id)))
    links = tuple(TierLinkInfo(link.tier2_site, link.tier1_site, tuple(link.parts), link.buffer_days)
                  for link in session.scalars(select(TierLink)))
    data = ImpactData(erp, links)
    recovery = {site.site_id: site.ttr_days for site in sites}
    impact = compute_impact(data, recovery, d0)
    payload = impact.to_dict()
    return {
        "location": location, "banner": BANNER,
        "sites_t1": [site.site_id for site in sites if site.tier == "1"],
        "sites_t2": [site.site_id for site in sites if site.tier == "2"],
        "kpi": payload["totals"], "impact": payload,
        "charts": {
            # R-06: recompute with each site alone; standalone values may overlap.
            "rar_by_site": [{"site_id": site.site_id,
                             "rar": compute_impact(data, {site.site_id: site.ttr_days}, d0).totals.rar}
                            for site in sites],
            "delay_by_part": [{"part_id": delay.part_id, "days": delay.days} for delay in impact.part_delays],
            "units_by_product": [
                {"product_id": product, "units": sum(order.units_blocked for order in impact.production_orders
                                                      if order.product_id == product)}
                for product in impact.totals.products_affected
            ],
        },
    }
