"""Graph ``resume_case`` — lanjutan kasus setelah balasan supplier atau eksekusi ERP (PRD 7.2; A-10).

    load_case → apply_replies → compute_impact → score → recommend → request_option_confirmation → meeting_room_check
      → report_refresh → END

Alur lurus, tanpa cabang: setiap pemicu selalu menghitung ulang dampak dan skor dari data terbaru.

Aturan yang dijaga di sini:
- R-13 (``delay_override_days`` / ``excluded`` / ``CONFIRMED_BY_SUPPLIER``) sudah diterapkan server saat balasan masuk.
  ``apply_replies`` hanya MEMBACA hasilnya (TL-12) dan mencatatnya di trace; agen tidak menghitung ulang aturan itu.
- ``compute_impact`` menyimpan snapshot baru (TL-08, ``persist=true``). Pemicunya ``supplier_reply`` atau ``erp_execution``;
  dipilih fungsi Python ``resolve_snapshot_trigger`` (G-06), alasannya ditulis ke ``decision`` trace ``load_case``.
- Semua cabang dan angka dari tool; tanpa percabangan ``mode`` / ``trigger`` D.2 (DEMO = LIVE, manual = auto).
- Tidak ada tool tulis ERP; hanya tool katalog TL-xx lewat ``ToolClient``.

``score``, ``recommend``, dan ``report_refresh`` adalah node ``analyze_case`` yang sama; ``request_option_confirmation`` dan
``meeting_room_check`` (A-16: sesudah ``recommend``, karena tingkat eskalasi R-22 bergantung pada opsi yang direkomendasikan) berasal
dari ``graphs/common.py``. ``recommend``
berasal dari ``graphs/recommend.py`` (A-11) dan memakai model yang sama dengan ``config["configurable"]["llm"]``.

Pemicu snapshot: kontrak D.2 belum punya field untuk alasan resume (``trigger`` D.2 = ``manual`` | ``auto``). Urutan keputusan:
(1) field opsional ``snapshot_trigger`` di payload, bila ada (USULAN untuk DS, belum di kontrak); (2) status kasus dari TL-17:
``EXECUTED`` berarti eksekusi ERP selesai (DATA_MODEL 3.3: ``AWAITING_APPROVAL`` → ``EXECUTED``, lalu ``resume_case``);
(3) selain itu balasan supplier, satu-satunya pemicu resume lainnya (R-12, R-17).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from graphs.analyze_case import fmt1, recommend, report_refresh, score
from graphs.common import (
    LLM_KEY, RunOutcome, RunState, escalation_clauses, escalation_data, get_tools, meeting_room_check, new_run_state,
    request_option_confirmation, run_guarded, traced_node,
)
from graphs.recommend import recommendation_summary
from tools.client import Json, ToolClient

TASK = "resume_case"
SUPPLIER_REPLY = "supplier_reply"
ERP_EXECUTION = "erp_execution"
SNAPSHOT_TRIGGERS = (SUPPLIER_REPLY, ERP_EXECUTION)  # 'initial' hanya milik analyze_case
CASE_EXECUTED = "EXECUTED"
TRIGGER_LABELS = {SUPPLIER_REPLY: "supplier replies", ERP_EXECUTION: "ERP execution"}


class ResumeCaseState(RunState, total=False):
    payload_snapshot_trigger: str | None  # snapshot_trigger dari payload (opsional)
    scope: str | None
    case_status: str | None
    snapshot_trigger: str  # supplier_reply | erp_execution, dipilih load_case
    applied_replies: list[Json]
    awaiting_sites: list[str]
    preliminary: bool
    impact_version: int | None
    impact_totals: Json
    case_score: float
    level: str
    alert_id: str | None
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


def payload_snapshot_trigger(payload: Mapping[str, Any]) -> str | None:
    """``snapshot_trigger`` opsional dari payload; nilai selain ``supplier_reply`` / ``erp_execution`` → ``ValueError``."""
    value = payload.get("snapshot_trigger")
    if value is None:
        return None
    if value not in SNAPSHOT_TRIGGERS:
        raise ValueError(f"snapshot_trigger must be one of {', '.join(SNAPSHOT_TRIGGERS)}, got {value!r}")
    return value


def resolve_snapshot_trigger(requested: str | None, case_status: str | None) -> tuple[str, str]:
    """(pemicu snapshot, alasan untuk ``decision`` trace). Fungsi murni (G-06): payload dulu, lalu status kasus."""
    if requested:
        return requested, f"payload snapshot_trigger={requested}"
    shown = case_status or "unknown"
    if case_status == CASE_EXECUTED:
        return ERP_EXECUTION, f"case status {shown} → ERP execution finished → snapshot trigger {ERP_EXECUTION}"
    return SUPPLIER_REPLY, f"case status {shown} → supplier reply → snapshot trigger {SUPPLIER_REPLY}"


def awaiting_sites(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """Site yang diminta konfirmasi (pesan ``OUT``) tetapi belum membalas (tanpa pesan ``IN``), urut permintaan."""
    answered = {m.get("site_id") for m in messages if m.get("direction") == "IN"}
    asked = dict.fromkeys(m["site_id"] for m in messages if m.get("direction") == "OUT" and m.get("site_id"))
    return [site for site in asked if site not in answered]


def awaiting_sites_from_requests(requests: Sequence[Mapping[str, Any]]) -> list[str]:
    """Site dengan permintaan ``REQUESTED`` (jenis apa pun, TL-12 v1.11 ``requests``), urut permintaan, tanpa duplikat."""
    return list(dict.fromkeys(r["site_id"] for r in requests if r.get("status") == "REQUESTED" and r.get("site_id")))


def _option_reply_only(item: Mapping[str, Any]) -> bool:
    """Entri ``applied`` yang hanya membawa jawaban konfirmasi opsi (R-13 v2): diuraikan lewat ``requests``, bukan sebagai balasan IMPACT."""
    option_fields = item.get("backup_ramp_confirmed") is not None or bool(item.get("backup_declined"))
    return option_fields and item.get("delay_override_days") is None and not item.get("excluded")


def _describe_reply(item: Mapping[str, Any]) -> str:
    site = item["site_id"]
    if item.get("excluded"):
        return f"{site} excluded (supplier says not affected)"
    days = item.get("delay_override_days")
    return f"{site} delay {days} days" if days is not None else f"{site} confirmed"


def _describe_request(request: Mapping[str, Any], applied: Sequence[Mapping[str, Any]]) -> str | None:
    """`S19 IMPACT no response`; jawaban `BACKUP_ACTIVATION` / `PULL_IN` (`S06 BACKUP_ACTIVATION YES (ramp 21 days)`). Balasan IMPACT
    yang dijawab sudah diuraikan lewat ``applied`` (``_describe_reply``)."""
    site, kind, status = request.get("site_id"), request.get("request_type"), request.get("status")
    if status == "NO_RESPONSE":
        return f"{site} {kind} no response"
    if status == "ANSWERED" and kind in ("BACKUP_ACTIVATION", "PULL_IN"):
        text = f"{site} {kind} {request.get('answer')}"
        ramp = next((a.get("backup_ramp_confirmed") for a in applied if a.get("site_id") == site), None)
        if kind == "BACKUP_ACTIVATION" and request.get("answer") == "YES" and ramp is not None:
            text += f" (ramp {ramp} days)"
        return text
    return None


# --------------------------------------------------------------------------- node


@traced_node(step="load_case", tool="get_case")
def load_case(state: ResumeCaseState, config: RunnableConfig) -> dict[str, Any]:
    case = get_tools(config).get_case(_case_id(state))
    trigger, why = resolve_snapshot_trigger(state.get("payload_snapshot_trigger"), case.get("status"))
    return {
        "scope": case.get("scope"),
        "case_status": case.get("status"),
        "snapshot_trigger": trigger,
        "output_summary": f"status={case.get('status')}; scope={case.get('scope')}",
        "decision": why,
    }


@traced_node(step="apply_replies", tool="get_supplier_messages")
def apply_replies(state: ResumeCaseState, config: RunnableConfig) -> dict[str, Any]:
    """Membaca balasan supplier dan hasil R-13 v2 dari server (TL-12 v1.11). Tidak menulis apa pun.

    Menunggu = site dengan permintaan ``REQUESTED`` (jenis apa pun); backend tanpa ``requests`` memakai pesan OUT tanpa IN."""
    result = get_tools(config).get_supplier_messages(_case_id(state))
    applied = list(result.get("applied") or [])
    requests = result.get("requests")
    waiting = (awaiting_sites(result.get("messages") or []) if requests is None
               else awaiting_sites_from_requests(requests))

    parts = [_describe_reply(item) for item in applied if not _option_reply_only(item)]
    parts += [text for text in (_describe_request(r, applied) for r in requests or []) if text]
    described = "; ".join(parts)
    summary = f"{len(applied)} replies applied" + (f": {described}" if described else "")
    if waiting:
        summary += ("; " if described else ": ") + f"still awaiting {', '.join(waiting)}"
    decision = (f"{described} (R-13, applied by the server) → recompute impact" if described
                else "no supplier reply applied → recompute impact from current data")
    return {
        "applied_replies": applied,
        "awaiting_sites": waiting,
        "preliminary": bool(waiting),
        "input_summary": f"case {_case_id(state)}",
        "output_summary": summary,
        "decision": decision,
    }


@traced_node(step="compute_impact", tool="compute_impact")
def compute_impact(state: ResumeCaseState, config: RunnableConfig) -> dict[str, Any]:
    trigger = state["snapshot_trigger"]
    impact = get_tools(config).compute_impact(_case_id(state), persist=True, trigger=trigger)  # type: ignore[arg-type]
    totals = impact.get("totals") or {}
    version = impact.get("version")
    return {
        "impact_version": version,
        "impact_totals": totals,
        "input_summary": f"persist=true; trigger={trigger}",
        "output_summary": (f"version={version}; units_blocked={totals.get('units_blocked')}; "
                           f"rar={totals.get('rar')}; po_value={totals.get('po_value')}"),
    }


def build_graph() -> Any:
    g = StateGraph(ResumeCaseState)
    nodes = (load_case, apply_replies, compute_impact, score, recommend, request_option_confirmation, meeting_room_check,
             report_refresh)
    for node in nodes:
        g.add_node(node.step, node)
    g.add_edge(START, nodes[0].step)
    for a, b in zip(nodes, nodes[1:]):
        g.add_edge(a.step, b.step)
    g.add_edge(nodes[-1].step, END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run → ``status`` / ``summary`` / ``data`` kontrak D.3 (semua angka dari state hasil tool)."""
    s = outcome.state
    data: Json = {key: s[key] for key in ("case_score", "level", "recommended_option") if s.get(key) is not None}
    data.update(escalation_data(s))

    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": data}

    parts = []
    if s.get("impact_version") is not None:
        label = TRIGGER_LABELS.get(s.get("snapshot_trigger"), "new data")
        blocked = (s.get("impact_totals") or {}).get("units_blocked")
        detail = f"version {s['impact_version']}" + (f", {blocked} units blocked" if blocked is not None else "")
        parts.append(f"Impact recalculated after {label} ({detail})")
    if s.get("case_score") is not None:
        parts.append(f"case scored {fmt1(s['case_score'])} ({s.get('level')})")
    if s.get("awaiting_sites"):
        parts.append(f"still awaiting reply from {', '.join(s['awaiting_sites'])}")
    parts.append(recommendation_summary(s))
    parts.extend(escalation_clauses(s))
    return {"status": "OK", "summary": "; ".join(parts) + ".", "data": data}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None) -> Json:
    """Jalankan ``resume_case`` untuk payload D.2. ``tools`` / ``llm`` hanya diisi tes."""
    _case_id(payload)
    requested = payload_snapshot_trigger(payload)  # payload tidak sah ditolak sebelum tool apa pun dipanggil
    tools = tools or ToolClient.from_payload(payload)
    initial = {**new_run_state(payload), "payload_snapshot_trigger": requested}
    outcome = run_guarded(build_graph(), initial, tools, configurable={LLM_KEY: llm} if llm is not None else None)
    return build_response(outcome)
