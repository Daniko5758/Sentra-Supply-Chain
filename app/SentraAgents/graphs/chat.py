"""Graph ``chat`` — Chat Agent (PRD 7.5, FR-16, G-07; A-13).

    START --permintaan eksekusi--> execution_policy → END          (G-07: tanpa LLM, tanpa tool)
    START --lainnya--------------> load_case → answer → END        (task ``chat``)

Pembagian tugas (G-04, G-06, G-07):
- ``load_case`` membaca ulang kasus terpilih (TL-17) sebagai konteks; chat global (``case_id`` kosong) melewatinya.
- ``answer``: agen ReAct (``create_agent``) dengan tool baca FR-16 saja (+ ``run_what_if`` bila F-10 aktif), riwayat dari payload
  ``history`` (maks 10 pesan), jawaban English yang menyebut ID data / URL sumber. Tool lewat ``ChatToolbox``: hanya tool baca,
  jatah ``MAX_AGENT_TOOL_CALLS`` per pertanyaan, tenggat G-05, setiap hasil dicatat sebagai bahan ``allowed`` untuk TL-24.
- Validasi angka (G-04): TL-24 dengan ``allowed`` = pertanyaan + riwayat + kasus + semua hasil tool. Gagal → ulang sekali dengan
  koreksi; gagal lagi → jawab TANPA angka; gagal lagi → template tanpa teks bebas. URL di jawaban harus ada di data (G-02).
- Kutipan (``citations``) dihitung kode dari ID dan URL yang benar-benar ada di data DAN disebut di jawaban; LLM tidak mengarangnya.
- G-07: permintaan eksekusi ("Approve option …", "Submit the plan") dijawab kode dengan teks kebijakan menurut flag F-12, tanpa
  LLM. Teks LLM yang mengaku sudah mengeksekusi sesuatu diganti teks yang sama dan jejak mencatat ``LLM_OVERRIDDEN``.

Flag fitur opsional dibaca dari env runtime (``FEATURE_ERP_EXECUTION``, ``FEATURE_WHATIF``; bawaan mati): payload D.2 belum
membawa flag. Tidak ada percabangan ``mode`` / ``trigger`` (DEMO = LIVE, manual = auto). Tidak ada tool tulis ERP.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from graphs.common import (
    LLM_KEY, AgentTimeout, RunOutcome, RunState, budget_exceeded, correction_instruction, get_llm, get_tools,
    message_text, new_run_state, run_guarded, trace_event, traced_node,
)
from prompts import load_prompt
from tools.client import FeatureDisabledError, Json, ToolClient, ToolError
from tools.langchain_tools import CHAT_TOOL_NAMES, CHAT_WHATIF_TOOL, build_chat_tools

log = logging.getLogger(__name__)

TASK = "chat"
PROMPT = "chat"
MAX_HISTORY = 10  # kontrak D.2
# G-05: jatah 25 tool call per run. Satu pertanyaan memakai 1 (get_case) + jatah agen + paling banyak 3 panggilan TL-24.
MAX_AGENT_TOOL_CALLS = 8
AGENT_RECURSION_LIMIT = 25  # jaring pengaman putaran model ↔ tool per pemanggilan agen
CHAT_READ_TIMEOUT_S = 30  # satu giliran bisa memanggil model beberapa kali; batas 120 detik G-05 tetap berlaku
MAX_CITATIONS = 20

FEATURES_KEY = "features"  # config["configurable"][FEATURES_KEY] = {"erp_execution": bool, "whatif": bool} (tes)
FEATURE_ENV = {"erp_execution": "FEATURE_ERP_EXECUTION", "whatif": "FEATURE_WHATIF"}
_TRUE = frozenset({"1", "true", "yes", "on"})

# Sumber jawaban (state ``answer_source``)
SOURCE_LLM = "llm"  # lolos G-04 pada percobaan pertama
SOURCE_RETRY = "retry"  # lolos setelah satu koreksi
SOURCE_NO_NUMBERS = "no_numbers"  # lolos setelah diminta menjawab tanpa angka
SOURCE_TEMPLATE = "template"  # gagal tiga kali
SOURCE_POLICY = "policy"  # G-07

TEMPLATE_REPLY = ("I could not give you an answer whose figures I can verify against the SENTRA data, so I am not showing "
                  "one. Please rephrase the question, or open the case or country page in SENTRA to see the figures.")
NUMBER_FREE_INSTRUCTION = (
    "Your previous answers still contained numbers or URLs that are not in the provided data. Answer the user's question once "
    "more without any numbers or digits: describe amounts, dates, scores and counts in words, and refer to records only by "
    "their IDs as they appear in the data. Do not include URLs. Reply with the answer only."
)
WHATIF_ON = ("What-if: when the user asks what happens if a location is disrupted, call run_what_if with that location. "
             "It creates no case and changes nothing.")
WHATIF_OFF = "What-if simulation is not enabled in this deployment. If the user asks for it, say so."


class ChatState(RunState, total=False):
    message: str
    history: list[tuple[str, str]]  # (human | ai, teks), sudah dibersihkan
    case: Json | None
    answer: str
    citations: list[Json]
    validated: bool
    answer_source: str
    tools_used: list[str]
    decision: str


# --------------------------------------------------------------------------- payload dan flag


def parse_chat_payload(payload: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """(pertanyaan, riwayat bersih) dari payload D.2. ``message`` wajib; ``history`` boleh kosong."""
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    history = payload.get("history")
    if history is not None and not isinstance(history, list):
        raise ValueError("history must be a list of {role, content}")
    return message.strip(), clean_history(history or [])


def clean_history(history: list[Any]) -> list[tuple[str, str]]:
    """Riwayat untuk model: maks ``MAX_HISTORY`` pesan terakhir, bentuk rusak dibuang, peran berselang-seling.

    Percakapan harus dimulai oleh pengguna dan diakhiri jawaban asisten (pertanyaan baru menyusul); pesan sejenis yang berurutan
    digabung."""
    rows: list[tuple[str, str]] = []
    for item in history[-MAX_HISTORY:]:
        if not isinstance(item, Mapping):
            continue
        role, content = item.get("role"), item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        kind, text = ("human" if role == "user" else "ai"), content.strip()
        if rows and rows[-1][0] == kind:
            rows[-1] = (kind, f"{rows[-1][1]}\n\n{text}")
        else:
            rows.append((kind, text))
    while rows and rows[0][0] != "human":
        rows.pop(0)
    if rows and rows[-1][0] == "human":
        rows.pop()
    return rows


def feature_flags(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Flag F-12 (``erp_execution``) dan F-10 (``whatif``) dari env runtime; bawaan mati."""
    env = os.environ if env is None else env
    return {key: (env.get(name) or "").strip().lower() in _TRUE for key, name in FEATURE_ENV.items()}


def _flags(config: Mapping[str, Any] | None) -> dict[str, bool]:
    """Flag run ini: env runtime, ditimpa ``config["configurable"][FEATURES_KEY]`` (tes)."""
    flags = feature_flags()
    given = ((config or {}).get("configurable") or {}).get(FEATURES_KEY)
    flags.update({key: bool(value) for key, value in (given or {}).items()})
    return flags


# --------------------------------------------------------------------------- G-07: permintaan eksekusi

_VERBS = r"(?:approve|execute|submit|authori[sz]e|implement|apply|release|place|cancel|reschedule|post|trigger|push|commit)"
_TARGET = (r"(?:it|them|all|everything|(?:the\s+|this\s+|that\s+|these\s+|those\s+|all\s+(?:the\s+)?)?"
           r"(?:options?|plans?|actions?|proposals?|recommendations?|orders?|pos?|prs?|"
           r"purchase\s+(?:orders?|requisitions?)|production\s+orders?|sales\s+orders?|changes?))")
_LEAD = r"(?:(?:yes|yeah|ok|okay|sure|please|kindly|just|now|and|then)\b[\s,.!:-]*)*"
_ASK = r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?(?:(?:i|we)\s+(?:hereby\s+)?)?"
EXECUTION_REQUEST = re.compile(
    rf"(?i)^\W*{_LEAD}{_ASK}"
    rf"(?:go\s+ahead(?:\s+and\s+{_VERBS})?\b|proceed\s+with\b|do\s+it\b|make\s+it\s+happen\b|{_VERBS}\b\s+{_TARGET}\b)"
)
# Pengakuan orang pertama bahwa agen sudah melakukan sesuatu ("I have approved ..."): agen hanya punya tool baca.
EXECUTION_CLAIM = re.compile(
    r"(?i)\b(?:i|we)(?:'ve|\s+have|\s+just|\s+now|\s+already|\s+successfully|\s+went\s+ahead\s+and)*\s+"
    r"(?:approved|executed|submitted|placed|cancell?ed|rescheduled|released|posted|pushed|implemented|applied)\b"
)


def is_execution_request(message: str) -> bool:
    """Pesan yang meminta agen menyetujui / mengeksekusi sesuatu. Sengaja sempit: hanya kalimat perintah di awal pesan
    ("Approve option …", "Please submit the plan", "Go ahead"); pertanyaan tentang eksekusi dijawab agen menurut prompt."""
    return bool(EXECUTION_REQUEST.match(message))


def claims_execution(text: str) -> bool:
    return bool(EXECUTION_CLAIM.search(text))


def execution_reply(erp_execution: bool) -> str:
    """Jawaban G-07 menurut flag F-12: dengan F-12 lewat Submit for approval + halaman Approvals, tanpa F-12 hanya usulan."""
    if erp_execution:
        return ("I cannot approve or execute anything: I only have read tools. Execution goes through "
                "Recommendations → Submit for approval, and an SCM Manager approves the proposed actions on the Approvals page.")
    return ("I cannot approve or execute anything: SENTRA only proposes actions. The Buyer or PPIC makes the change "
            "directly in the ERP.")


def system_prompt(flags: Mapping[str, bool]) -> str:
    return "\n\n".join([load_prompt(PROMPT), f"Execution policy: {execution_reply(flags['erp_execution'])}",
                        WHATIF_ON if flags["whatif"] else WHATIF_OFF])


# --------------------------------------------------------------------------- tool baca untuk agen


class ChatToolbox:
    """Pandangan ``ToolClient`` untuk agen ReAct chat (dipakai ``tools/langchain_tools.py``).

    Hanya tool baca FR-16 (G-01); ``run_what_if`` hanya bila F-10 aktif. Jatah ``MAX_AGENT_TOOL_CALLS`` dan tenggat G-05
    ditegakkan di sini. Jatah habis → ``ToolError`` ``VALIDATION_FAILED`` yang diteruskan ke model sebagai galat yang bisa
    dipulihkan (ia menjawab dari data yang sudah ada). Setiap hasil dan galat dicatat (``results``): itulah yang model lihat,
    jadi itu pula bahan ``allowed`` untuk TL-24. Aman dipakai dari beberapa thread (tool call paralel dalam satu langkah)."""

    def __init__(self, tools: ToolClient, state: Mapping[str, Any], *, whatif_enabled: bool = False,
                 limit: int = MAX_AGENT_TOOL_CALLS) -> None:
        self._tools, self._state, self._left = tools, state, limit
        self._allowed = frozenset(CHAT_TOOL_NAMES) | ({CHAT_WHATIF_TOOL} if whatif_enabled else frozenset())
        self._lock = threading.Lock()
        self.results: list[Any] = []
        self.used: list[str] = []

    def _call(self, name: str, *args: Any) -> Json:
        if name not in self._allowed:
            raise FeatureDisabledError("FEATURE_DISABLED", f"{name} is not available", tool=name)
        reason = budget_exceeded(self._state)
        if reason:
            raise AgentTimeout(reason)
        with self._lock:
            if self._left <= 0:
                raise ToolError("VALIDATION_FAILED", "tool call limit reached; answer from the data you already have",
                                tool=name)
            self._left -= 1
            self.used.append(name)
        try:
            result = getattr(self._tools, name)(*args)
        except ToolError as exc:
            with self._lock:
                self.results.append({"error": exc.as_dict()})
            raise
        with self._lock:
            self.results.append(result)
        return result

    def get_case(self, case_id: str, version: int | None = None) -> Json:
        return self._call("get_case", case_id, version)

    def get_po_schedule(self) -> Json:
        return self._call("get_po_schedule")

    def get_production_schedule(self) -> Json:
        return self._call("get_production_schedule")

    def get_supplier_network(self, case_id: str | None = None) -> Json:
        return self._call("get_supplier_network", case_id)

    def get_supplier_messages(self, case_id: str, site_id: str | None = None) -> Json:
        return self._call("get_supplier_messages", case_id, site_id)

    def compute_impact(self, case_id: str, *, persist: bool = False) -> Json:
        if persist:  # tool langchain selalu persist=False; penjaga tambahan: chat tidak menulis snapshot
            raise ToolError("VALIDATION_FAILED", "chat cannot persist an impact snapshot", tool="compute_impact")
        return self._call("compute_impact", case_id)

    def simulate_plan(self, case_id: str, actions: Any) -> Json:
        return self._call("simulate_plan", case_id, actions)

    def get_country_heatmap(self) -> Json:
        return self._call("get_country_heatmap")

    def get_report_data(self, case_id: str) -> Json:
        return self._call("get_report_data", case_id)

    def get_escalation(self, case_id: str) -> Json:
        return self._call("get_escalation", case_id)

    def run_what_if(self, location: str) -> Json:
        return self._call("run_what_if", location)


# --------------------------------------------------------------------------- kutipan: ID dan URL dari data

_ID_KEY = re.compile(r"(?:^|_)id$")
_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_URL_TAIL = ".,;:!?"


def record_ids(evidence: Any) -> dict[str, str]:
    """{teks ID: ID yang ditampilkan} untuk semua ID rekaman di ``evidence``: nilai kunci ``id`` / ``*_id`` yang berisi angka
    (ID kasus, site, order, pesan, ...), dan baris PO ``po_id/item_no``. Nomor PO ditampilkan ``PO <nomor>``."""
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if isinstance(value, str) and _ID_KEY.search(key) and 2 <= len(value) <= 40 \
                        and _RECORD_ID.fullmatch(value) and re.search(r"\d", value):
                    found.setdefault(value, f"PO {value}" if key == "po_id" else value)
                walk(value)
            po, item = node.get("po_id"), node.get("item_no")
            if isinstance(po, str) and isinstance(item, int) and not isinstance(item, bool):
                found.setdefault(f"{po}/{item}", f"PO {po}/{item}")
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(evidence)
    return found


def urls_in(text: str) -> list[str]:
    """URL di ``text`` menurut urutan muncul, tanpa tanda baca penutup kalimat."""
    seen: list[str] = []
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(_URL_TAIL)
        if url not in seen:
            seen.append(url)
    return seen


def evidence_urls(node: Any) -> set[str]:
    """Semua URL di string mana pun dalam ``node`` (tanpa garis miring penutup)."""
    out: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.update(u.rstrip("/") for u in urls_in(value))
        elif isinstance(value, Mapping):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(node)
    return out


def build_citations(text: str, evidence: Any, known_urls: set[str]) -> list[Json]:
    """Kutipan EP-18: ID rekaman dan URL yang ada di data DAN disebut di ``text``, menurut urutan muncul."""
    hits: list[tuple[int, Json]] = []
    for needle, shown in record_ids(evidence).items():
        match = re.search(rf"(?<![\w/-]){re.escape(needle)}(?![\w/-])", text)
        if match:
            hits.append((match.start(), {"type": "record", "id": shown}))
    for url in urls_in(text):
        if url.rstrip("/") in known_urls:
            hits.append((text.find(url), {"type": "url", "id": url}))
    out: list[Json] = []
    for _, citation in sorted(hits, key=lambda h: h[0]):
        if citation not in out:
            out.append(citation)
    return out[:MAX_CITATIONS]


# --------------------------------------------------------------------------- satu giliran: agen + validasi G-04


@dataclass
class Problems:
    """Yang salah pada satu draf jawaban."""

    empty: bool = False
    numbers: list[str] = field(default_factory=list)  # angka yang tidak ada di data (TL-24)
    urls: list[str] = field(default_factory=list)  # URL yang tidak ada di data (G-02)

    def __bool__(self) -> bool:
        return self.empty or bool(self.numbers) or bool(self.urls)

    def describe(self) -> str:
        parts = (["empty answer"] if self.empty else []) \
            + ([f"numbers not in the data: {', '.join(self.numbers)}"] if self.numbers else []) \
            + ([f"URLs not in the data: {', '.join(self.urls)}"] if self.urls else [])
        return "; ".join(parts)

    def correction(self) -> str:
        parts = (["Your previous reply was empty. Answer the user's question."] if self.empty else []) \
            + ([correction_instruction(self.numbers)] if self.numbers else []) \
            + ([f"Your previous text contained URLs that are not in the provided data: {', '.join(self.urls)}. "
                "Remove them, or use only URLs that appear in the data."] if self.urls else [])
        return " ".join(parts) + " Reply with the corrected answer only."


@dataclass
class Settled:
    """Jawaban akhir satu giliran."""

    text: str
    source: str
    decision: str


def question_message(message: str, case: Json | None) -> str:
    """Pesan manusia terakhir: data kasus terpilih (sebagai konten data, G-03) lalu pertanyaan."""
    if case is None:
        return f"No case is selected.\n\nQuestion: {message}"
    data = json.dumps(case, ensure_ascii=False, separators=(",", ":"))
    return f"Selected case data (read from SENTRA just now):\n<external_content>{data}</external_content>\n\nQuestion: {message}"


class Turn:
    """Satu pertanyaan: agen ReAct, bahan ``allowed``, dan validasi."""

    def __init__(self, tools: ToolClient, state: Mapping[str, Any], llm: Any, flags: Mapping[str, bool]) -> None:
        self.tools, self.state, self.llm, self.flags = tools, state, llm, flags
        self.message: str = state["message"]
        self.history: list[tuple[str, str]] = list(state.get("history") or [])
        self.case: Json | None = state.get("case")
        self.toolbox = ChatToolbox(tools, state, whatif_enabled=flags["whatif"])
        self.conversation: list[Any] = []
        self._agent: Any = None

    # ---- bahan validasi

    def allowed(self) -> Json:
        """Semua data yang model lihat (G-04): pertanyaan, riwayat, kasus terpilih, dan setiap hasil tool."""
        return {"question": self.message, "history": [text for _, text in self.history], "case": self.case,
                "tool_results": list(self.toolbox.results)}

    def evidence(self) -> Json:
        return {"case": self.case, "tool_results": list(self.toolbox.results)}

    # ---- agen

    def ask(self, correction: str | None = None) -> str:
        """Satu putaran agen. Tanpa ``correction``: riwayat + pertanyaan; dengan ``correction``: lanjutan percakapan."""
        reason = budget_exceeded(self.state)
        if reason:
            raise AgentTimeout(reason)
        if self._agent is None:
            from langchain.agents import create_agent  # impor lambat: tidak dibutuhkan task lain

            tools = build_chat_tools(self.toolbox, whatif_enabled=self.flags["whatif"])  # type: ignore[arg-type]
            self._agent = create_agent(self.llm, tools, system_prompt=system_prompt(self.flags))
        messages = ([*self.history, ("human", question_message(self.message, self.case))] if correction is None
                    else [*self.conversation, ("human", correction)])
        result = self._agent.invoke({"messages": messages}, {"recursion_limit": AGENT_RECURSION_LIMIT})
        self.conversation = list(result["messages"])
        return message_text(self.conversation[-1])

    # ---- validasi

    def problems(self, text: str) -> Problems:
        if not text:
            return Problems(empty=True)
        allowed = self.allowed()
        checked = self.tools.validate_numbers(text, allowed)
        numbers: list[str] = []
        if not checked.get("passed"):
            numbers = [str(n) for n in checked.get("offending_numbers") or []] or ["(unspecified)"]
        known = evidence_urls(allowed)
        return Problems(numbers=numbers, urls=[u for u in urls_in(text) if u.rstrip("/") not in known])


_BOLD_OR_CODE = re.compile(r"\*\*|__|`")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)


def plain_answer(text: str) -> str:
    """Jawaban chat tanpa tanda markdown: UI chat menampilkan teks apa adanya, jadi `**13.3**` terlihat dengan bintangnya.

    Hanya tanda tebal/miring/kode dan penanda judul yang dibuang; baris, paragraf, dan daftar bernomor tetap. Angka, ID, dan URL
    tidak berubah, jadi hasil validasi G-04 dan kutipan tetap berlaku."""
    text = _HEADING.sub("", text)
    text = _BOLD_OR_CODE.sub("", text)
    return _ITALIC.sub(r"\1", text)


def settle(turn: Turn) -> Settled:
    """Jawaban akhir: percobaan 1 → koreksi sekali → tanpa angka → template (G-04). Pengakuan eksekusi → teks G-07 (G-06/G-07)."""
    notes: list[str] = []
    text = turn.ask()
    for attempt in (1, 2, 3):
        if claims_execution(text):
            reply = execution_reply(turn.flags["erp_execution"])
            decision = (f"LLM text claims it executed something, but chat has read tools only → G-07 reply used "
                        f"(attempt {attempt})")
            trace_event(turn.tools, turn.state, "answer", "LLM_OVERRIDDEN", input_summary="chat answer",
                        output_summary="text replaced by the G-07 reply", decision=decision)
            return Settled(reply, SOURCE_POLICY, decision)
        problems = turn.problems(text)
        if not problems:
            if attempt == 1:
                return Settled(text, SOURCE_LLM, "G-04 passed on attempt 1")
            if attempt == 2:
                return Settled(text, SOURCE_RETRY, f"{notes[0]} → rewritten once; G-04 passed on attempt 2")
            return Settled(text, SOURCE_NO_NUMBERS, f"{'; '.join(notes)} → answered without numbers; G-04 passed")
        notes.append(problems.describe())
        if attempt == 3:
            break
        text = turn.ask(problems.correction() if attempt == 1 else NUMBER_FREE_INSTRUCTION)
    return Settled(TEMPLATE_REPLY, SOURCE_TEMPLATE, f"{'; '.join(notes)} → G-04 failed three times → template answer (no free text)")


# --------------------------------------------------------------------------- node


@traced_node(step="execution_policy")
def execution_policy(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    erp = _flags(config)["erp_execution"]
    how = ("F-12 is on → execution goes through Recommendations → Submit for approval and the Approvals page" if erp
           else "F-12 is off → SENTRA only proposes actions; Buyer/PPIC change the ERP directly")
    return {"answer": execution_reply(erp), "citations": [], "validated": True, "answer_source": SOURCE_POLICY,
            "tools_used": [], "decision": f"G-07: execution request → no tool and no LLM; {how}",
            "output_summary": "G-07 reply"}


@traced_node(step="load_case", tool="get_case")
def load_case(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    case_id = state.get("case_id")
    if not case_id:
        return {"case": None, "trace_status": "SKIPPED", "output_summary": "no case selected",
                "decision": "global chat: no case context, tools find the data"}
    case = get_tools(config).get_case(case_id)
    return {"case": case, "output_summary": f"status={case.get('status')}; scope={case.get('scope')}"}


@traced_node(step="answer")
def answer(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    tools = get_tools(config)
    turn = Turn(tools, state, get_llm(config, read_timeout=CHAT_READ_TIMEOUT_S), _flags(config))
    settled = settle(turn)
    if settled.source not in (SOURCE_POLICY, SOURCE_TEMPLATE):
        settled = Settled(plain_answer(settled.text), settled.source, settled.decision)
    known = evidence_urls(turn.allowed())
    citations = [] if settled.source in (SOURCE_POLICY, SOURCE_TEMPLATE) else build_citations(
        settled.text, turn.evidence(), known)
    used = list(dict.fromkeys(turn.toolbox.used))
    shown = " ".join(turn.message.split())
    return {
        "answer": settled.text, "citations": citations, "validated": True, "answer_source": settled.source,
        "tools_used": used, "decision": settled.decision,
        "input_summary": f"question: {shown if len(shown) <= 80 else shown[:79] + '…'}; history {len(turn.history)} messages",
        "output_summary": f"source={settled.source}; tools: {', '.join(used) or '-'}; citations: {len(citations)}",
    }


# --------------------------------------------------------------------------- cabang (fungsi Python, G-06)


def route_message(state: ChatState) -> str:
    return "execution_policy" if is_execution_request(state["message"]) else "load_case"


def build_graph() -> Any:
    g = StateGraph(ChatState)
    for node in (execution_policy, load_case, answer):
        g.add_node(node.step, node)
    g.add_conditional_edges(START, route_message, {"execution_policy": "execution_policy", "load_case": "load_case"})
    g.add_edge("load_case", "answer")
    g.add_edge("answer", END)
    g.add_edge("execution_policy", END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run → ``status`` / ``summary`` / ``data`` kontrak D.3 (``chat``: ``answer``, ``citations``, ``validated``)."""
    s = outcome.state
    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": {}}
    if not s.get("answer"):
        return {"status": "ERROR", "summary": "The chat agent produced no answer.", "data": {}}

    how = {SOURCE_LLM: "numbers validated (G-04)", SOURCE_RETRY: "numbers validated after one correction (G-04)",
           SOURCE_NO_NUMBERS: "answered without numbers after G-04 failed twice", SOURCE_TEMPLATE: "template answer (G-04)",
           SOURCE_POLICY: "execution request (G-07)"}.get(s.get("answer_source"), "answered")
    used = s.get("tools_used") or []
    return {"status": "OK", "summary": f"Answer ready: {how}; tools used: {', '.join(used) or 'none'}.",
            "data": {"answer": s["answer"], "citations": s.get("citations") or [], "validated": bool(s.get("validated"))}}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None,
        features: Mapping[str, bool] | None = None) -> Json:
    """Jalankan ``chat`` untuk payload D.2. ``tools`` / ``llm`` / ``features`` hanya diisi tes.

    ``message`` dan ``history`` divalidasi SEBELUM apa pun dipanggil: tidak sah → ``ERROR`` dengan ``VALIDATION_FAILED``."""
    try:
        message, history = parse_chat_payload(payload)
    except ValueError as exc:
        return {"status": "ERROR", "summary": f"VALIDATION_FAILED: {exc}", "data": {}}
    tools = tools or ToolClient.from_payload(payload)
    configurable = {key: value for key, value in ((LLM_KEY, llm), (FEATURES_KEY, features)) if value is not None}
    initial = {**new_run_state(payload), "message": message, "history": history}
    outcome = run_guarded(build_graph(), initial, tools, configurable=configurable)
    return build_response(outcome)
