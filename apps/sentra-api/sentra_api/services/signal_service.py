"""TL-05 save_signals: validate, drop (G-02), match sites (R-01), merge or open cases, compute the first snapshot."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentra_api.case_state import CLOSED_STATUSES
from sentra_api.erp import ErpData
from sentra_api.heatmap import country_name
from sentra_api.models import Case, CaseSite, Signal, SupplierSite, TierLink
from sentra_api.services.case_analysis import evaluate_case, persist_evaluation
from sentra_api.services.ids import next_id
from sentra_api.signals import (
    CASE_MERGE_WINDOW_H, DUPLICATE_WINDOW_DAYS, ParsedSignal, SignalRejected, SiteRef, candidate_sites, normalize_place, parse_signal,
)


@dataclass(frozen=True)
class SavedSignal:
    index: int
    signal_id: str
    match: str  # MATCHED | NO_SUPPLIER_MATCH
    case_id: str | None
    merged_into_existing: bool


@dataclass(frozen=True)
class DroppedSignal:
    index: int
    reason: str
    detail: str = ""


@dataclass
class IngestResult:
    saved: list[SavedSignal] = field(default_factory=list)
    dropped: list[DroppedSignal] = field(default_factory=list)

    def to_tl05(self) -> dict[str, Any]:
        """Response body of TL-05."""
        return {
            "saved": [{"index": s.index, "signal_id": s.signal_id, "match": s.match, "case_id": s.case_id,
                       "merged_into_existing": s.merged_into_existing} for s in self.saved],
            "dropped": [{"index": d.index, "reason": d.reason} for d in self.dropped],
        }


def case_location(signal: ParsedSignal, candidates: Sequence[SiteRef]) -> str:
    """Place shown in titles: the matched sites' own spelling (not the signal's casing), or the country name."""
    if signal.scope == "LOCATION":
        return candidates[0].kabupaten or signal.kabupaten or ""
    return country_name(signal.country)


def find_open_case(session: Session, signal: ParsedSignal, now: datetime) -> Case | None:
    """R-01: an open case with the same risk type and place whose latest signal is at most 72 h old."""
    candidates = session.scalars(
        select(Case)
        .where(Case.risk_type == signal.risk_type, Case.scope == signal.scope, Case.status.not_in(CLOSED_STATUSES))
        .order_by(Case.created_at.desc(), Case.case_id.desc())
    )
    window = timedelta(hours=CASE_MERGE_WINDOW_H)
    for case in candidates:
        if signal.scope == "LOCATION":
            same_place = normalize_place(case.location) == normalize_place(signal.kabupaten)
        else:
            same_place = case.country == signal.country
        if not same_place:
            continue
        latest = session.scalar(select(func.max(Signal.fetched_at)).where(Signal.case_id == case.case_id))
        if latest is not None and now - latest <= window:
            return case
    return None


def recent_source_urls(session: Session, now: datetime) -> set[str]:
    """URLs of the signals stored in the last 30 days (DUPLICATE_SOURCE)."""
    since = now - timedelta(days=DUPLICATE_WINDOW_DAYS)
    urls: set[str] = set()
    for source_urls in session.scalars(select(Signal.source_urls).where(Signal.fetched_at >= since)):
        urls.update(source_urls)
    return urls


def supplied_categories(session: Session, erp: ErpData) -> dict[str, set[str]]:
    """R-01 v2: the categories each site supplies: tier 1 = category of the parts it is the main supplier of (ERP material
    master), tier 2 = `component_category` of its links to tier-1 sites."""
    found: dict[str, set[str]] = {}
    for material in erp.materials:
        found.setdefault(material.main_supplier_id, set()).add(material.category)
    for link in session.scalars(select(TierLink)):
        if link.component_category:
            found.setdefault(link.tier2_site, set()).add(link.component_category)
    return found


def ingest_signals(
    session: Session, erp: ErpData, raw_signals: Sequence[Mapping[str, Any]], *, d0: date, now: datetime,
    new_session_id: Callable[[], str] | None = None,
) -> IngestResult:
    """Save the valid signals; a new case gets its first impact snapshot, priority rows and alert right away.

    `new_session_id` gives a new case its AgentCore session id (from the warm pool, B-13b); without it the model default
    (a fresh UUID) applies.

    The caller commits. Queueing `analyze_case` for new or changed cases is the caller's job.
    """
    refs = [SiteRef(s.site_id, s.tier, s.kabupaten, s.country) for s in session.scalars(select(SupplierSite))]
    categories = supplied_categories(session, erp)
    known_urls = recent_source_urls(session, now)
    result = IngestResult()
    for index, raw in enumerate(raw_signals):
        try:
            parsed = parse_signal(raw)
        except SignalRejected as rejected:
            result.dropped.append(DroppedSignal(index, rejected.reason, rejected.detail))
            continue
        if not parsed.simulated and known_urls.issuperset(parsed.source_urls):
            result.dropped.append(DroppedSignal(index, "DUPLICATE_SOURCE"))  # no new case, no merge
            continue
        known_urls.update(parsed.source_urls)

        candidates = candidate_sites(
            parsed.scope, parsed.kabupaten, parsed.country, refs, risk_type=parsed.risk_type, category=parsed.category,
            downtime_days=parsed.downtime_days, supplied_categories=categories,
        )
        case: Case | None = None
        merged = created = False
        if candidates:
            case = find_open_case(session, parsed, now)
            merged = case is not None
            if case is None:
                case = _open_case(session, parsed, candidates, d0, now, new_session_id)
                created = True
            else:
                case.updated_at = now

        signal_id = next_id(session, Signal.id, "SIG")
        session.add(Signal(
            id=signal_id, mode=parsed.mode, risk_type=parsed.risk_type, scope=parsed.scope, country=parsed.country,
            kabupaten=parsed.kabupaten, kecamatan=parsed.kecamatan, severity=parsed.severity,
            downtime_days=parsed.downtime_days,
            effective_date=parsed.effective_date, tariff_pp=parsed.tariff_pp, category=parsed.category,
            summary=parsed.summary, source_urls=parsed.source_urls, engine=parsed.engine,
            verification=parsed.verification, evidence=parsed.evidence, simulated=parsed.simulated,
            source_label=parsed.source_label, match="MATCHED" if case else "NO_SUPPLIER_MATCH",
            fetched_at=now, case_id=case.case_id if case else None,
        ))
        session.flush()
        if created:
            persist_evaluation(session, case, evaluate_case(session, erp, case, d0), version=1, trigger="initial", now=now)
        result.saved.append(SavedSignal(
            index, signal_id, "MATCHED" if case else "NO_SUPPLIER_MATCH", case.case_id if case else None, merged,
        ))
    return result


def _open_case(
    session: Session, signal: ParsedSignal, candidates: Sequence[SiteRef], d0: date, now: datetime,
    new_session_id: Callable[[], str] | None,
) -> Case:
    location = case_location(signal, candidates)
    session_id = {"agent_session_id": new_session_id()} if new_session_id is not None else {}
    case = Case(
        case_id=next_id(session, Case.case_id, "CASE"), title=f"{signal.risk_type.title()} – {location}",
        risk_type=signal.risk_type, scope=signal.scope, location=location, country=signal.country, mode=signal.mode,
        status="NEW", d0_date=d0, created_at=now, updated_at=now, **session_id,
    )
    session.add(case)
    session.add_all(CaseSite(case_id=case.case_id, site_id=site.site_id) for site in candidates)
    session.flush()
    return case
