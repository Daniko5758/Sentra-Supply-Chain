"""EP-12 case detail, shared by the public API and TL-17 `get_case`."""

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.errors import ApiError
from sentra_api.models import (
    Case, CaseSite, ImpactSnapshot, PriorityRow, Recommendation, Signal, SupplierRequest, SupplierSite, TierLink,
)
from sentra_api.priority import bcp_band
from sentra_api.priority import level as score_level
from sentra_api.services.case_analysis import first_signal
from sentra_api.services.meeting_records import EscalationOut
from sentra_api.timeutil import iso


class VersionOut(BaseModel):
    version: int
    trigger: str
    created_at: str


class CaseSignalOut(BaseModel):
    signal_id: str
    summary: str
    severity: int | None
    source_urls: list[str]
    source_label: str | None


class KpiOut(BaseModel):
    units_at_risk: int
    delayed_output_value: int
    affected_po_value: int
    revenue_at_risk: int
    priority_score: float | None
    level: str | None
    status: str


class SiteRequestOut(BaseModel):
    """`requests` of API_CONTRACT G.6 (v1.11)."""

    request_id: str
    request_type: Literal["IMPACT", "BACKUP_ACTIVATION", "PULL_IN"]
    status: Literal["REQUESTED", "ANSWERED", "NO_RESPONSE"]
    answer: str | None


class CaseSiteOut(BaseModel):
    """API_CONTRACT G.6."""

    site_id: str
    supplier_name: str
    tier: str
    path: str
    recovery_days: int
    k_a_formula: float | None
    p_rpt_a: float | None
    k_a: float | None
    k_b_formula: float | None
    p_rpt_b: float | None
    k_b: float | None
    rpt_status: str
    verification_status: str
    delay_override_days: int | None
    excluded: bool
    backup_ramp_confirmed: int | None
    backup_declined: bool
    backup_site: str | None
    requests: list[SiteRequestOut]


class PriorityRowOut(BaseModel):
    """API_CONTRACT G.5."""

    site_id: str
    supplier_name: str
    tier: str
    recovery_days: int
    rar: int
    output_value: int
    po_value: int
    bcp_score: int
    bcp_band: Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"]
    components: dict[str, float]
    score: float


class CaseDetail(BaseModel):
    case_id: str
    title: str
    risk_type: str
    scope: str
    location: str | None
    country: str
    mode: str
    simulated: bool
    status: str
    d0_date: str
    created_at: str
    updated_at: str
    version: int
    versions: list[VersionOut]
    signal: CaseSignalOut
    kpi: KpiOut
    impact: dict[str, Any]
    sites: list[CaseSiteOut]
    priority: list[PriorityRowOut]
    preliminary: bool
    escalation: EscalationOut  # v1.11 (DEC-039): replaces `meeting_room`


def _site_path(site: SupplierSite, links: list[TierLink]) -> str:
    """Tier 2: `<site> → <tier-1 site> → <parts>` for each tier-1 site it feeds (G.6); tier 1: the site itself."""
    if site.tier != "2":
        return site.site_id
    legs = [f"{site.site_id} → {link.tier1_site} → {', '.join(link.parts)}" for link in links if link.tier2_site == site.site_id]
    return "; ".join(legs) or site.site_id


def build_case_detail(session: Session, case_id: str, version: int | None, *, escalation: dict[str, Any]) -> CaseDetail:
    """404 NOT_FOUND when the case, or the requested version, does not exist. `escalation` is G.7 (the caller computes it: it
    needs the ERP)."""
    case = session.get(Case, case_id)
    if case is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} was not found.")
    snapshots = list(session.scalars(select(ImpactSnapshot).where(ImpactSnapshot.case_id == case_id).order_by(ImpactSnapshot.version)))
    chosen = version if version is not None else (snapshots[-1].version if snapshots else None)
    snapshot = next((s for s in snapshots if s.version == chosen), None)
    if snapshot is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} has no version {version}." if version else f"Case {case_id} has no impact snapshot.")

    rows = sorted(
        session.scalars(select(PriorityRow).where(PriorityRow.case_id == case_id, PriorityRow.version == snapshot.version)),
        key=lambda r: (-r.score, r.site_id),
    )
    rank = {row.site_id: position for position, row in enumerate(rows)}
    sites = {s.site_id: s for s in session.scalars(select(SupplierSite))}
    links = list(session.scalars(select(TierLink)))
    case_sites = sorted(
        session.scalars(select(CaseSite).where(CaseSite.case_id == case_id)),
        key=lambda cs: (rank.get(cs.site_id, len(rank)), cs.site_id),
    )
    recovery = {row.site_id: row.recovery_days for row in rows}
    requests: dict[str, list[SiteRequestOut]] = {}
    for r in session.scalars(select(SupplierRequest).where(SupplierRequest.case_id == case_id).order_by(SupplierRequest.request_id)):
        requests.setdefault(r.site_id, []).append(
            SiteRequestOut(request_id=r.request_id, request_type=r.request_type, status=r.status, answer=r.answer))

    signals = list(session.scalars(select(Signal).where(Signal.case_id == case_id).order_by(Signal.fetched_at, Signal.id)))
    opening = first_signal(session, case_id)
    severities = [s.severity for s in signals if s.severity is not None]
    urls = list(dict.fromkeys(url for s in signals for url in s.source_urls))

    totals = snapshot.payload["totals"]
    score = max((row.score for row in rows), default=None)
    recommendation = session.scalar(
        select(Recommendation).where(Recommendation.case_id == case_id, Recommendation.version == snapshot.version)
    )
    return CaseDetail(
        case_id=case.case_id, title=case.title, risk_type=case.risk_type, scope=case.scope, location=case.location,
        country=case.country, mode=case.mode, simulated=case.mode == "DEMO", status=case.status,
        d0_date=case.d0_date.isoformat(), created_at=iso(case.created_at), updated_at=iso(case.updated_at),
        version=snapshot.version,
        versions=[VersionOut(version=s.version, trigger=s.trigger, created_at=iso(s.created_at)) for s in snapshots],
        signal=CaseSignalOut(
            signal_id=opening.id, summary=opening.summary, severity=max(severities, default=None), source_urls=urls,
            source_label=opening.source_label,
        ),
        kpi=KpiOut(
            units_at_risk=totals["units_blocked"], delayed_output_value=totals["output_value"],
            affected_po_value=totals["po_value"], revenue_at_risk=totals["rar"], priority_score=score,
            level=None if score is None else score_level(score), status=case.status,
        ),
        impact=snapshot.payload,
        sites=[
            CaseSiteOut(
                site_id=cs.site_id, supplier_name=sites[cs.site_id].supplier_name, tier=sites[cs.site_id].tier,
                path=_site_path(sites[cs.site_id], links), recovery_days=recovery.get(cs.site_id, 0),
                k_a_formula=cs.k_a_formula, p_rpt_a=cs.p_rpt_a, k_a=cs.k_a, k_b_formula=cs.k_b_formula,
                p_rpt_b=cs.p_rpt_b, k_b=cs.k_b, rpt_status=cs.rpt_status, verification_status=cs.verification_status,
                delay_override_days=cs.delay_override_days, excluded=cs.excluded,
                backup_ramp_confirmed=cs.backup_ramp_confirmed, backup_declined=cs.backup_declined,
                backup_site=sites[cs.site_id].backup_site, requests=requests.get(cs.site_id, []),
            )
            for cs in case_sites
        ],
        priority=[
            PriorityRowOut(
                site_id=r.site_id, supplier_name=sites[r.site_id].supplier_name, tier=r.tier,
                recovery_days=r.recovery_days, rar=r.rar, output_value=r.output_value, po_value=r.po_value,
                bcp_score=r.bcp_score, bcp_band=bcp_band(r.bcp_score), components=r.components, score=r.score,
            )
            for r in rows
        ],
        # Until a final recommendation exists (B-16) every case counts as preliminary.
        preliminary=True if recommendation is None else recommendation.preliminary,
        escalation=EscalationOut(**escalation),
    )
