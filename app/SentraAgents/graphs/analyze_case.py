"""Graph ``analyze_case`` — orkestrator kasus (PRD 7.2, 7.7; A-07).

    load_case → schedule_check → map_network → compute_impact → score → branch_scope
      branch_scope --LOCATION--> assess_confidence --ada K < ambang--> request_verification → recommend
                                                   \\--semua lolos----------------------------> recommend
      branch_scope --COUNTRY---> recommend
    recommend → request_option_confirmation → meeting_room_check → report_refresh → END

v3 (A-16, DEC-038, DEC-039): ``meeting_room_check`` pindah ke SESUDAH ``recommend`` karena tingkat eskalasi R-22 bergantung pada opsi yang
direkomendasikan; ``request_option_confirmation`` meminta konfirmasi supplier untuk Opsi C (``BACKUP_ACTIVATION``) / Opsi E (``PULL_IN``).
Keduanya node ``graphs/common.py`` (dipakai juga ``resume_case``) yang hanya meneruskan keputusan server (TL-23, TL-28).

Aturan yang dijaga di sini:
- Semua cabang ditentukan fungsi Python dari hasil tool (G-06), bukan LLM. Alasan cabang ditulis ke ``decision``
  trace oleh node yang menghitungnya (``branch_scope``, ``assess_confidence``, ``request_verification``);
  fungsi ``route_*`` hanya membaca state hasil node itu. Ambang keyakinan dihitung server
  (``needs_verification`` dari TL-10); agen tidak menyimpan angka ambang.
- LLM hanya dipakai di ``schedule_check`` (ringkasan ≤ 60 kata + validasi angka G-04).
- Tidak ada percabangan ``mode`` / ``trigger`` (DEMO = LIVE, manual = auto).
- Tidak ada tool tulis ERP; hanya tool katalog TL-xx lewat ``ToolClient``.

``recommend`` adalah node dari ``graphs/recommend.py`` (A-11: opsi dari TL-23, alasan oleh agen ReAct, TL-16, G-04/G-06);
``resume_case`` (A-10) memakai node yang sama. LLM juga dipakai di sana, jadi model yang diinjeksi harus mendukung ``bind_tools``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from graphs.common import (
    LLM_KEY, RunOutcome, RunState, escalation_clauses, escalation_data, get_llm, get_tools, meeting_room_check, new_run_state,
    request_option_confirmation, run_guarded, traced_node,
)
from graphs.recommend import recommend, recommendation_summary
from graphs.schedule import summarize_schedule
from tools.client import Json, ToolClient

TASK = "analyze_case"
SCOPE_LOCATION = "LOCATION"
SCOPE_COUNTRY = "COUNTRY"


class AnalyzeCaseState(RunState, total=False):
    scope: str | None
    schedule_summary: str
    schedule_summary_source: str  # "llm" | "template"
    candidate_sites: list[str]
    impact_version: int | None
    impact_totals: Json
    case_score: float
    level: str
    alert_id: str | None
    branch: str  # nama node berikutnya, ditulis branch_scope
    verification_sites: list[str]  # site dengan needs_verification (TL-10)
    verification_requested: list[str]
    preliminary: bool
    recommendation_status: str
    recommended_option: str | None
    rule_reason: str | None
    confirmations_needed: list[Json]  # dari recommend (TL-23 v1.11)
    confirmations_requested: list[Json]  # request_option_confirmation: [{"site_id", "request_type"}]
    escalation_tier: int | None  # TL-23, lalu TL-28 (meeting_room_check)
    meeting_status: str  # CREATED | NOT_NEEDED | SKIPPED_FEATURE_OFF | ERROR
    rationale_options: list[str]
    validator_fallback: bool
    report_version: int | None
    decision: str


def _case_id(state: Mapping[str, Any]) -> str:
    case_id = state.get("case_id")
    if not case_id:
        raise ValueError(f"{TASK} requires case_id")
    return case_id


def fmt1(value: float) -> str:
    """Angka 1 desimal, half-up (R-20)."""
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- node


@traced_node(step="load_case", tool="get_case")
def load_case(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    case = get_tools(config).get_case(_case_id(state))
    return {"scope": case.get("scope"), "output_summary": f"status={case.get('status')}; scope={case.get('scope')}"}


@traced_node(step="schedule_check", tool="get_po_schedule")
def schedule_check(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    result = summarize_schedule(get_tools(config), lambda: get_llm(config))
    return {
        "schedule_summary": result.text,
        "schedule_summary_source": result.source,
        "output_summary": result.text,  # EP-04 agent_summary membaca ini
        "decision": result.note or "summary passed number validation (G-04)",
    }


@traced_node(step="map_network", tool="map_event_to_sites")
def map_network(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    mapped = get_tools(config).map_event_to_sites(_case_id(state))
    sites = [c["site_id"] for c in mapped.get("candidates") or []]
    return {
        "scope": mapped["scope"],
        "candidate_sites": sites,
        "input_summary": f"case {_case_id(state)}",
        "output_summary": f"scope={mapped['scope']}; {len(sites)} candidate sites: {', '.join(sites) or '-'}",
    }


@traced_node(step="compute_impact", tool="compute_impact")
def compute_impact(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    impact = get_tools(config).compute_impact(_case_id(state), persist=True, trigger="initial")
    totals = impact.get("totals") or {}
    version = impact.get("version")
    return {
        "impact_version": version,
        "impact_totals": totals,
        "output_summary": (f"version={version}; units_blocked={totals.get('units_blocked')}; "
                           f"rar={totals.get('rar')}; po_value={totals.get('po_value')}"),
    }


@traced_node(step="score", tool="score_case")
def score(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    scored = get_tools(config).score_case(_case_id(state))
    changed = " (level changed)" if scored.get("level_changed") else ""
    return {
        "case_score": scored["case_score"],
        "level": scored["level"],
        "alert_id": scored.get("alert_id"),
        "decision": f"case score {fmt1(scored['case_score'])} → {scored['level']}{changed}",
    }


@traced_node(step="branch_scope")
def branch_scope(state: AnalyzeCaseState) -> dict[str, Any]:
    scope = state.get("scope")
    if scope == SCOPE_LOCATION:
        branch, why = "assess_confidence", "location event → assess supplier confidence"
    elif scope == SCOPE_COUNTRY:
        branch, why = "recommend", "country-wide event → skip location confidence, go to recommendation"
    else:
        raise ValueError(f"unknown scope {scope!r}; expected {SCOPE_LOCATION} or {SCOPE_COUNTRY}")
    return {"branch": branch, "decision": f"scope={scope} → {branch} ({why})", "output_summary": branch}


@traced_node(step="assess_confidence", tool="assess_confidence")
def assess_confidence(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    result = get_tools(config).assess_confidence(_case_id(state))
    flagged = [s for s in result.get("sites") or [] if s.get("needs_verification")]
    sites = [s["site_id"] for s in flagged]
    if flagged:
        reasons = "; ".join(s.get("reason") or f"{s['site_id']} below confidence threshold" for s in flagged)
        decision = f"{reasons} → request supplier confirmation"
    else:
        decision = "no K_A/K_B below threshold → no supplier confirmation needed"
    return {
        "verification_sites": sites,
        "decision": decision,
        "input_summary": f"{len(state.get('candidate_sites') or [])} candidate sites",
        "output_summary": f"{len(result.get('sites') or [])} sites assessed; needs verification: {', '.join(sites) or '-'}",
    }


@traced_node(step="request_verification", tool="request_supplier_confirmation")
def request_verification(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    wanted = list(state.get("verification_sites") or [])
    result = get_tools(config).request_supplier_confirmation(_case_id(state), wanted)
    asked = {r["site_id"] for r in result.get("requested") or []}
    asked |= {s["site_id"] for s in result.get("skipped") or [] if s.get("reason") == "ALREADY_REQUESTED"}
    requested = [site for site in wanted if site in asked]
    return {
        "verification_requested": requested,
        "preliminary": True,
        "decision": f"confirmation requested from {', '.join(requested) or '-'} → recommendation is PRELIMINARY",
        "input_summary": f"sites: {', '.join(wanted)}",
    }


@traced_node(step="report_refresh", tool="get_report_data")
def report_refresh(state: AnalyzeCaseState, config: RunnableConfig) -> dict[str, Any]:
    """Memastikan data laporan + grafik (dihitung server) tersedia untuk versi terbaru. Narasi = graph generate_report."""
    report = get_tools(config).get_report_data(_case_id(state))
    return {"report_version": report.get("version"), "output_summary": f"report data version={report.get('version')}"}


# --------------------------------------------------------------------------- cabang (fungsi Python, G-06)


def route_after_scope(state: AnalyzeCaseState) -> str:
    return state["branch"]


def route_after_confidence(state: AnalyzeCaseState) -> str:
    return "request_verification" if state.get("verification_sites") else "recommend"


def build_graph() -> Any:
    g = StateGraph(AnalyzeCaseState)
    for node in (load_case, schedule_check, map_network, compute_impact, score, branch_scope, assess_confidence,
                 request_verification, recommend, request_option_confirmation, meeting_room_check, report_refresh):
        g.add_node(node.step, node)

    g.add_edge(START, "load_case")
    for a, b in (("load_case", "schedule_check"), ("schedule_check", "map_network"),
                 ("map_network", "compute_impact"), ("compute_impact", "score"), ("score", "branch_scope")):
        g.add_edge(a, b)
    g.add_conditional_edges("branch_scope", route_after_scope,
                            {"assess_confidence": "assess_confidence", "recommend": "recommend"})
    g.add_conditional_edges("assess_confidence", route_after_confidence,
                            {"request_verification": "request_verification", "recommend": "recommend"})
    g.add_edge("request_verification", "recommend")
    g.add_edge("recommend", "request_option_confirmation")
    g.add_edge("request_option_confirmation", "meeting_room_check")
    g.add_edge("meeting_room_check", "report_refresh")
    g.add_edge("report_refresh", END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run → ``status`` / ``summary`` / ``data`` kontrak D.3 (semua angka dari state hasil tool)."""
    s = outcome.state
    data: Json = {}
    for key in ("case_score", "level", "verification_requested", "recommended_option"):
        if s.get(key) is not None:
            data[key] = s[key]
    data.update(escalation_data(s))

    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": data}

    parts = []
    if s.get("case_score") is not None:
        parts.append(f"Case scored {fmt1(s['case_score'])} ({s.get('level')})")
    if s.get("verification_requested"):
        parts.append(f"supplier confirmation requested from {', '.join(s['verification_requested'])}")
    elif s.get("scope") == SCOPE_LOCATION:
        parts.append("no supplier confirmation needed")
    parts.append(recommendation_summary(s))
    parts.extend(escalation_clauses(s))
    return {"status": "OK", "summary": "; ".join(parts) + ".", "data": data}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None) -> Json:
    """Jalankan ``analyze_case`` untuk payload D.2. ``tools`` / ``llm`` hanya diisi tes."""
    _case_id(payload)
    tools = tools or ToolClient.from_payload(payload)
    outcome = run_guarded(build_graph(), new_run_state(payload), tools,
                          configurable={LLM_KEY: llm} if llm is not None else None)
    return build_response(outcome)
