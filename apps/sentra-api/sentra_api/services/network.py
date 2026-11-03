"""Supplier network (EP-05, TL-06): tier-2 -> tier-1 -> part -> product, with the case highlight (FR-05, GN-04)."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.models import Case, CaseSite, ImpactSnapshot, PriorityRow, SupplierSite, TierLink
from sentra_api.priority import bcp_band

AFFECTED, VIA_TIER2, CONFIRMED_NO_IMPACT, NONE = "affected", "via_tier2", "confirmed_no_impact", "none"


def site_node(site: SupplierSite, highlight: str) -> dict[str, Any]:
    """`site` node. `kabupaten_local` is always present (DEC-025): local-script names from the seed, `[]` when none."""
    return {
        "id": site.site_id, "type": "site", "label": site.supplier_name, "tier": site.tier,
        "kabupaten": site.kabupaten, "kabupaten_local": list(site.kabupaten_local or []), "country": site.country,
        "bcp_score": site.bcp_score, "bcp_band": bcp_band(site.bcp_score), "highlight": highlight,
    }


class _Highlight:
    """What a snapshot of a case marks (the latest unless a version is asked): sites by state, parts and products as sets."""

    def __init__(self) -> None:
        self.sites: dict[str, str] = {}
        self.parts: set[str] = set()
        self.products: set[str] = set()


def _case_highlight(session: Session, case: Case, version: int, payload: dict[str, Any]) -> _Highlight:
    marked = _Highlight()
    marked.parts = {line["material_id"] for line in payload["po_lines"]} | {
        part for order in payload["production_orders"] for part in order["blocking_parts"]}
    marked.products = set(payload["totals"]["products_affected"])
    standalone_rar = {
        row.site_id: row.rar
        for row in session.scalars(select(PriorityRow).where(PriorityRow.case_id == case.case_id, PriorityRow.version == version))
    }
    for case_site in session.scalars(select(CaseSite).where(CaseSite.case_id == case.case_id)):
        rar = standalone_rar.get(case_site.site_id, 0)
        if case_site.verification_status == "CONFIRMED_BY_SUPPLIER" and rar == 0:
            marked.sites[case_site.site_id] = CONFIRMED_NO_IMPACT
        elif rar > 0:
            marked.sites[case_site.site_id] = AFFECTED
    # A tier-1 site that is not a candidate but whose parts are late because of a tier-2 candidate.
    for delay in payload["part_delays"]:
        via = delay["via_site"]
        if via is not None and delay["part_id"] in marked.parts and via not in marked.sites:
            marked.sites[via] = VIA_TIER2
    return marked


def build_network(session: Session, erp: ErpData, case_id: str | None, version: int | None = None) -> dict[str, Any]:
    """EP-05 body. Without a case every highlight is `none` / `false`; a case without a snapshot is highlighted as none.

    `version` (only with a case) highlights the path of that snapshot of the case; None = the latest. 404 when the case has no such
    version. The tier-2 / tier-1 / part / product structure is master data and the same for every version.
    """
    marked, version_shown = _Highlight(), None
    if case_id is not None:
        case = session.get(Case, case_id)
        if case is None:
            raise ApiError(404, "NOT_FOUND", f"Case {case_id} was not found.")
        query = select(ImpactSnapshot).where(ImpactSnapshot.case_id == case_id)
        if version is not None:
            query = query.where(ImpactSnapshot.version == version)
        snapshot = session.scalars(query.order_by(ImpactSnapshot.version.desc()).limit(1)).first()
        if snapshot is None and version is not None:
            raise ApiError(404, "NOT_FOUND", f"Case {case_id} has no version {version}.", {"case_id": case_id, "version": version})
        if snapshot is not None:
            version_shown = snapshot.version
            marked = _case_highlight(session, case, version_shown, snapshot.payload)

    sites = sorted((s for s in session.scalars(select(SupplierSite)) if s.tier in ("1", "2")),
                   key=lambda s: (-int(s.tier), s.site_id))
    site_ids = {s.site_id for s in sites}
    links = sorted(session.scalars(select(TierLink)), key=lambda l: (l.tier2_site, l.tier1_site, l.component))
    materials = sorted(erp.materials, key=lambda m: m.material_id)
    products = sorted(erp.products, key=lambda p: p.product_id)
    bom = sorted(erp.bom_items, key=lambda b: (b.material_id, b.product_id))

    def state(site_id: str) -> str:
        return marked.sites.get(site_id, NONE)

    nodes: list[dict[str, Any]] = [site_node(s, state(s.site_id)) for s in sites]
    nodes += [{"id": m.material_id, "type": "part", "label": m.description, "category": m.category,
               "highlight": m.material_id in marked.parts}
              for m in materials]
    nodes += [{"id": p.product_id, "type": "product", "label": p.description, "highlight": p.product_id in marked.products}
              for p in products]

    edges: list[dict[str, Any]] = [
        {"from": l.tier2_site, "to": l.tier1_site, "type": "tier2_to_tier1", "component": l.component,
         "component_category": l.component_category, "buffer_days": l.buffer_days,
         "highlight": state(l.tier2_site) == AFFECTED and any(part in marked.parts for part in l.parts)}
        for l in links
    ]
    edges += [
        {"from": m.main_supplier_id, "to": m.material_id, "type": "supplies",
         "highlight": m.material_id in marked.parts and state(m.main_supplier_id) in (AFFECTED, VIA_TIER2)}
        for m in materials if m.main_supplier_id in site_ids
    ]
    edges += [
        {"from": b.material_id, "to": b.product_id, "type": "used_in", "qty_per_unit": b.qty_per_unit,
         "highlight": b.material_id in marked.parts and b.product_id in marked.products}
        for b in bom
    ]
    return {"case_id": case_id, "version": version_shown, "nodes": nodes, "edges": edges}
