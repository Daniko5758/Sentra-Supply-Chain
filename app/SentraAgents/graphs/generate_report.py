"""Graph ``generate_report`` — Report Agent (PRD 7.2, 7.5; FR-15; A-12).

    load_case → get_report_data → write_report → END                  (task ``generate_report``)

Pembagian tugas (G-02, G-04, G-06):
- Data laporan (KPI, sinyal, dampak, verifikasi, jadwal, rekomendasi, aksi) dan GRAFIK datang dari TL-19, dihitung server dari
  snapshot terbaru. Agen tidak menghitung angka dan tidak membuat grafik; satu-satunya deret grafik yang dibaca LLM adalah riwayat
  revenue at risk per versi (masukan bagian ``supplier_verification``).
- LLM menulis ISI satu bagian FR-15 per panggilan: delapan bagian, urutan dan judul ``## ...`` dari kode, masukan hanya potongan
  JSON bagian itu. Ringkasan eksekutif ≤ 80 kata (``fit_words``). URL sumber TIDAK ditulis LLM: kode menambahkannya dari
  ``signal.source_urls`` (G-02); teks yang memuat URL ditolak.
- v1.11 (A-17): bila TL-19 membawa ``escalation``, bagian kesembilan ``escalation`` (sesudah ``recommended_plan``) ikut. Tingkat ≥ 1:
  LLM menulis dari potongan ``escalation_slice`` (alasan, peserta, approver, status rapat, keputusan; TANPA ``join_url``; teks yang
  memuat URL ditolak). Tingkat 0: kalimat tetap ``NO_MEETING_SENTENCE`` dari kode, tanpa panggilan LLM, tidak dihitung "ditulis".
  Backend tanpa ``escalation`` di TL-19: tetap delapan bagian.
- ``save_report`` (TL-20) mengirim narasi utuh; validator G-04 berjalan di server atas data TL-19. ``accepted = false`` → satu kali
  ulang: bagian yang memuat angka bermasalah ditulis ulang dengan ``offending_numbers`` sebagai koreksi, lalu narasi dikirim lagi.
  Gagal kedua → server memakai template (``fallback_template_used``).
- G-06: teks yang menganjurkan opsi lain daripada ``recommended_option`` (atau menolaknya) dibuang dengan ``contradictions`` dari
  ``graphs/recommend.py`` (fungsi Python) dan jejak mencatat ``LLM_OVERRIDDEN``; bagian itu ditulis ulang sekali.
- Kejujuran data (CLAUDE.md 6.8): bagian yang memuat item simulasi harus memuat "SIMULATED"; bila LLM lupa, kode menambah satu baris
  catatan. Bagian ``action_status`` tanpa aksi selalu menyebut "not executed"; ``appendix`` selalu menyebut Mock SAP S/4HANA.
- G-03: teks dari luar (ringkasan sinyal yang berasal dari berita, isi balasan supplier) dibungkus ``<external_content>``.

Bagian yang gagal ditulis (model error, ditolak dua kali) diganti satu baris keterangan; bila tidak satu bagian pun jadi,
``save_report`` tidak dipanggil dan run berstatus ``ERROR`` (tidak ada laporan tersimpan).

Paralel (A-15, DEC-016): bagian tidak saling bergantung (masing-masing hanya membaca potongan TL-19-nya), jadi ditulis bersamaan (maks
``MAX_PARALLEL_SECTIONS`` thread, satu panggilan LLM per bagian). Narasi dirakit menurut urutan bagian, dan jejak ``LLM_OVERRIDDEN``
ditahan per bagian lalu ditulis berurutan, sehingga hasil dan jejak sama dengan versi serial. Tidak ada percabangan ``mode`` / ``trigger`` (DEMO = LIVE, manual = auto) dan
tidak ada tool tulis ERP.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from graphs.common import (
    LLM_KEY, LLM_MAX_PARALLEL, AgentTimeout, RunOutcome, RunState, budget_exceeded, correction_instruction, get_llm,
    get_tools, map_parallel, message_text, new_run_state, run_guarded, trace_event, traced_node,
)
from graphs.recommend import contradictions
from graphs.schedule import fit_words
from prompts import load_prompt
from tools.client import Json, ToolClient

log = logging.getLogger(__name__)

TASK = "generate_report"
STEP = "write_report"
PROMPT = "report"
MAX_SUMMARY_WORDS = 80  # FR-15, PRD 7.5
MAX_PARALLEL_SECTIONS = LLM_MAX_PARALLEL  # A-15: bagian ditulis bersamaan; 1 = serial
HARD_LIMIT_FACTOR = 2  # bagian selain ringkasan: ditolak bila > 2 × max_words (model sering melebihi target)

# Status ``report_status`` (state)
SAVED = "SAVED"  # narasi LLM tersimpan
TEMPLATE = "TEMPLATE"  # validator G-04 gagal dua kali; server menyimpan template
NO_NARRATIVE = "NO_NARRATIVE"  # tidak satu bagian pun punya teks yang boleh dikirim; TL-20 tidak dipanggil
REJECTED = "REJECTED"  # server menolak dua kali (di luar kontrak TL-20)

PLACEHOLDER = "Not available: the narrative for this section could not be generated."
SIMULATED = "SIMULATED"
SIMULATED_NOTE = "Note: this section covers SIMULATED items."
NOT_EXECUTED_TOKEN = "not executed"
NOT_EXECUTED_LINE = "Proposed actions — not executed."
ERP_TOKEN = "Mock SAP S/4HANA"
ERP_LINE = "ERP data: Mock SAP S/4HANA — SIMULATED."


# --------------------------------------------------------------------------- bagian FR-15

ESCALATION_ID = "escalation"  # v1.11 (DEC-039): sesudah recommended_plan; ada bila TL-19 membawa ``escalation``
NO_MEETING_SENTENCE = "No meeting was needed: the case is handled in the app."  # tingkat 0: kalimat tetap dari kode, tanpa LLM


@dataclass(frozen=True)
class SectionSpec:
    id: str  # sama dengan ``sections`` EP-17
    heading: str
    brief: str  # untuk LLM (English)
    max_words: int
    strict: bool = False  # ringkasan: lebih dari max_words dipotong per kalimat

    @property
    def hard_words(self) -> int:
        return self.max_words if self.strict else self.max_words * HARD_LIMIT_FACTOR


REPORT_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "executive_summary", "Executive summary",
        "One paragraph for an executive: the risk level and priority score, units at risk, revenue at risk, whether the "
        "view is preliminary, and the option the rules recommend, with the reason in your own words.",
        MAX_SUMMARY_WORDS, strict=True),
    SectionSpec(
        "what_happened", "What happened",
        "Two or three sentences on the signal that opened the case: what happened, where, and how severe it is. "
        "Do not write source links; the system adds them.", 100),
    SectionSpec(
        "impact", "Impact",
        "The key figures: units at risk, delayed output value, affected PO value and revenue at risk. Then the parts "
        "that arrive late (days late), the blocked production orders (units) and the sales orders at risk (old and new "
        "dates). If a tariff or a country exposure is present, describe it.", 160),
    SectionSpec(
        "supplier_verification", "Supplier verification",
        "One line per site: its verification status (confirmed by the supplier, waiting for the supplier, or "
        "confirmation not needed), the expected delay if the supplier gave one, and the confidence scores K_A and K_B "
        "when present (one decimal). If revenue at risk differs between versions, say how it changed.", 160),
    SectionSpec(
        "schedule_status", "Schedule status",
        "The PO schedule: how many PO lines there are, how many slipped and how many are on time, and how many "
        "production orders are covered or short of parts.", 80),
    SectionSpec(
        "recommended_plan", "Recommended plan",
        "Each available option: what it changes (units blocked and revenue at risk, before and after) and what it "
        "costs; which option the rules recommend and why (rule_reason); whether the recommendation is preliminary; "
        "and the steps outside the ERP.", 200),
    SectionSpec(
        ESCALATION_ID, "Escalation",
        "The escalation tier and kind (Meeting Room or Executive Meeting Room), the reasons (reasons[].text exactly as "
        "written), who takes part and who must approve, the meeting (start time and status; never write a link), and "
        "the meeting decision if there is one (option, approved_by, budget_approved).", 120),
    SectionSpec(
        "action_status", "Action status",
        "If actions is empty, say \"Proposed actions — not executed\": the options are proposals and nothing has been "
        "changed in the ERP. Otherwise list each action with its type and status and the ERP document, if any.", 100),
    SectionSpec(
        "appendix", "Appendix: sources and assumptions",
        "Three to five short bullets on the assumptions: the figures come from the impact snapshot of the report "
        "version, calculated by SENTRA rules and not by a language model; revenue at risk is the delayed output value "
        "plus the affected PO value, net of shortages that exist without the event; ERP data comes from Mock SAP "
        "S/4HANA — SIMULATED.", 100),
)
SECTION_IDS = tuple(spec.id for spec in REPORT_SECTIONS)


class GenerateReportState(RunState, total=False):
    scope: str | None
    case_status: str | None
    case_simulated: bool
    report: Json
    report_version: int
    report_status: str
    sections_written: list[str]
    sections_total: int
    validator_fallback: bool
    decision: str


def _case_id(state: Mapping[str, Any]) -> str:
    case_id = state.get("case_id")
    if not case_id:
        raise ValueError(f"{TASK} requires case_id")
    return case_id


# --------------------------------------------------------------------------- masukan LLM per bagian (potongan TL-19)


def _pick(source: Any, *keys: str) -> Json:
    source = source if isinstance(source, Mapping) else {}
    return {key: source.get(key) for key in keys}


def escalation_tier(report: Mapping[str, Any]) -> int | None:
    """Tingkat R-22 dari ``report["escalation"]`` (TL-19 v1.11); ``None`` bila backend belum membawanya."""
    escalation = report.get("escalation")
    tier = escalation.get("tier") if isinstance(escalation, Mapping) else None
    return tier if isinstance(tier, int) and not isinstance(tier, bool) else None


def sections_for(report: Mapping[str, Any]) -> tuple[SectionSpec, ...]:
    """Bagian laporan run ini: delapan bagian FR-15 + ``escalation`` bila TL-19 membawa eskalasi (backend v1.11)."""
    keep_escalation = escalation_tier(report) is not None
    return tuple(spec for spec in REPORT_SECTIONS if spec.id != ESCALATION_ID or keep_escalation)


def llm_section_count(report: Mapping[str, Any]) -> int:
    """Bagian yang ditulis LLM: semua bagian run ini kecuali kalimat tetap tingkat 0 (tidak dihitung "ditulis")."""
    return len(sections_for(report)) - (1 if escalation_tier(report) == 0 else 0)


def escalation_slice(escalation: Any) -> Json:
    """Data eskalasi untuk LLM. TANPA ``join_url`` (tautan rapat tidak boleh ada di narasi), agenda, galat, catatan keputusan
    bebas, dan batas kebijakan; rapat dan keputusan hanya field yang diceritakan bagian ini."""
    escalation = escalation if isinstance(escalation, Mapping) else {}
    out = _pick(escalation, "tier", "kind", "reasons", "participants", "required_approvers", "recommended_option",
                "premium", "extra_duty", "decision_required")
    meeting, decision = escalation.get("meeting"), escalation.get("decision")
    out["meeting"] = (_pick(meeting, "tier", "kind", "provider", "status", "start_time", "duration_min")
                      if isinstance(meeting, Mapping) else None)
    out["decision"] = _pick(decision, "option", "approved_by", "budget_approved") if isinstance(decision, Mapping) else None
    return out


def section_data(section_id: str, report: Mapping[str, Any]) -> Json:
    """Potongan data TL-19 yang dibaca LLM untuk ``section_id``. Selalu memuat ``version``. Grafik tidak ikut, kecuali riwayat
    revenue at risk per versi untuk ``supplier_verification``."""
    kpi, signal = report.get("kpi"), report.get("signal")
    recommendation = report.get("recommendation") if isinstance(report.get("recommendation"), Mapping) else {}
    charts = report.get("charts") if isinstance(report.get("charts"), Mapping) else {}
    data: dict[str, Any]
    if section_id == "executive_summary":
        data = {"kpi": kpi, "signal": _pick(signal, "summary", "severity"),
                "recommendation": {k: v for k, v in recommendation.items() if k not in ("options", "non_erp_steps")}}
    elif section_id == "what_happened":
        data = {"signal": _pick(signal, "summary", "severity")}
    elif section_id == "impact":
        data = {"kpi": kpi, "impact": report.get("impact")}
    elif section_id == "supplier_verification":
        data = {"verification": report.get("verification"), "revenue_at_risk_by_version": charts.get("rar_by_version")}
    elif section_id == "schedule_status":
        data = {"schedule": report.get("schedule")}
    elif section_id == "recommended_plan":
        data = {"recommendation": recommendation}
    elif section_id == ESCALATION_ID:
        data = {"escalation": escalation_slice(report.get("escalation"))}
    elif section_id == "action_status":
        data = {"actions": report.get("actions"), "execution_enabled": recommendation.get("execution_enabled")}
    elif section_id == "appendix":
        data = {"signal": _pick(signal, "source_label")}
    else:
        raise ValueError(f"unknown report section: {section_id}")
    return {"version": report.get("version"), **data}


def _external(text: str) -> str:
    """G-03: teks dari berita / balasan supplier dibungkus supaya diperlakukan sebagai data."""
    return f"<external_content>{text.replace('</external_content>', '')}</external_content>"


def mark_external(data: Mapping[str, Any]) -> Json:
    """Salinan ``data`` dengan ringkasan sinyal dan isi balasan supplier di dalam ``<external_content>`` (G-03)."""
    out = copy.deepcopy(dict(data))
    signal = out.get("signal")
    if isinstance(signal, dict) and isinstance(signal.get("summary"), str):
        signal["summary"] = _external(signal["summary"])
    for site in out.get("verification") or []:
        reply = site.get("reply") if isinstance(site, dict) else None
        if isinstance(reply, dict) and isinstance(reply.get("body"), str):
            reply["body"] = _external(reply["body"])
    return out


def expects_simulated_mark(section_id: str, report: Mapping[str, Any], case_simulated: bool) -> bool:
    """Bagian ini memuat item simulasi (sinyal DEMO, balasan supplier skrip, rekaman Mock S/4HANA) → harus bertanda SIMULATED."""
    if section_id in ("executive_summary", "what_happened", "appendix"):
        return case_simulated
    if section_id == "supplier_verification":
        replies = [(site.get("reply") or {}) for site in report.get("verification") or [] if isinstance(site, dict)]
        return case_simulated or any(r.get("simulated") for r in replies if isinstance(r, dict))
    if section_id == "action_status":
        return bool(report.get("actions"))
    return False


def section_request(spec: SectionSpec, report: Mapping[str, Any], case_id: str, case_simulated: bool) -> str:
    """Pesan manusia untuk satu bagian: tugas bagian, identitas kasus, dan potongan data (JSON)."""
    payload = {
        "section": {"id": spec.id, "heading": spec.heading, "brief": spec.brief, "max_words": spec.max_words,
                    "mark_simulated_items": expects_simulated_mark(spec.id, report, case_simulated)},
        "case": {"case_id": case_id, "simulated": case_simulated},
        "data": mark_external(section_data(spec.id, report)),
    }
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- pemeriksaan teks (fungsi murni)

_TAGS = re.compile(r"</?external_content>")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s")
_URL = re.compile(r"(?i)https?://|\bwww\.")
_LEAK = re.compile(r"(?i)\b(?:json|mark_simulated_items|system prompt)\b")


@dataclass(frozen=True)
class Checked:
    text: str | None  # isi bagian yang boleh dipakai
    problem: str | None = None  # alasan ditolak (untuk instruksi koreksi dan catatan)
    overridden: tuple[str, ...] = ()  # pertentangan dengan aturan R-15 (G-06)


def check_text(raw: str, spec: SectionSpec, *, recommended: str | None, letters: frozenset[str],
               reserve_words: int = 0) -> Checked:
    """Isi bagian yang diterima, atau alasan penolakannya. ``reserve_words``: kata yang dicadangkan untuk catatan kode."""
    text = _TAGS.sub("", raw).strip()
    if not text:
        return Checked(None, "it is empty")
    found = contradictions(text, recommended, letters)
    if found:
        return Checked(None, f"it {'; '.join(found)}, but the recommended option is decided by rule R-15", tuple(found))
    if _HEADING.search(text):
        return Checked(None, "it contains a heading (the system adds the heading)")
    if _URL.search(text):
        return Checked(None, "it contains a URL (the system adds source links)")
    if _LEAK.search(text):
        return Checked(None, "it refers to JSON, key names or its own instructions")
    limit = spec.hard_words - (reserve_words if spec.strict else 0)
    if spec.strict:
        fitted = fit_words(text, limit)
        return Checked(fitted) if fitted else Checked(None, f"its first sentence is longer than {limit} words")
    if len(text.split()) > limit:
        return Checked(None, f"it is longer than {limit} words")
    return Checked(text)


def fix_instruction(problem: str, spec: SectionSpec, reserve_words: int = 0) -> str:
    limit = spec.hard_words - (reserve_words if spec.strict else 0)
    return (f"Your previous reply cannot be used because {problem}. Rewrite the body of the \"{spec.heading}\" section "
            f"only: no headings, no URLs, no mention of JSON, at most {limit} words, and only numbers from the provided "
            "JSON.")


def required_notes(section_id: str, *, simulated: bool, has_actions: bool) -> list[tuple[str, str]]:
    """(kata yang harus ada, baris yang ditambah kode bila tidak ada). Urutan penting: baris ERP sudah memuat SIMULATED."""
    notes: list[tuple[str, str]] = []
    if section_id == "appendix":
        notes.append((ERP_TOKEN, ERP_LINE))
    if section_id == "action_status" and not has_actions:
        notes.append((NOT_EXECUTED_TOKEN, NOT_EXECUTED_LINE))
    if simulated:
        notes.append((SIMULATED, SIMULATED_NOTE))
    return notes


def apply_notes(text: str, notes: list[tuple[str, str]]) -> str:
    for token, line in notes:
        if token.lower() not in text.lower():
            text = f"{text}\n\n{line}"
    return text


def sources_block(report: Mapping[str, Any]) -> str:
    """Sumber sinyal, ditulis kode dari data server (G-02): URL bila ada, selain itu label sumber."""
    signal = report.get("signal") if isinstance(report.get("signal"), Mapping) else {}
    urls = [u for u in signal.get("source_urls") or [] if isinstance(u, str) and u]
    if urls:
        return "Sources:\n" + "\n".join(f"- {url}" for url in urls)
    if signal.get("source_label"):
        return f"Source: {signal['source_label']}."
    return "No source is recorded for this signal."


# --------------------------------------------------------------------------- menulis satu bagian


@dataclass
class Draft:
    """Satu bagian: pesan untuk model, balasan terakhir model (untuk koreksi), dan isi yang BOLEH dipakai (``None`` = tidak ada)."""

    spec: SectionSpec
    request: str
    raw: str = ""
    text: str | None = None
    note: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)  # jejak ``LLM_OVERRIDDEN`` yang ditahan sampai ``flush_events``
    fixed: bool = False  # teks tetap dari kode (tingkat 0): tidak ditulis dan tidak diperbaiki LLM


@dataclass
class _Context:
    tools: ToolClient
    state: Mapping[str, Any]
    report: Mapping[str, Any]
    simulated: bool
    llm: Any
    system: str
    recommended: str | None
    letters: frozenset[str]


def _ask(ctx: _Context, draft: Draft, instruction: str | None) -> str:
    request = draft.request
    history: list[tuple[str, str]] = []
    if instruction is not None:
        if draft.raw:
            history = [("ai", draft.raw), ("human", instruction)]
        else:  # balasan kosong tidak boleh jadi pesan asisten
            request = f"{request}\n\n{instruction}"
    return message_text(ctx.llm.invoke([("system", ctx.system), ("human", request), *history]))


def _reserve(spec: SectionSpec, ctx: _Context) -> int:
    """Ringkasan berbatas 80 kata: sisakan tempat untuk catatan SIMULATED yang mungkin ditambah kode."""
    if spec.strict and expects_simulated_mark(spec.id, ctx.report, ctx.simulated):
        return len(SIMULATED_NOTE.split())
    return 0


def write_section(draft: Draft, ctx: _Context, *, correction: str | None = None) -> None:
    """Tulis (atau tulis ulang, bila ``correction``) isi ``draft``. Teks yang ditolak diulang sekali dengan instruksi bentuk; pada
    koreksi G-04, teks baru yang gagal TIDAK menghapus teks lama (server memakai template setelah gagal dua kali)."""
    spec = draft.spec
    previous = draft.text
    reserve = _reserve(spec, ctx)
    instruction = correction
    problem = "no reply"
    for attempt in (1, 2):
        reason = budget_exceeded(ctx.state)
        if reason:
            raise AgentTimeout(reason)
        try:
            raw = _ask(ctx, draft, instruction)
        except Exception as exc:  # model / konfigurasi / batas langkah
            log.warning("Report model failed for section %s: %s", spec.id, type(exc).__name__)
            draft.text, draft.note = previous, f"LLM failed ({type(exc).__name__}) → {'previous text kept' if previous else 'not written'}"
            return
        draft.raw = raw
        checked = check_text(raw, spec, recommended=ctx.recommended, letters=ctx.letters, reserve_words=reserve)
        if checked.overridden:
            shown = "; ".join(checked.overridden)
            draft.events.append(dict(
                input_summary=f"narrative for section {spec.heading}", output_summary="text not saved",
                decision=(f"LLM text for section {spec.heading} {shown}, but rule R-15 recommends "
                          f"{f'Option {ctx.recommended}' if ctx.recommended else 'no option'} "
                          "→ rule result kept; text not saved (G-06)")))
        if checked.text is not None:
            notes = required_notes(spec.id, simulated=expects_simulated_mark(spec.id, ctx.report, ctx.simulated),
                                   has_actions=bool(ctx.report.get("actions")))
            draft.text, draft.note = apply_notes(checked.text, notes), None
            return
        problem = checked.problem or problem
        if attempt == 1:
            fix = fix_instruction(problem, spec, reserve)
            instruction = f"{correction} {fix}" if correction else fix
    draft.text = previous
    draft.note = f"text rejected ({problem}) → {'previous text kept' if previous else 'not written'}"


def flush_events(drafts: Sequence[Draft], ctx: _Context) -> None:
    """Tulis jejak ``LLM_OVERRIDDEN`` yang ditahan ``write_section``, menurut urutan ``drafts`` lalu urutan kejadian per draft."""
    for draft in drafts:
        events, draft.events = draft.events, []
        for event in events:
            trace_event(ctx.tools, ctx.state, STEP, "LLM_OVERRIDDEN", tool="save_report", **event)


def write_sections(drafts: Sequence[Draft], ctx: _Context, corrections: Mapping[str, str] | None = None) -> None:
    """``write_section`` untuk semua ``drafts`` bersamaan (``MAX_PARALLEL_SECTIONS``); ``corrections`` = instruksi per id bagian.
    Galat pertama menurut urutan draft dilempar ulang (``AgentTimeout``, ``ToolError``); jejak yang sudah ditahan tetap ditulis."""
    try:
        map_parallel(lambda draft: write_section(draft, ctx, correction=(corrections or {}).get(draft.spec.id)), drafts,
                     max_workers=MAX_PARALLEL_SECTIONS)
    finally:
        flush_events(drafts, ctx)


# --------------------------------------------------------------------------- narasi utuh, simpan, ulang sekali


@dataclass
class Outcome:
    status: str
    written: list[str] = field(default_factory=list)  # bagian yang punya teks (urutan laporan)
    total: int = len(REPORT_SECTIONS)  # jumlah bagian run ini (``sections_for``)
    fallback: bool = False
    attempts: int = 0
    notes: list[str] = field(default_factory=list)


def render_narrative(drafts: Mapping[str, Draft], report: Mapping[str, Any]) -> str:
    """Narasi markdown utuh: judul dan urutan bagian dari kode; bagian tanpa teks diganti keterangan."""
    blocks = []
    for draft in drafts.values():  # urutan bagian = urutan ``sections_for``
        spec = draft.spec
        parts = [draft.text or PLACEHOLDER]
        if spec.id == "what_happened":
            parts.append(sources_block(report))
        blocks.append(f"## {spec.heading}\n\n" + "\n\n".join(parts))
    return "\n\n".join(blocks)


def _blamed(drafts: Mapping[str, Draft], offending: list[str]) -> list[Draft]:
    """Bagian yang memuat angka bermasalah; bila tidak ada yang cocok, semua bagian yang punya teks."""
    writable = [d for d in drafts.values() if d.text and not d.fixed]
    hit = [d for d in writable if any(token in d.text for token in offending)]
    return hit or writable


def write_narrative(tools: ToolClient, state: Mapping[str, Any], report: Mapping[str, Any], simulated: bool,
                    get_model: Callable[[], Any]) -> Outcome:
    """TL-19 sudah dibaca (``report``): tulis delapan bagian, simpan lewat TL-20, ulang sekali bila ditolak."""
    case_id = _case_id(state)
    version = report["version"]
    specs = sections_for(report)
    try:
        llm = get_model()
    except Exception as exc:
        log.warning("Report LLM unavailable: %s", type(exc).__name__)
        return Outcome(NO_NARRATIVE, total=llm_section_count(report), notes=[f"LLM unavailable ({type(exc).__name__})"])

    recommendation = report.get("recommendation") if isinstance(report.get("recommendation"), Mapping) else {}
    letters = frozenset(o["option"] for o in recommendation.get("options") or [] if isinstance(o, Mapping) and o.get("option"))
    ctx = _Context(tools, state, report, simulated, llm, load_prompt(PROMPT), recommendation.get("recommended_option"), letters)
    drafts = {spec.id: Draft(spec, section_request(spec, report, case_id, simulated)) for spec in specs}
    if ESCALATION_ID in drafts and escalation_tier(report) == 0:  # tingkat 0: satu kalimat tetap, tanpa panggilan LLM
        drafts[ESCALATION_ID].text, drafts[ESCALATION_ID].fixed = NO_MEETING_SENTENCE, True
    write_sections([d for d in drafts.values() if not d.fixed], ctx)

    outcome = Outcome(NO_NARRATIVE, total=llm_section_count(report))
    for attempt in (1, 2):
        outcome.written = [d.spec.id for d in drafts.values() if d.text and not d.fixed]
        outcome.notes = [f"{d.spec.heading}: {d.note}" for d in drafts.values() if d.note]
        if not outcome.written:
            return outcome  # tidak ada teks yang boleh dikirim
        response = tools.save_report(case_id, version, render_narrative(drafts, report))
        outcome.attempts = attempt
        validator = response.get("validator") or {}
        if response.get("accepted"):
            outcome.fallback = bool(validator.get("fallback_template_used"))
            outcome.status = TEMPLATE if outcome.fallback else SAVED
            return outcome
        if attempt == 2:
            outcome.status = REJECTED
            return outcome
        offending = [str(n) for n in validator.get("offending_numbers") or []]
        blamed = _blamed(drafts, offending)
        write_sections(blamed, ctx, {
            draft.spec.id: (f"{correction_instruction(offending)} Reply with the corrected body of the "
                            f"\"{draft.spec.heading}\" section only.") for draft in blamed})
    return outcome  # pragma: no cover - loop selalu kembali


def _describe(outcome: Outcome, version: int) -> str:
    """Alasan keputusan untuk ``decision`` trace."""
    count = f"{len(outcome.written)}/{outcome.total}"
    done = {
        SAVED: f"narrative written for {count} sections and saved (G-04 passed on attempt {outcome.attempts})",
        TEMPLATE: f"narrative written for {count} sections; G-04 validator failed twice → server stored its template narrative",
        REJECTED: "server refused the narrative twice → nothing saved",
        NO_NARRATIVE: "no section text could be written → nothing saved",
    }[outcome.status]
    text = f"report version {version}: {done}"
    if outcome.notes:
        text += "; notes: " + "; ".join(outcome.notes)
    return text


# --------------------------------------------------------------------------- node


@traced_node(step="load_case", tool="get_case")
def load_case(state: GenerateReportState, config: RunnableConfig) -> dict[str, Any]:
    case = get_tools(config).get_case(_case_id(state))
    simulated = bool(case.get("simulated"))
    return {"scope": case.get("scope"), "case_status": case.get("status"), "case_simulated": simulated,
            "output_summary": f"status={case.get('status')}; scope={case.get('scope')}; simulated={str(simulated).lower()}"}


@traced_node(step="get_report_data", tool="get_report_data")
def get_report_data(state: GenerateReportState, config: RunnableConfig) -> dict[str, Any]:
    report = get_tools(config).get_report_data(_case_id(state))
    version = report.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("get_report_data returned no integer version")
    return {"report": report, "report_version": version,
            "output_summary": f"version={version}; sections: {len(sections_for(report))}"}


@traced_node(step=STEP, tool="save_report")
def write_report(state: GenerateReportState, config: RunnableConfig) -> dict[str, Any]:
    tools = get_tools(config)
    version = state["report_version"]
    outcome = write_narrative(tools, state, state["report"], bool(state.get("case_simulated")), lambda: get_llm(config))
    status = "ERROR" if outcome.status in (NO_NARRATIVE, REJECTED) else "OK"
    return {
        "report_status": outcome.status,
        "sections_written": outcome.written,
        "sections_total": outcome.total,
        "validator_fallback": outcome.fallback,
        "trace_status": status,
        "input_summary": f"case {_case_id(state)}; report version {version}; {outcome.total} sections",
        "output_summary": f"status={outcome.status}; sections written: {len(outcome.written)}/{outcome.total}",
        "decision": _describe(outcome, version),
    }


def build_graph() -> Any:
    g = StateGraph(GenerateReportState)
    nodes = (load_case, get_report_data, write_report)
    for node in nodes:
        g.add_node(node.step, node)
    g.add_edge(START, nodes[0].step)
    for a, b in zip(nodes, nodes[1:]):
        g.add_edge(a.step, b.step)
    g.add_edge(nodes[-1].step, END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run → ``status`` / ``summary`` / ``data`` kontrak D.3 (``generate_report``: versi dan pemakaian template)."""
    s = outcome.state
    version = s.get("report_version")
    data: Json = {"version": version, "validator_fallback": bool(s.get("validator_fallback"))}

    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": data}

    status = s.get("report_status")
    written, total = len(s.get("sections_written") or []), s.get("sections_total") or len(REPORT_SECTIONS)
    if status == SAVED:
        detail = "all sections" if written == total else f"{written} of {total} sections (the others could not be generated)"
        return {"status": "OK", "summary": f"Report version {version} saved; narrative written for {detail}.", "data": data}
    if status == TEMPLATE:
        return {"status": "OK", "data": data, "summary": (
            f"Report version {version} saved; the number check failed twice, so the server stored its template narrative.")}
    reason = {REJECTED: "the server refused the narrative twice",
              NO_NARRATIVE: "no usable narrative text could be written"}.get(status or "", "the report was not generated")
    return {"status": "ERROR", "summary": f"No report saved: {reason}.", "data": data}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None) -> Json:
    """Jalankan ``generate_report`` untuk payload D.2. ``tools`` / ``llm`` hanya diisi tes."""
    _case_id(payload)
    tools = tools or ToolClient.from_payload(payload)
    outcome = run_guarded(build_graph(), new_run_state(payload), tools,
                          configurable={LLM_KEY: llm} if llm is not None else None)
    return build_response(outcome)
