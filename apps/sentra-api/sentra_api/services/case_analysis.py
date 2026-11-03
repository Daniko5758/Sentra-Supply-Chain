"""Impact, score and alert of a case: load the inputs, call the pure engines, persist the result.

The same path serves every case, whatever the origin of its signal (CLAUDE.md rule 2).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.impact_engine import (
    DisruptionInfo, Impact, ImpactData, TierLinkInfo, compute_impact, exposure_impact, tariff_impact,
)
from sentra_api.models import Alert, Case, CaseSite, ImpactSnapshot, PriorityRow as PriorityRowRecord, Signal, SupplierSite, TierLink
from sentra_api.priority import PriorityRow, SiteProfile, case_level, case_score, country_priority_rows, priority_rows
from sentra_api.services.ids import next_id
from sentra_api.signals import DISRUPTION_COUNTRY_RISKS


@dataclass(frozen=True)
class CaseEvaluation:
    impact: Impact
    rows: tuple[PriorityRow, ...]
    score: float
    level: str


def first_signal(session: Session, case_id: str) -> Signal:
    """The signal that opened the case; its tariff terms drive R-14."""
    return session.scalars(
        select(Signal).where(Signal.case_id == case_id).order_by(Signal.fetched_at, Signal.id).limit(1)
    ).one()


def recovery_days(site: SupplierSite, case_site: CaseSite, downtime_days: int | None = None) -> int:
    """d(s), R-02 v2: 0 when excluded; else the supplier's answer (R-13), else the downtime the source states, else the
    site's recovery time; a backup ramp the supplier confirmed (R-13 v2) can only lower it."""
    if case_site.excluded:
        return 0
    if case_site.delay_override_days is not None:
        days = case_site.delay_override_days
    elif downtime_days is not None:
        days = downtime_days
    else:
        days = site.ttr_days
    if case_site.backup_ramp_confirmed is not None:
        days = min(days, case_site.backup_ramp_confirmed)
    return days


def is_disruption_country_event(case: Case, downtime_days: int | None) -> bool:
    """R-18 v2: a political or logistics country event that states how long it lasts is calculated like a location event."""
    return case.scope == "COUNTRY" and case.risk_type in DISRUPTION_COUNTRY_RISKS and downtime_days is not None


def confirmed_awal(session: Session, case_id: str) -> dict[tuple[str, int], date]:
    """R-03: confirmation dates when the case was created, read from snapshot version 1 (empty before it exists)."""
    snapshot = session.get(ImpactSnapshot, (case_id, 1))
    if snapshot is None:
        return {}
    return {(row["po_id"], row["item_no"]): date.fromisoformat(row["confirmed_date"])
            for row in snapshot.payload["po_lines"]}


@dataclass(frozen=True)
class CaseInputs:
    """The SENTRA-side data an impact calculation needs besides the ERP read."""

    sites: dict[str, SupplierSite]
    data_links: tuple[TierLinkInfo, ...]
    case_sites: tuple[CaseSite, ...]
    downtime_days: int | None = None  # of the signal that opened the case (R-02 v2)
    category: str | None = None  # of that signal (R-01 v2, G.1 `disruption`)

    def recovery(self) -> dict[str, int]:
        return {cs.site_id: recovery_days(self.sites[cs.site_id], cs, self.downtime_days) for cs in self.case_sites}


def load_case_inputs(session: Session, case: Case) -> CaseInputs:
    sites = {site.site_id: site for site in session.scalars(select(SupplierSite))}
    links = tuple(TierLinkInfo(l.tier2_site, l.tier1_site, tuple(l.parts), l.buffer_days)
                  for l in session.scalars(select(TierLink)))
    case_sites = tuple(session.scalars(select(CaseSite).where(CaseSite.case_id == case.case_id).order_by(CaseSite.site_id)))
    opening = session.scalars(
        select(Signal).where(Signal.case_id == case.case_id).order_by(Signal.fetched_at, Signal.id).limit(1)
    ).first()
    return CaseInputs(sites, links, case_sites, None if opening is None else opening.downtime_days,
                      None if opening is None else opening.category)


def case_impact(
    session: Session, inputs: CaseInputs, erp: ErpData, case: Case, d0: date, recovery: Mapping[str, int] | None = None
) -> Impact:
    """R-02..R-06, R-14 or R-18 of the case on the given ERP state (the real one, or a plan applied to it).

    `recovery`: d(s) per candidate site instead of the ones the case sites give now (option C asks what a faster backup would do).
    """
    logistics = case.risk_type == "LOGISTICS"
    if case.scope == "LOCATION" or is_disruption_country_event(case, inputs.downtime_days):
        data = ImpactData(erp, inputs.data_links)
        impact = compute_impact(data, inputs.recovery() if recovery is None else recovery, d0,
                                confirmed_awal=confirmed_awal(session, case.case_id), logistics=logistics)
        if case.scope == "LOCATION":
            return impact
        sites = tuple(sorted(cs.site_id for cs in inputs.case_sites))
        return replace(impact, scope="COUNTRY", disruption=DisruptionInfo(case.country, inputs.category,
                                                                           inputs.downtime_days, sites))
    site_countries = {site_id: site.country for site_id, site in inputs.sites.items()}
    if case.risk_type == "TARIFF":
        signal = first_signal(session, case.case_id)
        return tariff_impact(erp, site_countries, country=case.country, category=signal.category,
                             tariff_pp=signal.tariff_pp, effective_date=signal.effective_date)
    return exposure_impact(erp, site_countries, country=case.country, d0=d0)


def evaluate_case(session: Session, erp: ErpData, case: Case, d0: date) -> CaseEvaluation:
    """R-02..R-08, R-14, R-18 for the current ERP data and the case's site states."""
    inputs = load_case_inputs(session, case)
    impact = case_impact(session, inputs, erp, case, d0)
    profiles = {
        cs.site_id: SiteProfile(cs.site_id, inputs.sites[cs.site_id].supplier_name, inputs.sites[cs.site_id].tier,
                                inputs.sites[cs.site_id].bcp_score)
        for cs in inputs.case_sites
    }
    if case.scope == "LOCATION" or impact.disruption is not None:
        rows = priority_rows(ImpactData(erp, inputs.data_links), profiles, inputs.recovery(), d0,
                             confirmed_awal=confirmed_awal(session, case.case_id), logistics=case.risk_type == "LOGISTICS")
    else:
        rows = country_priority_rows(impact, profiles, [cs.site_id for cs in inputs.case_sites])
    return CaseEvaluation(impact, rows, case_score(rows), case_level(rows))


def persist_evaluation(
    session: Session, case: Case, evaluation: CaseEvaluation, *, version: int, trigger: str, now: datetime
) -> None:
    """Store snapshot + priority rows of `version`, update the case score and the case's single alert."""
    impact = replace(evaluation.impact, version=version)
    session.add(ImpactSnapshot(case_id=case.case_id, version=version, trigger=trigger, payload=impact.to_dict(), created_at=now))
    session.add_all(
        PriorityRowRecord(
            case_id=case.case_id, version=version, site_id=row.site_id, tier=row.tier, recovery_days=row.recovery_days,
            rar=row.rar, output_value=row.output_value, po_value=row.po_value, bcp_score=row.bcp_score,
            score=row.score, components=row.to_dict()["components"],
        )
        for row in evaluation.rows
    )
    case.score, case.level, case.updated_at = evaluation.score, evaluation.level, now
    upsert_alert(session, case, level=evaluation.level, score=evaluation.score, units=impact.totals.units_blocked,
                 rar=impact.totals.rar, now=now)


def upsert_alert(
    session: Session, case: Case, *, level: str, score: float, units: int, rar: int, now: datetime
) -> Alert:
    """The case's single alert: created once, then updated when the numbers or the level change."""
    alert = session.scalar(select(Alert).where(Alert.case_id == case.case_id))
    if alert is None:
        alert = Alert(
            alert_id=next_id(session, Alert.alert_id, "AL"), case_id=case.case_id, level=level, title=case.title,
            location=case.location, score=score, units_at_risk=units, revenue_at_risk=rar, mode=case.mode,
            created_at=now,
        )
        session.add(alert)
    else:
        alert.level, alert.score, alert.units_at_risk, alert.revenue_at_risk = level, score, units, rar
    session.flush()
    return alert
