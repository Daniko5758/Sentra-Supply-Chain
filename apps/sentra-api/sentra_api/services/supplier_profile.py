"""EP-06 supplier profile: master data of one site, the sites that feed it, its alternates and its messages."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.models import AlternateSource, SupplierMessage, SupplierSite, TierLink
from sentra_api.priority import bcp_band, bcp_categories, round1
from sentra_api.services.case_tools import alternate_sources, message_view


def _bcp(site: SupplierSite) -> dict[str, Any]:
    """EP-06 `bcp` (v1.11): R-07a v2 score and band, categories to one decimal (half-up), answers and stated RTO."""
    categories = {name: round1(value) for name, value in bcp_categories(site.bcp_answers).items()}
    return {"score": site.bcp_score, "band": bcp_band(site.bcp_score), "categories": categories,
            "answers": dict(site.bcp_answers), "stated_rto_days": site.bcp_rto_days,
            "backup_site": site.bcp_backup_site, "emergency_plan": site.bcp_emergency_plan,
            "safety_stock": site.bcp_safety_stock, "insurance": site.bcp_insurance}


def _alternate_sources(session: Session, parts: list[str]) -> list[dict[str, Any]]:
    """TL-14 sources plus the air-freight variant of option A (v1.11)."""
    air = {(a.part_id, a.site_id): a for a in session.scalars(select(AlternateSource).where(AlternateSource.part_id.in_(parts)))}
    return [
        source | {"air_lead_time_days": air[source["part_id"], source["site_id"]].air_lead_time_days,
                  "air_freight_per_unit": air[source["part_id"], source["site_id"]].air_freight_per_unit}
        for source in alternate_sources(session, parts)["sources"]
    ]


def supplier_profile(session: Session, erp: ErpData, site_id: str) -> dict[str, Any]:
    site = session.get(SupplierSite, site_id)
    if site is None:
        raise ApiError(404, "NOT_FOUND", f"Supplier site {site_id} was not found.", {"site_id": site_id})
    parts = sorted(m.material_id for m in erp.materials if m.main_supplier_id == site_id)
    links = sorted((l for l in session.scalars(select(TierLink)) if l.tier1_site == site_id),
                   key=lambda l: (l.tier2_site, l.component))
    messages = session.scalars(
        select(SupplierMessage).where(SupplierMessage.site_id == site_id).order_by(SupplierMessage.created_at, SupplierMessage.id)
    )
    return {
        "site_id": site.site_id, "supplier_name": site.supplier_name, "tier": site.tier, "kabupaten": site.kabupaten,
        "kecamatan": site.kecamatan, "country": site.country, "hazard_zone_flood": site.hazard_zone_flood,
        "ttr_days": site.ttr_days, "otd_12m": site.otd_12m, "backup_site": site.backup_site,
        "bcp": _bcp(site),
        "backup": {"site": site.backup_site, "ramp_days": site.backup_ramp_days, "surcharge_pct": site.backup_surcharge_pct},
        "parts_supplied": parts,
        "tier2_suppliers": [{"site_id": l.tier2_site, "component": l.component, "component_category": l.component_category,
                              "buffer_days": l.buffer_days} for l in links],
        "alternate_sources_for_parts": _alternate_sources(session, parts),
        "messages": [message_view(m) for m in messages],
    }
