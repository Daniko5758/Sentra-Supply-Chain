"""Case operations behind the internal tools (TL-07, TL-08, TL-09, TL-12, TL-14, TL-15, TL-21).

Rules stay in the pure modules; this layer loads the data, calls them and stores the result.
"""

import re
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.models import (
    AgentTrace, AlternateSource, Case, CaseSite, ImpactSnapshot, PriorityRow, Signal, SupplierMessage, SupplierRequest,
    SupplierSite,
)
from sentra_api.priority import bcp_band
from sentra_api.priority import level as score_level
from sentra_api.recommendation_rules import PlanAction, PlanError, simulate_plan
from sentra_api.services.case_analysis import (
    case_impact, evaluate_case, load_case_inputs, persist_evaluation, upsert_alert,
)
from sentra_api.services.case_detail import PriorityRowOut
from sentra_api.services.ids import next_id
from sentra_api.services.system_status import FINISHED_TRACE_STATUSES, record_trace_integration
from sentra_api.signals import normalize_place
from sentra_api.timeutil import iso, iso_or_none

SNAPSHOT_TRIGGERS = ("initial", "supplier_reply", "erp_execution")
MESSAGE_LIMIT = 200  # an integration message is a short status such as RATE_LIMITED


def load_case(session: Session, case_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} was not found.", {"case_id": case_id})
    return case


def latest_snapshot(session: Session, case_id: str) -> ImpactSnapshot | None:
    return session.scalars(
        select(ImpactSnapshot).where(ImpactSnapshot.case_id == case_id).order_by(ImpactSnapshot.version.desc()).limit(1)
    ).first()


def require_snapshot(session: Session, case_id: str) -> ImpactSnapshot:
    snapshot = latest_snapshot(session, case_id)
    if snapshot is None:
        raise ApiError(409, "CONFLICT", "The case has no impact snapshot yet; call compute_impact with persist=true first.")
    return snapshot


# --- TL-07 -------------------------------------------------------------------------------------------------------

def mentioned_districts(session: Session, case_id: str) -> set[str]:
    """Districts (kecamatan, normalized) named by any signal of the case."""
    return {normalize_place(name)
            for signal in session.scalars(select(Signal).where(Signal.case_id == case_id)) for name in signal.kecamatan}


def district_mentioned(case: Case, site: SupplierSite, mentioned: set[str]) -> bool:
    """R-01: a location case names the district of the site."""
    return case.scope == "LOCATION" and bool(site.kecamatan) and normalize_place(site.kecamatan) in mentioned


def map_event_to_sites(session: Session, case_id: str) -> dict[str, Any]:
    """R-01 result for a case: its candidate sites, and whether the signals name the site's district."""
    case = load_case(session, case_id)
    mentioned = mentioned_districts(session, case_id)
    candidates = []
    for case_site in session.scalars(select(CaseSite).where(CaseSite.case_id == case_id).order_by(CaseSite.site_id)):
        site = session.get(SupplierSite, case_site.site_id)
        candidates.append({
            "site_id": site.site_id, "tier": site.tier, "kabupaten": site.kabupaten, "kecamatan": site.kecamatan,
            "country": site.country, "ttr_days": site.ttr_days,
            "kecamatan_mentioned": district_mentioned(case, site, mentioned),
            "hazard_zone": site.hazard_zone_flood,
        })
    return {"case_id": case_id, "scope": case.scope, "candidates": candidates}


# --- TL-08 -------------------------------------------------------------------------------------------------------

def compute_impact(
    session: Session, erp: ErpData, case_id: str, d0: date, *, persist: bool, trigger: str | None, now: datetime
) -> dict[str, Any]:
    """G.1 impact of the case now. With `persist` a new snapshot version is stored (with its priority rows and alert).

    `initial` on a case that already has its first snapshot (created with the case) returns that snapshot.
    """
    case = load_case(session, case_id)
    latest = latest_snapshot(session, case_id)
    if not persist:
        impact = case_impact(session, load_case_inputs(session, case), erp, case, d0)
        return replace(impact, version=latest.version if latest else 1).to_dict()
    if trigger not in SNAPSHOT_TRIGGERS:
        raise ApiError(400, "VALIDATION_FAILED", f"trigger is required when persist is true: {list(SNAPSHOT_TRIGGERS)}",
                       {"field": "trigger"})
    if trigger == "initial" and latest is not None:
        first = session.get(ImpactSnapshot, (case_id, 1))
        return first.payload
    version = (latest.version if latest else 0) + 1
    persist_evaluation(session, case, evaluate_case(session, erp, case, d0), version=version, trigger=trigger, now=now)
    return session.get(ImpactSnapshot, (case_id, version)).payload


# --- TL-09 -------------------------------------------------------------------------------------------------------

def priority_out(session: Session, case_id: str, version: int) -> list[PriorityRowOut]:
    rows = sorted(session.scalars(select(PriorityRow).where(PriorityRow.case_id == case_id, PriorityRow.version == version)),
                  key=lambda r: (-r.score, r.site_id))
    names = {s.site_id: s.supplier_name for s in session.scalars(select(SupplierSite))}
    return [
        PriorityRowOut(
            site_id=r.site_id, supplier_name=names[r.site_id], tier=r.tier, recovery_days=r.recovery_days, rar=r.rar,
            output_value=r.output_value, po_value=r.po_value, bcp_score=r.bcp_score, bcp_band=bcp_band(r.bcp_score),
            components=r.components, score=r.score,
        )
        for r in rows
    ]


_SCORED_LEVEL = re.compile(r"level=(HIGH|MEDIUM|LOW)")


def _last_scored_level(session: Session, case_id: str) -> str | None:
    """The level TL-09 reported last time for this case, kept in its `score_case` trace (None before the first call)."""
    summary = session.scalar(
        select(AgentTrace.output_summary)
        .where(AgentTrace.case_id == case_id, AgentTrace.step == "score_case")
        .order_by(AgentTrace.id.desc()).limit(1)
    )
    found = _SCORED_LEVEL.search(summary or "")
    return found.group(1) if found else None


def score_case(session: Session, case_id: str, *, run_id: str | None, now: datetime) -> dict[str, Any]:
    """R-07b / R-08 of the latest snapshot: updates the case and its alert; a level different from the one reported
    last time (or the first report) is `level_changed` and is written to the trace."""
    case = load_case(session, case_id)
    snapshot = require_snapshot(session, case_id)
    rows = priority_out(session, case_id, snapshot.version)
    score = max((row.score for row in rows), default=0.0)
    level = score_level(score)
    previous = _last_scored_level(session, case_id)
    changed = previous != level
    case.score, case.level, case.updated_at = score, level, now
    totals = snapshot.payload["totals"]
    alert = upsert_alert(session, case, level=level, score=score, units=totals["units_blocked"], rar=totals["rar"], now=now)
    if changed:
        add_trace(
            session, run_id=run_id, case_id=case_id, task=None, step="score_case", tool="score_case",
            input_summary=f"version {snapshot.version}", output_summary=f"level={level}; score {score}",
            decision=f"Level {previous or 'none'} -> {level}", duration_ms=None, status="OK", now=now,
        )
    return {
        "case_id": case_id, "version": snapshot.version, "case_score": score, "level": level, "level_changed": changed,
        "alert_id": alert.alert_id, "priority": [row.model_dump() for row in rows],
    }


# --- TL-12 / TL-14 -----------------------------------------------------------------------------------------------

def message_view(m: SupplierMessage) -> dict[str, Any]:
    """A supplier message as EP-14 shows it."""
    return {"message_id": m.id, "case_id": m.case_id, "site_id": m.site_id, "direction": m.direction, "body": m.body,
            "structured": m.structured, "created_at": iso(m.created_at), "simulated": m.simulated,
            "request_id": m.request_id, "request_type": m.request_type}


def request_view(r: SupplierRequest) -> dict[str, Any]:
    """A supplier request as TL-12 `requests` shows it."""
    return {"request_id": r.request_id, "site_id": r.site_id, "request_type": r.request_type, "status": r.status,
            "answer": r.answer, "requested_at": iso(r.requested_at), "answered_at": iso_or_none(r.answered_at)}


def supplier_messages(session: Session, case_id: str, site_id: str | None) -> dict[str, Any]:
    load_case(session, case_id)
    query = select(SupplierMessage).where(SupplierMessage.case_id == case_id).order_by(SupplierMessage.created_at, SupplierMessage.id)
    if site_id is not None:
        query = query.where(SupplierMessage.site_id == site_id)
    messages = [message_view(m) for m in session.scalars(query)]
    case_sites = select(CaseSite).where(CaseSite.case_id == case_id).order_by(CaseSite.site_id)
    requests = select(SupplierRequest).where(SupplierRequest.case_id == case_id).order_by(SupplierRequest.request_id)
    if site_id is not None:
        case_sites = case_sites.where(CaseSite.site_id == site_id)
        requests = requests.where(SupplierRequest.site_id == site_id)
    applied = [
        {"site_id": cs.site_id, "delay_override_days": cs.delay_override_days, "excluded": cs.excluded,
         "backup_ramp_confirmed": cs.backup_ramp_confirmed, "backup_declined": cs.backup_declined,
         "verification_status": cs.verification_status}
        for cs in session.scalars(case_sites)
        if cs.delay_override_days is not None or cs.excluded or cs.backup_ramp_confirmed is not None or cs.backup_declined
    ]
    return {"messages": messages, "applied": applied, "requests": [request_view(r) for r in session.scalars(requests)]}


def alternate_sources(session: Session, part_ids: list[str]) -> dict[str, Any]:
    found = list(session.scalars(select(AlternateSource).where(AlternateSource.part_id.in_(part_ids))))
    order = {part: i for i, part in enumerate(dict.fromkeys(part_ids))}
    found.sort(key=lambda s: (order[s.part_id], s.site_id))
    have = {s.part_id for s in found}
    return {
        "sources": [{"part_id": s.part_id, "site_id": s.site_id, "price": s.price, "lead_time_days": s.lead_time_days,
                     "max_qty": s.max_qty} for s in found],
        "missing": [part for part in order if part not in have],
    }


# --- TL-15 -------------------------------------------------------------------------------------------------------

def simulate(session: Session, erp: ErpData, case_id: str, actions: list[dict[str, Any]], d0: date) -> dict[str, Any]:
    """Before / After of a hypothetical plan. Nothing is written."""
    case = load_case(session, case_id)
    inputs = load_case_inputs(session, case)
    latest = latest_snapshot(session, case_id)
    plan = [PlanAction(a["type"], a["payload"]) for a in actions]
    try:
        result = simulate_plan(erp, plan, lambda state: case_impact(session, inputs, state, case, d0))
    except PlanError as error:
        raise ApiError(400, "VALIDATION_FAILED", error.args[0], {"field": f"actions[{error.index}]"}) from None
    return replace(result, impact_after=replace(result.impact_after, version=latest.version if latest else 1)).to_dict()


# --- TL-21 -------------------------------------------------------------------------------------------------------

def add_trace(
    session: Session, *, run_id: str | None, case_id: str | None, task: str | None, step: str, tool: str | None,
    input_summary: str | None, output_summary: str | None, decision: str | None, duration_ms: int | None, status: str,
    now: datetime,
) -> AgentTrace:
    trace = AgentTrace(
        id=next_id(session, AgentTrace.id, "TR"), case_id=case_id, run_id=run_id, task=task, step=step, tool=tool,
        input_summary=input_summary, output_summary=output_summary, decision=decision, duration_ms=duration_ms,
        status=status, created_at=now,
    )
    session.add(trace)
    session.flush()
    return trace


def log_trace(session: Session, fields: dict[str, Any], *, trace_id: str | None, now: datetime) -> str:
    """Create a trace step, or finish the one with `trace_id` (RUNNING -> final). A finished search-tool step is
    also recorded as a call of its integration (EP-26)."""
    if fields.get("case_id") is not None:
        load_case(session, fields["case_id"])
    if trace_id is None:
        trace = add_trace(session, now=now, **fields)
        was_finished = False
    else:
        trace = session.get(AgentTrace, trace_id)
        if trace is None:
            raise ApiError(404, "NOT_FOUND", f"Trace {trace_id} was not found.", {"trace_id": trace_id})
        was_finished = trace.status in FINISHED_TRACE_STATUSES
        for key, value in fields.items():
            if value is not None:
                setattr(trace, key, value)
    if not was_finished:
        message = None
        if FINISHED_TRACE_STATUSES.get(trace.status) == "FAILED" and trace.output_summary:
            message = trace.output_summary[:MESSAGE_LIMIT]
        record_trace_integration(session, tool=trace.tool, trace_status=trace.status, duration_ms=trace.duration_ms,
                                 message=message, now=now)
    session.flush()
    return trace.id
