"""Graph ``recommend`` — Recommendation Agent (PRD 7.2, 7.5; A-11).

    load_case → recommend → END                       (task ``recommend``)

Node ``recommend`` juga node penutup ``analyze_case`` dan ``resume_case``: satu fungsi, satu jejak berlangkah ``recommend``.

Pembagian tugas (G-04, G-06):
- Opsi, aksi, sebelum/sesudah, ``recommended_option``, ``rule_reason``, ``preliminary`` dan versi datang dari TL-23
  (``get_recommendation_options``), dihitung server (R-14, R-15). Agen tidak menghitung dan tidak mengubahnya.
- LLM hanya menulis alasan ≤ 120 kata per opsi yang TERSEDIA, lewat agen ReAct dengan satu tool: ``simulate_plan`` (read-only,
  maks ``MAX_SIMULATIONS_PER_OPTION`` per opsi, ``case_id`` dikunci ke kasus ini). Angka hanya dari JSON opsi.
- ``save_recommendation`` (TL-16) mengirim teks alasan saja; validator G-04 berjalan di server. ``accepted = false`` →
  satu kali ulang: opsi yang teksnya memuat angka bermasalah ditulis ulang dengan ``offending_numbers`` sebagai koreksi, lalu
  semua alasan dikirim lagi. Gagal kedua → server memakai template (``fallback_template_used``).
- G-06: teks yang menganjurkan opsi lain daripada ``recommended_option`` (atau menolak opsi yang dianjurkan aturan) dibuang
  dan TIDAK dikirim ke TL-16; jejak mencatat ``LLM_OVERRIDDEN``. Pendeteksinya fungsi Python (``contradictions``), bukan LLM.
  Opsi tanpa teks dari LLM tidak ikut ``rationales``; kartunya tampil tanpa alasan, ``rule_reason`` tetap dari kode.
- Paralel (A-15, DEC-016): alasan tiap opsi ditulis bersamaan (maks ``MAX_PARALLEL_RATIONALES`` thread; satu agen ReAct dan jatah
  ``simulate_plan`` per opsi, tanpa state bersama). Urutan hasil, ``rationales`` yang dikirim ke TL-16, dan baris jejak
  ``LLM_OVERRIDDEN`` mengikuti urutan opsi (jejak ditahan per opsi lalu ditulis berurutan), sama dengan versi serial.
- Bentuk: hanya satu paragraf prosa polos yang disimpan (``form_problem``). Balasan berupa penolakan/markdown/banyak paragraf
  atau yang membocorkan nama kunci JSON dan prompt dibuang seperti teks yang gagal; G-04 dan G-06 saja tidak menangkapnya.

Tidak ada percabangan ``mode`` / ``trigger`` (DEMO = LIVE, manual = auto). Tidak ada tool tulis ERP.
"""

from __future__ import annotations

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
from graphs.schedule import fit_words
from prompts import load_prompt
from tools.client import Json, ToolClient, ToolError
from tools.langchain_tools import build_recommendation_tools

log = logging.getLogger(__name__)

TASK = "recommend"
STEP = "recommend"
PROMPT = "recommendation"
MAX_WORDS = 120  # PRD 7.5
# G-05: jatah 25 tool call dan 120 detik per run dibagi dengan langkah lain. Uji nyata (19 Okt): satu putaran simulate_plan
# memaksa model menulis ulang semua aksi opsi (puluhan detik), padahal before/after sudah dihitung aturan di TL-23.
MAX_SIMULATIONS_PER_OPTION = 1
AGENT_RECURSION_LIMIT = 12  # jaring pengaman putaran model ↔ tool per pemanggilan agen
MAX_PARALLEL_RATIONALES = LLM_MAX_PARALLEL  # A-15: alasan per opsi ditulis bersamaan (satu agen per opsi); 1 = serial

# Status ``recommendation_status`` (state)
SAVED = "SAVED"  # alasan LLM tersimpan
TEMPLATE = "TEMPLATE"  # validator G-04 gagal dua kali; server menyimpan template
NO_RATIONALE = "NO_RATIONALE"  # tidak ada teks LLM yang boleh dikirim; TL-16 tidak dipanggil
NO_OPTIONS = "NO_OPTIONS"  # tidak ada opsi tersedia
REJECTED = "REJECTED"  # server menolak dua kali (di luar kontrak TL-16)


class RecommendState(RunState, total=False):
    scope: str | None
    case_status: str | None
    preliminary: bool
    recommendation_status: str
    recommended_option: str | None
    rule_reason: str | None
    confirmations_needed: list[Json]  # TL-23 v1.11: [{"site_id", "request_type"}] yang belum diminta (A-16)
    escalation_tier: int | None  # TL-23 v1.11: tingkat R-22 (0/1/2)
    rationale_options: list[str]
    validator_fallback: bool
    decision: str


def _case_id(state: Mapping[str, Any]) -> str:
    case_id = state.get("case_id")
    if not case_id:
        raise ValueError(f"{TASK} requires case_id")
    return case_id


# --------------------------------------------------------------------------- G-06: teks vs hasil aturan

# Penyebutan opsi: "Option A", "option 'B'", "Plan C". Huruf harus kapital (menghindari "option a cheaper ...").
_MENTION = re.compile(r"(?i:\boptions?\b|\bplans?\b)\s+[\"'“‘(]?([A-Z])\b")
# Opsi yang disebut sebagai pembanding ("over Option B", "instead of Option B") bukan yang dianjurkan.
_COMPARING = re.compile(r"(?i)\b(?:over|than|versus|vs\.?|against|unlike|instead of|rather than|compared (?:with|to)"
                        r"|and not|but not)\s+(?:the\s+)?$")
# Kata kerja aktif ("we recommend Option B"): sasarannya penyebutan opsi tepat sesudah kata kerja.
_ACTIVE = re.compile(r"(?i)\b(?:recommend|recommends|suggest|suggests|advise|advises|prefer|prefers|favou?rs?|choose|chooses"
                     r"|select|selects|pick|picks|adopt|adopts|go with|goes with|opt for|opts for|switch to|switches to"
                     r"|proceed with|proceeds with)\b")
# Bentuk pasif/predikat ("Option B is recommended", "Option B is the better choice"): sasarannya penyebutan sebelumnya,
# dan harus didahului kata kerja bantu ("is", "is not", "would be") supaya "the selected supplier" tidak terhitung.
_PARTICIPLE = re.compile(r"(?i)\b(?:recommended|suggested|advised|preferred|favou?red|selected|chosen|picked)\b|\bpreferable\b")
_SUPERLATIVE = re.compile(r"(?i)\b(?:better|best|right|correct|safer|safest|optimal|ideal|wiser|smarter)\s+"
                          r"(?:option|choice|plan|path|course|approach|alternative|pick)\b")
_COPULA = re.compile(r"(?i)(?:\b(?:is|are|was|were|be|been|being)\b|\b(?:isn|aren|wasn|weren)['’]t\b)"
                     r"(?:\s+(?:also|therefore|thus|clearly|strongly|still|not|never|only|really|simply))*"
                     r"(?:\s+(?:the|a|an))?\s*$")
_NEGATION = re.compile(r"(?i)\b(?:not|never|no|neither|nor|cannot|without|rather than|instead of|in place of|as opposed to)\b"
                       r"|n['’]t\b")
_HYPOTHETICAL = re.compile(r"(?i)\b(?:if|when|whether|unless|in case)\b")
_CLAUSE_BREAK = re.compile(r"(?i)[,;:]|\b(?:but|while|whereas|and|so|because|although|though|since|which)\b")
_AGAINST = re.compile(r"(?i)\s+against\b")
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")
OBJECT_GAP = 12  # karakter antara kata kerja anjuran dan opsi yang dianjurkan ("recommend switching to Option B")
SUBJECT_GAP = 40  # karakter antara opsi (subjek) dan "is recommended" / "is the better choice"


def _clause_before(sentence: str, pos: int, width: int = 30) -> str:
    """Potongan klausa tepat sebelum ``pos`` (dibatasi tanda baca / kata sambung), tempat negasi dan "if" dicari."""
    return _CLAUSE_BREAK.split(sentence[max(0, pos - width):pos])[-1]


def _nearest(mentions: list[tuple[int, int, str, bool]], lo: int, hi: int, side: str, max_gap: int) -> str | None:
    best: tuple[int, str] | None = None
    for start, end, letter, comparing in mentions:
        if comparing:
            continue
        if side == "after" and start >= hi:
            gap = start - hi
        elif side == "before" and end <= lo:
            gap = lo - end
        else:
            continue
        if gap <= max_gap and (best is None or gap < best[0]):
            best = (gap, letter)
    return best[1] if best else None


def stated_choices(text: str, letters: set[str] | frozenset[str]) -> tuple[set[str], set[str]]:
    """(opsi yang dianjurkan teks, opsi yang ditolak teks) menurut pola kalimat Inggris yang lazim.

    Heuristik murni (tanpa LLM) yang sengaja hati-hati: hanya kata anjuran yang tegas (recommend, suggest, prefer, choose,
    "is recommended", "is the better choice", ...) yang dihitung; kalimat andaian ("if you choose Option B ..."), perbandingan
    ("... over Option B"), dan gerund ("choosing Option B would ...") tidak. Opsi di luar ``letters`` diabaikan."""
    endorsed: set[str] = set()
    rejected: set[str] = set()
    for sentence in _SENTENCES.split(text):
        mentions = [(m.start(), m.end(), m.group(1), bool(_COMPARING.search(sentence[max(0, m.start() - 22):m.start()])))
                    for m in _MENTION.finditer(sentence) if m.group(1) in letters]
        if not mentions:
            continue
        cues = ([(m, "active") for m in _ACTIVE.finditer(sentence)]
                + [(m, "participle") for m in _PARTICIPLE.finditer(sentence)]
                + [(m, "superlative") for m in _SUPERLATIVE.finditer(sentence)])
        for cue, kind in cues:
            fragment = _clause_before(sentence, cue.start())
            if _HYPOTHETICAL.search(fragment):
                continue
            against = kind == "active" and bool(_AGAINST.match(sentence[cue.end():]))  # "advise against Option A"
            if against:
                letter = _nearest([(s, e, x, False) for s, e, x, _ in mentions], cue.start(), cue.end(), "after",
                                  OBJECT_GAP + len(" against"))
            elif kind == "active":
                letter = _nearest(mentions, cue.start(), cue.end(), "after", OBJECT_GAP)
            else:
                following = next((m[2] for m in mentions if not m[3] and m[0] >= cue.end()
                                  and not sentence[cue.end():m[0]].strip()), None)  # "the recommended Option B"
                if kind == "participle" and following:
                    letter = following
                elif _COPULA.search(fragment):
                    letter = _nearest(mentions, cue.start(), cue.end(), "before", SUBJECT_GAP)
                else:
                    continue
            if letter is None:
                continue
            (rejected if against or _NEGATION.search(fragment) else endorsed).add(letter)
    return endorsed, rejected


# Bentuk teks yang boleh disimpan: satu paragraf prosa polos (prompt: "one plain paragraph, no markdown"). Uji nyata dengan prompt
# yang dibalik (19 Okt) menunjukkan model bisa membalas dengan penolakan, pembuka seperti "Since write_for_option equals ...",
# tabel/markdown, atau bagian "disclosure"; semuanya lolos G-04 (angkanya dari JSON) dan G-06, jadi tampil di kartu opsi.
_MARKDOWN = re.compile(r"\*\*|__|`|(?m:^\s{0,3}#{1,6}\s)|(?m:^\s*(?:[-*•]|\d+[.)])\s+)|(?m:^\s*(?:-{3,}|\*{3,})\s*$)")
_LEAK = re.compile(r"(?i)\b(?:write_for_option|recommended_option|rule_reason|system prompt|red-team|json)\b")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def form_problem(text: str) -> str | None:
    """Alasan teks bukan satu paragraf prosa polos tentang opsinya (dibuang, tidak dikirim ke TL-16), atau ``None``."""
    if _PARAGRAPH_BREAK.search(text):
        return "more than one paragraph"
    if _MARKDOWN.search(text):
        return "markdown formatting"
    if _LEAK.search(text):
        return "references to its own instructions or data keys"
    return None


def contradictions(text: str, recommended: str | None, letters: set[str] | frozenset[str]) -> list[str]:
    """Pertentangan teks dengan hasil aturan R-15 (G-06). Kosong = teks boleh dikirim."""
    endorsed, rejected = stated_choices(text, letters)
    found = [f"recommends Option {x}" for x in sorted(endorsed - {recommended})]
    if recommended in rejected:
        found.append(f"argues against Option {recommended}")
    return found


# --------------------------------------------------------------------------- agen ReAct per opsi


class _SimulationBudget:
    """Pandangan ``ToolClient`` untuk agen ReAct: hanya ``simulate_plan``, ``case_id`` dikunci, jatah terbatas (G-05).

    Jatah habis → ``ToolError`` ``VALIDATION_FAILED``; ``tools/langchain_tools.py`` meneruskannya ke model sebagai galat yang
    bisa dipulihkan, jadi model menulis alasan dari JSON yang sudah ada."""

    def __init__(self, tools: ToolClient, case_id: str, limit: int) -> None:
        self._tools, self._case_id, self._left = tools, case_id, limit

    def simulate_plan(self, case_id: str, actions: Any) -> Json:
        if self._left <= 0:
            raise ToolError("VALIDATION_FAILED",
                            f"simulate_plan limit reached ({MAX_SIMULATIONS_PER_OPTION} per option); "
                            "write the rationale from the JSON you already have", tool="simulate_plan")
        self._left -= 1
        return self._tools.simulate_plan(self._case_id, actions)


@dataclass
class Draft:
    """Alasan satu opsi: percakapan agen (untuk koreksi) dan teks yang BOLEH dikirim (``None`` = tidak ada)."""

    option: str
    budget: _SimulationBudget
    messages: list[Any] = field(default_factory=list)
    text: str | None = None
    note: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)  # jejak ``LLM_OVERRIDDEN`` yang ditahan sampai ``flush_events``
    contradicted: list[str] = field(default_factory=list)  # pertentangan G-06 pada putaran terakhir (DEC-047: ditulis ulang sekali)


def contradiction_instruction(option: str, problems: Sequence[str], recommended: str | None) -> str:
    """DEC-047: instruksi tulis ulang (English, untuk LLM) untuk teks yang bertentangan dengan hasil aturan R-15 (G-06)."""
    result = f"Option {recommended} is recommended" if recommended else "no option is recommended"
    return (
        f"Your previous text went against the rule result: it {'; '.join(problems)}. The recommendation is decided by fixed "
        f"rules and is final: {result}. Rewrite the rationale for Option {option} only: explain what this option does and its "
        "effect, using only the provided JSON, without recommending, preferring or arguing against any option. "
        f"One plain paragraph, at most {MAX_WORDS} words."
    )


def option_input(view: Mapping[str, Any], option: str) -> str:
    """JSON yang diberikan ke LLM untuk satu opsi: seluruh keluaran TL-23 + ``write_for_option``."""
    return json.dumps({**view, "write_for_option": option}, ensure_ascii=False)


def _run_agent(llm: Any, budget: _SimulationBudget, messages: list[Any]) -> list[Any]:
    from langchain.agents import create_agent  # impor lambat: tidak dibutuhkan task lain

    agent = create_agent(llm, build_recommendation_tools(budget), system_prompt=load_prompt(PROMPT))  # type: ignore[arg-type]
    result = agent.invoke({"messages": messages}, {"recursion_limit": AGENT_RECURSION_LIMIT})
    return list(result["messages"])


@dataclass
class _Context:
    tools: ToolClient
    state: Mapping[str, Any]
    view: Mapping[str, Any]
    llm: Any
    recommended: str | None
    letters: frozenset[str]


def _discard(draft: Draft, previous: str | None, reason: str) -> None:
    """Teks baru dibuang. Tanpa teks sebelumnya opsi ini tidak punya alasan; pada koreksi teks lama tetap dikirim."""
    draft.text = previous
    draft.note = f"{reason} → {'previous text kept' if previous else 'not saved'}"


def write_rationale(draft: Draft, ctx: _Context, *, correction: str | None = None) -> None:
    """Satu putaran agen untuk ``draft``; hasilnya menggantikan ``draft.text`` hanya bila lolos G-06 dan batas kata.

    Pada koreksi (``correction``), teks baru yang gagal (galat model, kosong, kepanjangan, bertentangan) TIDAK menghapus teks
    lama: teks lama tetap dikirim dan server memakai template (G-04, gagal dua kali)."""
    reason = budget_exceeded(ctx.state)
    if reason:
        raise AgentTimeout(reason)

    previous = draft.text
    draft.contradicted = []
    messages = ([("human", option_input(ctx.view, draft.option))] if correction is None
                else [*draft.messages, ("human", correction)])
    try:
        conversation = _run_agent(ctx.llm, draft.budget, messages)
        text = message_text(conversation[-1])
    except ToolError:
        raise  # sentra-api bermasalah: bukan alasan untuk diam-diam melewati alasan
    except Exception as exc:  # model / konfigurasi / batas langkah agen
        log.warning("Recommendation agent failed for option %s: %s", draft.option, type(exc).__name__)
        _discard(draft, previous, f"LLM failed ({type(exc).__name__})")
        return
    draft.messages = conversation

    problems = contradictions(text, ctx.recommended, ctx.letters)
    if problems:
        shown = "; ".join(problems)
        draft.contradicted = problems
        _discard(draft, previous, f"text {shown}")
        draft.events.append(dict(
            input_summary=f"rationale for Option {draft.option}",
            output_summary="text not saved",
            decision=(f"LLM text for Option {draft.option} {shown}, but rule R-15 recommends "
                      f"{f'Option {ctx.recommended}' if ctx.recommended else 'no option'} "
                      "→ rule result kept; text not saved (G-06)"),
        ))
        return

    problem = form_problem(text)
    if problem:
        _discard(draft, previous, f"text has {problem}")
        return

    fitted = fit_words(text, MAX_WORDS) if text else None
    if not fitted:
        _discard(draft, previous, "empty text" if not text else f"text longer than {MAX_WORDS} words")
        return
    draft.text, draft.note = fitted, None


def flush_events(drafts: Sequence[Draft], ctx: _Context) -> None:
    """Tulis jejak ``LLM_OVERRIDDEN`` yang ditahan ``write_rationale``, menurut urutan ``drafts`` lalu urutan kejadian per draft."""
    for draft in drafts:
        events, draft.events = draft.events, []
        for event in events:
            trace_event(ctx.tools, ctx.state, STEP, "LLM_OVERRIDDEN", tool="save_recommendation", **event)


def write_rationales(drafts: Sequence[Draft], ctx: _Context, corrections: Mapping[str, str] | None = None) -> None:
    """``write_rationale`` untuk semua ``drafts`` bersamaan (``MAX_PARALLEL_RATIONALES``); ``corrections`` = instruksi per opsi.

    Galat pertama menurut urutan draft dilempar ulang (``AgentTimeout``, ``ToolError``) seperti versi serial; jejak yang sudah ditahan
    tetap ditulis lebih dulu (``finally``)."""
    try:
        map_parallel(lambda draft: write_rationale(draft, ctx, correction=(corrections or {}).get(draft.option)), drafts,
                     max_workers=MAX_PARALLEL_RATIONALES)
    finally:
        flush_events(drafts, ctx)


# --------------------------------------------------------------------------- tulis, simpan, ulang sekali


@dataclass
class Outcome:
    status: str
    saved: list[str] = field(default_factory=list)  # opsi yang teksnya dikirim ke TL-16 (urutan opsi)
    fallback: bool = False
    attempts: int = 0
    notes: list[str] = field(default_factory=list)


def _texts(drafts: Mapping[str, Draft]) -> dict[str, str]:
    return {option: d.text for option, d in drafts.items() if d.text}


def _blamed(drafts: Mapping[str, Draft], offending: list[str]) -> list[Draft]:
    """Draft yang memuat angka bermasalah; bila tidak ada yang cocok, semua draft yang bersuara."""
    hit = [d for d in drafts.values() if d.text and any(token in d.text for token in offending)]
    return hit or [d for d in drafts.values() if d.text]


def explain_options(tools: ToolClient, state: Mapping[str, Any], view: Mapping[str, Any],
                    get_model: Callable[[], Any]) -> Outcome:
    """TL-23 sudah dibaca (``view``): tulis alasan per opsi tersedia, simpan lewat TL-16, ulang sekali bila ditolak."""
    options = list(view.get("options") or [])
    available = [o["option"] for o in options if o.get("available")]
    if not available:
        return Outcome(NO_OPTIONS)
    case_id = _case_id(state)

    try:
        llm = get_model()
    except Exception as exc:
        log.warning("Recommendation LLM unavailable: %s", type(exc).__name__)
        return Outcome(NO_RATIONALE, notes=[f"LLM unavailable ({type(exc).__name__})"])

    # A-17: huruf yang dikenal G-06 = SEMUA kode opsi di view (A, B, C, D, E, R, S; juga yang tidak tersedia), bukan daftar tetap
    letters = frozenset(o["option"] for o in options)
    ctx = _Context(tools, state, view, llm, view.get("recommended_option"), letters)
    drafts = {option: Draft(option, _SimulationBudget(tools, case_id, MAX_SIMULATIONS_PER_OPTION)) for option in available}
    write_rationales(list(drafts.values()), ctx)
    # DEC-047: teks yang bertentangan dengan aturan (G-06) ditulis ulang SEKALI; masih bertentangan → tidak disimpan
    contradicted = [d for d in drafts.values() if d.contradicted]
    if contradicted:
        write_rationales(contradicted, ctx, {
            d.option: contradiction_instruction(d.option, d.contradicted, ctx.recommended) for d in contradicted})

    outcome = Outcome(NO_RATIONALE)
    for attempt in (1, 2):
        rationales = _texts(drafts)
        outcome.notes = [f"Option {d.option}: {d.note}" for d in drafts.values() if d.note]
        if not rationales:
            return outcome  # tidak ada teks yang boleh dikirim (TL-16 menolak rationales kosong)
        response = tools.save_recommendation(case_id, view["version"], rationales)
        outcome.attempts = attempt
        validator = response.get("validator") or {}
        if response.get("accepted"):
            outcome.saved = list(rationales)
            outcome.fallback = bool(validator.get("fallback_template_used"))
            outcome.status = TEMPLATE if outcome.fallback else SAVED
            return outcome
        if attempt == 2:
            outcome.status = REJECTED
            return outcome
        offending = [str(n) for n in validator.get("offending_numbers") or []]
        blamed = _blamed(drafts, offending)
        write_rationales(blamed, ctx, {
            draft.option: (f"{correction_instruction(offending)} Reply with the corrected rationale for Option "
                           f"{draft.option} only, in at most {MAX_WORDS} words.") for draft in blamed})
    return outcome  # pragma: no cover - loop selalu kembali


def _describe(outcome: Outcome, rule_reason: str | None, preliminary: bool) -> str:
    """Alasan keputusan untuk ``decision`` trace: hasil aturan (kode) lalu apa yang dilakukan agen."""
    shown = ", ".join(outcome.saved) or "-"
    done = {
        NO_OPTIONS: "no option available → nothing to explain",
        SAVED: f"rationales saved for {shown} (G-04 passed on attempt {outcome.attempts})",
        TEMPLATE: f"G-04 validator failed twice → server stored template rationales for {shown}",
        REJECTED: "server refused the rationales twice → nothing saved",
        NO_RATIONALE: "no LLM rationale could be sent → recommendation saved without rationales",
    }[outcome.status]
    text = f"{rule_reason or 'recommendation decided by rule R-15'} → {done}"
    if outcome.notes:
        text += "; notes: " + "; ".join(outcome.notes)
    if preliminary:
        text += "; PRELIMINARY until supplier confirmation"
    return text


# --------------------------------------------------------------------------- node


@traced_node(step="load_case", tool="get_case")
def load_case(state: RecommendState, config: RunnableConfig) -> dict[str, Any]:
    case = get_tools(config).get_case(_case_id(state))
    return {"scope": case.get("scope"), "case_status": case.get("status"),
            "output_summary": f"status={case.get('status')}; scope={case.get('scope')}"}


@traced_node(step=STEP, tool="get_recommendation_options")
def recommend(state: RecommendState, config: RunnableConfig) -> dict[str, Any]:
    tools = get_tools(config)
    case_id = _case_id(state)
    view = tools.get_recommendation_options(case_id)
    available = [o["option"] for o in view.get("options") or [] if o.get("available")]
    outcome = explain_options(tools, state, view, lambda: get_llm(config))

    recommended = view.get("recommended_option")
    preliminary = bool(state.get("preliminary")) or bool(view.get("preliminary"))
    status = {NO_OPTIONS: "SKIPPED", NO_RATIONALE: "ERROR", REJECTED: "ERROR"}.get(outcome.status, "OK")
    return {
        "recommended_option": recommended,
        "rule_reason": view.get("rule_reason"),
        # A-16: dibaca oleh request_option_confirmation / meeting_room_check; backend lama tanpa field ini → [] / None
        "confirmations_needed": list(view.get("confirmations_needed") or []),
        "escalation_tier": view.get("escalation_tier"),
        "preliminary": preliminary,
        "recommendation_status": outcome.status,
        "rationale_options": outcome.saved,
        "validator_fallback": outcome.fallback,
        "trace_status": status,
        "input_summary": f"case {case_id}; available options: {', '.join(available) or '-'}",
        "output_summary": (f"recommended={recommended or '-'}; status={outcome.status}; "
                           f"rationales: {', '.join(outcome.saved) or '-'}"),
        "decision": _describe(outcome, view.get("rule_reason"), preliminary),
    }


def build_graph() -> Any:
    g = StateGraph(RecommendState)
    g.add_node(load_case.step, load_case)
    g.add_node(recommend.step, recommend)
    g.add_edge(START, load_case.step)
    g.add_edge(load_case.step, recommend.step)
    g.add_edge(recommend.step, END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def recommendation_summary(s: Mapping[str, Any]) -> str:
    """Satu klausa ringkasan D.3 dari state hasil node ``recommend``; dipakai ``recommend``, ``analyze_case``, ``resume_case``.

    Aturan bisa tidak menganjurkan opsi mana pun (mis. hanya Opsi E yang menunggu konfirmasi supplier): itu bukan
    "belum dibuat", jadi dibedakan dari run yang belum sampai ke langkah ini."""
    option = s.get("recommended_option")
    if option:
        return f"Option {option} recommended" + (" (PRELIMINARY)" if s.get("preliminary") else "")
    status = s.get("recommendation_status")
    if status == NO_OPTIONS:
        return "no option available"
    if status:
        return "no option recommended by the rules"
    return "recommendation not generated yet"


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run → ``status`` / ``summary`` / ``data`` kontrak D.3 (``recommend``: opsi dan pemakaian template)."""
    s = outcome.state
    data: Json = {"recommended_option": s.get("recommended_option"), "validator_fallback": bool(s.get("validator_fallback"))}

    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": data}

    clause = recommendation_summary(s)
    parts = [clause[0].upper() + clause[1:]]
    saved = s.get("rationale_options") or []
    if saved:
        parts.append(f"rationales saved for {', '.join(saved)}" + (" (template used)" if s.get("validator_fallback") else ""))
    elif s.get("recommendation_status") != NO_OPTIONS:
        parts.append("no rationale saved")
    return {"status": "OK", "summary": "; ".join(parts) + ".", "data": data}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None) -> Json:
    """Jalankan ``recommend`` untuk payload D.2. ``tools`` / ``llm`` hanya diisi tes."""
    _case_id(payload)
    tools = tools or ToolClient.from_payload(payload)
    outcome = run_guarded(build_graph(), new_run_state(payload), tools,
                          configurable={LLM_KEY: llm} if llm is not None else None)
    return build_response(outcome)
