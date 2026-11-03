"""Helper bersama semua graph (A-06): jejak per node, batas G-05, dan validasi angka G-04.

- ``traced_node(step, tool)``: membungkus node LangGraph. ``log_trace`` (TL-21) ``RUNNING`` di awal, lalu
  ``OK`` / ``ERROR`` / ``AGENT_TIMEOUT`` dengan ``trace_id`` yang sama, durasi, ringkasan input/output, dan
  ``decision`` bila node mengembalikannya (alasan cabang, G-06; PRD 7.7 "Jejak", FR-19).
- ``map_parallel`` (A-15, DEC-016): jalankan pekerjaan yang tidak saling bergantung (alasan per opsi rekomendasi, narasi per bagian laporan)
  bersamaan dengan batas thread; hasil menurut urutan masukan, jadi hasil dan urutan jejak sama dengan versi serial.
- Batas G-05: maks 25 pemanggilan tool (``scan_external`` 50, DEC-022, DEC-032; ``analyze_case`` / ``resume_case`` 29, A-16) dan 120 detik per run. ``tool_calls`` di state dihitung dari
  ``ToolClient.calls`` (tanpa ``log_trace`` milik pembungkus). Terlampaui → trace ``AGENT_TIMEOUT`` dan
  ``AgentTimeout``; ``run_guarded`` mengubahnya jadi ``RunOutcome(status="AGENT_TIMEOUT")`` untuk respons D.3.
- ``with_number_validation``: validator angka G-04 lewat ``validate_numbers`` (TL-24); ulang sekali, lalu ``None``
  agar pemanggil memakai template.
- ``request_option_confirmation`` dan ``meeting_room_check`` (A-16, DEC-038, DEC-039): dua node penutup ``analyze_case`` dan
  ``resume_case``, sesudah ``recommend``. Keduanya hanya MEMBACA keputusan server (TL-23 ``confirmations_needed``, TL-28) dan meneruskannya
  (TL-11, TL-13); tingkat, peserta, dan approver dihitung server (G-06).

Tidak ada cabang berdasarkan ``mode`` atau ``trigger`` di sini: keduanya hanya lewat state ke tool dan trace.

Pemakaian node::

    @traced_node(step="map_network", tool="map_event_to_sites")
    def map_network(state: AnalyzeCaseState, config: RunnableConfig) -> dict:
        tools = get_tools(config)
        sites = tools.map_event_to_sites(state["case_id"])
        return {"candidates": sites["candidates"], "decision": "scope=LOCATION → assess_confidence"}

``config`` boleh dihilangkan bila node tidak butuh klien. Klien tool dikirim lewat
``config["configurable"]["tools"]`` (lihat ``run_guarded``).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import operator
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Iterable, Mapping, TypedDict, TypeVar

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from prompts import load_prompt
from tools.client import Json, Mode, ToolClient, ToolError

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 25  # G-05
# DEC-022, N-17 (DEC-032): scan_external <= 50 (36 request Brave + 8 sonar; ``news_config.MAX_TOOL_CALLS_PER_SCAN`` = 48, sisa 2); graph lain
# tetap 25 (PRD G-05). Task yang tidak terdaftar memakai MAX_TOOL_CALLS.
# A-16 (DEC-038, DEC-039): ``analyze_case`` dan ``resume_case`` mendapat 4 panggilan tambahan sesudah ``recommend``: TL-11 x2 (satu per jenis
# konfirmasi opsi: ``BACKUP_ACTIVATION`` dan ``PULL_IN``), TL-28 (``get_escalation``), dan TL-13 (``notify_meeting_room``). 25 + 4 = 29.
MAX_TOOL_CALLS_BY_TASK: dict[str, int] = {"scan_external": 50, "analyze_case": 29, "resume_case": 29}
MAX_RUN_SECONDS = 120.0  # G-05
LLM_MAX_PARALLEL = 4  # A-15: panggilan LLM bersamaan per node (satu per opsi rekomendasi / bagian laporan); 1 = serial
DEFAULT_RECURSION_LIMIT = 50  # G-05: batas langkah graph (superstep), selain penghitung tool_calls
TOOLS_KEY = "tools"  # config["configurable"][TOOLS_KEY] = ToolClient
LLM_KEY = "llm"  # config["configurable"][LLM_KEY] = model LangChain (opsional; default get_model)

MAX_SUMMARY_CHARS = 600  # ringkasan jadwal 60 kata ikut di output_summary (EP-04 agent_summary)
MAX_DECISION_CHARS = 500
MAX_VALUE_CHARS = 80

# Kunci state yang tidak ikut ringkasan input otomatis.
_BOOKKEEPING = frozenset({"run_id", "task", "mode", "trigger", "case_id", "tool_calls", "started_at", "scan_partition"})


class RunState(TypedDict):
    """Field wajib state semua graph (CLAUDE.md bagian 2, aturan 1). Graph memperluasnya per task."""

    run_id: str | None
    task: str
    mode: Mode | None
    trigger: str | None
    case_id: str | None
    # N-10 (DEC-022): reducer penjumlahan. ``traced_node`` menulis DELTA node, bukan nilai absolut, supaya dua node yang berjalan
    # bersamaan (fan-out) tidak saling menimpa. Nilai awal 0 dari ``new_run_state``.
    tool_calls: Annotated[int, operator.add]
    started_at: float  # epoch detik (time.time)


def _now() -> float:
    return time.time()


def new_run_state(payload: Mapping[str, Any]) -> RunState:
    """State awal dari payload kontrak D.2. ``started_at`` dihitung dari sini (batas 120 detik)."""
    return {
        "run_id": payload.get("run_id"),
        "task": str(payload["task"]),
        "mode": payload.get("mode"),
        "trigger": payload.get("trigger"),
        "case_id": payload.get("case_id"),
        "tool_calls": 0,
        "started_at": _now(),
    }



# --------------------------------------------------------------------------- paralel dengan batas (A-15, DEC-016)

T = TypeVar("T")
R = TypeVar("R")


def map_parallel(fn: Callable[[T], R], items: Iterable[T], *, max_workers: int) -> list[R]:
    """``[fn(x) for x in items]`` dengan maks ``max_workers`` thread. Hasil menurut urutan ``items`` (deterministik).

    - Tanpa lebih dari satu item, atau ``max_workers <= 1``: dijalankan serial di thread pemanggil (tanpa thread baru).
    - Exception: yang dilempar item paling awal (menurut urutan masukan) dilempar ulang, sama dengan versi serial; item yang belum
      mulai dibatalkan. Item yang sedang berjalan tidak ditunggu (agar batas 120 detik G-05 tidak tertahan satu panggilan model).
    - Konteks (``contextvars``: span OpenTelemetry, callback LangChain) disalin ke tiap thread.
    - ``fn`` tidak boleh berbagi state yang bisa ditulis antar-item; ``ToolClient`` dan ``trace_event`` aman dipakai bersamaan.
    """
    work = list(items)
    if len(work) <= 1 or max_workers <= 1:
        return [fn(item) for item in work]
    pool = ThreadPoolExecutor(max_workers=min(max_workers, len(work)), thread_name_prefix="sentra-par")
    futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in work]
    try:
        return [future.result() for future in futures]
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=False)  # keberhasilan: semua selesai; galat: jangan menunggu panggilan model yang masih berjalan


# --------------------------------------------------------------------------- batas G-05


class AgentTimeout(Exception):
    """Batas G-05 terlampaui (> 25 tool call, 50 untuk ``scan_external``; > 120 detik; atau batas langkah graph)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def max_tool_calls(task: str | None) -> int:
    """Batas tool call G-05 untuk ``task``: 25, kecuali task yang terdaftar di ``MAX_TOOL_CALLS_BY_TASK`` (``scan_external`` 50,
    ``analyze_case`` / ``resume_case`` 29)."""
    return MAX_TOOL_CALLS_BY_TASK.get(str(task), MAX_TOOL_CALLS)


def budget_exceeded(state: Mapping[str, Any]) -> str | None:
    """Alasan bila state melampaui batas G-05, selain itu ``None``. Tepat batas tool call (25; ``scan_external`` 50;
    ``analyze_case`` / ``resume_case`` 29) atau 120 detik masih boleh."""
    calls = int(state.get("tool_calls") or 0)
    limit = max_tool_calls(state.get("task"))
    if calls > limit:
        return f"Tool call limit exceeded ({calls} > {limit})"
    started_at = state.get("started_at")
    if started_at is not None:
        elapsed = _now() - float(started_at)
        if elapsed > MAX_RUN_SECONDS:
            return f"Run time limit exceeded ({elapsed:.0f}s > {MAX_RUN_SECONDS:.0f}s)"
    return None


def get_tools(config: Mapping[str, Any] | None) -> ToolClient:
    tools = ((config or {}).get("configurable") or {}).get(TOOLS_KEY)
    if tools is None:
        raise RuntimeError(f"config['configurable']['{TOOLS_KEY}'] must hold the run's ToolClient (see run_guarded)")
    return tools


def get_llm(config: Mapping[str, Any] | None, *, read_timeout: int | None = None,
            max_attempts: int | None = None) -> Any:
    """Model untuk node LLM: dari config (tes / injeksi) atau ``get_model()`` (diimpor malas, SDK berat).
    ``read_timeout`` dan ``max_attempts`` hanya berlaku untuk model bawaan (bukan yang diinjeksi)."""
    llm = ((config or {}).get("configurable") or {}).get(LLM_KEY)
    if llm is None:
        from model.load import CHAT_MODEL, get_model

        llm = get_model(CHAT_MODEL, read_timeout=read_timeout, max_attempts=max_attempts)
    return llm


# --------------------------------------------------------------------------- jejak


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    return _short(value, MAX_VALUE_CHARS)


def summarize(values: Mapping[str, Any], skip: frozenset[str] | set[str] = frozenset()) -> str | None:
    """``k=v; k=[3 items]; k={5 keys}`` — isi koleksi tidak dicetak, hanya ukurannya."""
    parts = [f"{k}={_describe(v)}" for k, v in values.items() if k not in skip and v is not None]
    return _short("; ".join(parts), MAX_SUMMARY_CHARS) if parts else None


# Buku besar panggilan ``log_trace`` per ToolClient (N-10, DEC-022). ``ToolClient.calls`` menghitung SEMUA panggilan, termasuk
# ``log_trace`` milik pembungkus. Pada satu node, jendela hitungnya berada di antara dua trace sendiri, jadi trace tidak ikut.
# Dua node paralel (fan-out) berbagi satu ToolClient: trace saudaranya bisa jatuh di dalam jendela. Karena itu setiap trace dicatat
# di sini, dan ``traced_node`` mengurangkannya dari selisih ``calls``. Kunci dipegang selama trace berjalan supaya pembacaan
# (``calls``, jumlah trace) selalu konsisten.
_TRACE_LOCK = threading.Lock()
_TRACE_CALLS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()


def _call_counters(tools: ToolClient) -> tuple[int, int]:
    """(``tools.calls``, jumlah panggilan ``log_trace`` pembungkus) dibaca bersamaan."""
    with _TRACE_LOCK:
        try:
            return tools.calls, _TRACE_CALLS.get(tools, 0)
        except TypeError:  # klien tiruan yang tak bisa dirujuk lemah: tanpa koreksi
            return tools.calls, 0


def _trace(tools: ToolClient, state: Mapping[str, Any], step: str, tool: str | None, status: str, *,
           trace_id: str | None = None, **fields: Any) -> str | None:
    """Satu panggilan TL-21. Kegagalan jejak tidak boleh menjatuhkan run: dicatat ke log, node lanjut."""
    with _TRACE_LOCK:
        try:
            resp = tools.log_trace(task=state["task"], step=step, status=status, case_id=state.get("case_id"),
                                   tool=tool, trace_id=trace_id, **fields)  # type: ignore[arg-type]
        except ToolError as exc:
            log.warning("log_trace failed for step %s (%s): %s", step, status, exc)
            return trace_id
        finally:
            try:
                _TRACE_CALLS[tools] = _TRACE_CALLS.get(tools, 0) + 1
            except TypeError:
                pass
    return resp.get("trace_id") or trace_id


def trace_event(tools: ToolClient, state: Mapping[str, Any], step: str, status: str, *, tool: str | None = None,
                **fields: Any) -> str | None:
    """Satu baris jejak TAMBAHAN di dalam node, mis. ``LLM_OVERRIDDEN`` (G-06). Dicatat di buku besar jejak, jadi tidak ikut
    ``tool_calls`` (G-05) seperti trace pembungkus. Kegagalan jejak tidak menjatuhkan node."""
    return _trace(tools, state, step, tool, status, **fields)


def traced_node(step: str, tool: str | None = None, *,
                keep_decision: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Dekorator node: jejak ``RUNNING`` → ``OK`` / ``ERROR`` / ``AGENT_TIMEOUT`` dan penghitung ``tool_calls``.

    Node mengembalikan ``dict`` pembaruan state (atau ``None``). Kunci opsional di dalamnya:

    - ``decision``: alasan cabang → field ``decision`` trace (tetap ada di pembaruan state).
    - ``input_summary`` / ``output_summary``: menggantikan ringkasan otomatis; hanya untuk trace (dibuang).
    - ``trace_status``: ``"SKIPPED"`` (langkah sengaja dilewati, mis. fitur mati) atau ``"ERROR"`` (langkah gagal
      tanpa exception, mis. sumber eksternal habis); default ``"OK"``. Hanya untuk trace (dibuang).
    - ``extra_tool_calls``: pemanggilan tool di luar ``ToolClient`` (Brave, sonar) yang ikut batas G-05 (dibuang).

    Pembungkus menulis ``tool_calls`` sebagai DELTA node (``RunState.tool_calls`` punya reducer penjumlahan, N-10 / DEC-022):
    jumlah panggilan ``ToolClient`` selama node berjalan (tanpa trace pembungkus, juga trace node saudara yang paralel) +
    ``extra_tool_calls``. Pemeriksaan batas dilakukan sebelum node (run yang sudah melewati batas tidak memulai langkah
    baru) dan sesudahnya, memakai ``state.tool_calls`` + delta.

    ``keep_decision=False``: ``decision`` hanya dikirim ke trace, tidak ditulis ke state. Dipakai node yang berjalan bersamaan
    dengan node lain (dua penulisan ``decision`` dalam satu superstep ditolak LangGraph).
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        wants_config = "config" in inspect.signature(fn).parameters

        @functools.wraps(fn)
        def node(state: Mapping[str, Any], config: RunnableConfig) -> Json:
            tools = get_tools(config)

            reason = budget_exceeded(state)
            if reason:
                _trace(tools, state, step, tool, "AGENT_TIMEOUT", decision=_short(reason, MAX_DECISION_CHARS),
                       output_summary="step not started")
                raise AgentTimeout(reason)

            generic_input = summarize(state, _BOOKKEEPING)
            trace_id = _trace(tools, state, step, tool, "RUNNING", input_summary=generic_input)
            calls_before, traces_before = _call_counters(tools)
            started = time.perf_counter()

            def elapsed_ms() -> int:
                return int((time.perf_counter() - started) * 1000)

            try:
                update = fn(state, config) if wants_config else fn(state)
            except AgentTimeout as exc:
                _trace(tools, state, step, tool, "AGENT_TIMEOUT", trace_id=trace_id, duration_ms=elapsed_ms(),
                       decision=_short(exc.reason, MAX_DECISION_CHARS))
                raise
            except Exception as exc:
                _trace(tools, state, step, tool, "ERROR", trace_id=trace_id, duration_ms=elapsed_ms(),
                       input_summary=generic_input,
                       output_summary=_short(f"{type(exc).__name__}: {exc}", MAX_SUMMARY_CHARS))
                raise

            if update is None:
                update = {}
            if not isinstance(update, dict):
                raise TypeError(f"node '{step}' must return a dict state update or None, got {type(update).__name__}")
            update = dict(update)
            ok_status = update.pop("trace_status", "OK")
            if ok_status not in ("OK", "SKIPPED", "ERROR"):
                raise ValueError(f"node '{step}': trace_status must be OK, SKIPPED or ERROR, got {ok_status!r}")
            extra_calls = int(update.pop("extra_tool_calls", 0) or 0)
            input_summary = update.pop("input_summary", None) or generic_input
            output_summary = update.pop("output_summary", None) or summarize(update, {"decision", "tool_calls"})
            decision = update.get("decision")
            if not keep_decision:
                update.pop("decision", None)

            calls_after, traces_after = _call_counters(tools)
            delta = max(0, (calls_after - calls_before) - (traces_after - traces_before)) + extra_calls
            update["tool_calls"] = delta
            reason = budget_exceeded({**state, "tool_calls": int(state.get("tool_calls") or 0) + delta})
            if reason and not decision:
                decision = reason
            _trace(
                tools, state, step, tool, "AGENT_TIMEOUT" if reason else ok_status, trace_id=trace_id,
                duration_ms=elapsed_ms(),
                input_summary=_short(input_summary, MAX_SUMMARY_CHARS) if input_summary else None,
                output_summary=_short(output_summary, MAX_SUMMARY_CHARS) if output_summary else None,
                decision=_short(decision, MAX_DECISION_CHARS) if decision else None,
            )
            if reason:
                raise AgentTimeout(reason)
            return update

        # LangGraph hanya meneruskan `config` bila parameternya bertipe RunnableConfig di signature yang terlihat.
        node.__signature__ = inspect.Signature([  # type: ignore[attr-defined]
            inspect.Parameter("state", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("config", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=RunnableConfig),
        ])
        node.step = step  # type: ignore[attr-defined]
        return node

    return decorate


# --------------------------------------------------------------------------- menjalankan graph dengan batas


@dataclass
class RunOutcome:
    """Hasil ``run_guarded``. ``state`` = nilai state terakhir yang berhasil diamati."""

    status: str  # "OK" | "AGENT_TIMEOUT" (D.3; ERROR ditangani dispatcher main.py)
    state: dict[str, Any]
    message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def timed_out(self) -> bool:
        return self.status == "AGENT_TIMEOUT"


def run_guarded(graph: Any, initial_state: Mapping[str, Any], tools: ToolClient, *,
                recursion_limit: int = DEFAULT_RECURSION_LIMIT,
                configurable: Mapping[str, Any] | None = None) -> RunOutcome:
    """Jalankan graph terkompilasi dengan batas G-05.

    ``AgentTimeout`` (dari ``traced_node``) dan ``GraphRecursionError`` menjadi ``status="AGENT_TIMEOUT"``;
    keduanya sudah/akan tercatat di trace. ``configurable`` = isi tambahan ``config["configurable"]`` (mis. ``llm``).
    Exception lain tidak ditangkap (``main.dispatch`` mengubahnya
    menjadi ``ERROR``; node yang gagal sudah menulis trace ``ERROR``).
    """
    config: RunnableConfig = {"configurable": {**(configurable or {}), TOOLS_KEY: tools},
                              "recursion_limit": recursion_limit}
    last: dict[str, Any] = dict(initial_state)
    try:
        for last in graph.stream(dict(initial_state), config, stream_mode="values"):
            pass
    except AgentTimeout as exc:
        return RunOutcome("AGENT_TIMEOUT", last, exc.reason)
    except GraphRecursionError:
        reason = f"Graph step limit reached ({recursion_limit})"
        _trace(tools, initial_state, "agent_run", None, "AGENT_TIMEOUT", decision=reason,
               output_summary="graph stopped by recursion limit")
        return RunOutcome("AGENT_TIMEOUT", last, reason)
    return RunOutcome("OK", last)


# --------------------------------------------------------------------------- validasi angka G-04


def message_text(message: Any) -> str:
    """Teks pesan model; ``content`` boleh ``str`` atau daftar blok."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(b if isinstance(b, str) else b.get("text", "") for b in content if b)
    return str(content).strip()


def correction_instruction(offending_numbers: list[str]) -> str:
    """Instruksi koreksi (English, untuk LLM) setelah validator menolak."""
    shown = ", ".join(str(n) for n in offending_numbers) or "(unspecified)"
    return (
        f"Your previous text contained numbers that are not in the provided data: {shown}. "
        "Rewrite it using only numbers that appear in the provided JSON. "
        "Remove any number you cannot find there. Keep the same language and structure."
    )


def with_number_validation(text: str, allowed: Any, *, tools: ToolClient,
                           regenerate: Callable[[str], str]) -> str | None:
    """Validator angka G-04 (TL-24).

    ``text`` lolos → dikembalikan. Gagal → ``regenerate(instruksi_koreksi)`` dipanggil SEKALI dan hasilnya
    divalidasi lagi; lolos → teks baru. Gagal kedua (atau teks pengganti kosong) → ``None``: pemanggil memakai
    template tanpa narasi bebas. ``allowed`` = JSON yang diberikan ke LLM pada pemanggilan itu.

    Kesalahan tool (``ToolError``) tidak ditelan: server tak terjangkau bukan berarti angka valid atau tidak.
    """
    result = tools.validate_numbers(text, allowed)
    if result.get("passed"):
        return text

    retry = regenerate(correction_instruction(list(result.get("offending_numbers") or [])))
    if not isinstance(retry, str) or not retry.strip():
        return None
    retry = retry.strip()
    if tools.validate_numbers(retry, allowed).get("passed"):
        return retry
    return None


# --------------------------------------------------------------------------- konfirmasi opsi dan rapat (A-16, DEC-038, DEC-039)

# Jenis konfirmasi opsi pada TL-23 ``confirmations_needed`` menurut urutan pemanggilan TL-11. ``IMPACT`` bukan milik node ini: itu
# permintaan keyakinan supplier (R-12) yang dikirim ``request_verification``.
CONFIRMATION_TYPES = ("BACKUP_ACTIVATION", "PULL_IN")
MEETING_LABELS = {"MEETING_ROOM": "Meeting Room", "EXECUTIVE_MEETING_ROOM": "Executive Meeting Room"}
TIER_KINDS = {1: "MEETING_ROOM", 2: "EXECUTIVE_MEETING_ROOM"}  # ``escalation_tier`` (A.4) → ``meeting_kind``
AGENDA_PROMPT = "meeting_agenda"
AGENDA_TIMEOUT_S = 25.0  # satu panggilan LLM untuk agenda; lewat batas ini agenda = null (server memakai template R-22)
AGENDA_MAX_WORDS = 120  # prompt: "at most 120 words"
AGENDA_MAX_CHARS = 1500  # batas TL-13 ``agenda``


def _case_id_of(state: Mapping[str, Any]) -> str:
    case_id = state.get("case_id")
    if not case_id:
        raise ValueError(f"{state.get('task')} requires case_id")
    return case_id


@traced_node(step="request_option_confirmation", tool="request_supplier_confirmation")
def request_option_confirmation(state: Mapping[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Meminta konfirmasi supplier untuk opsi yang direkomendasikan bila server bilang perlu (TL-23 ``confirmations_needed``):
    Opsi C → ``BACKUP_ACTIVATION``, Opsi E → ``PULL_IN`` (R-12 v2). Satu panggilan TL-11 per jenis, ``site_ids`` urut.
    Agen tidak memilih jenis atau site sendiri; ``preliminary`` tidak diubah di sini."""
    wanted: dict[str, list[str]] = {}
    for item in state.get("confirmations_needed") or []:
        if item.get("request_type") in CONFIRMATION_TYPES:
            wanted.setdefault(item["request_type"], []).append(item["site_id"])
    if not wanted:
        return {"confirmations_requested": [], "trace_status": "SKIPPED",
                "decision": "no supplier confirmation needed for the recommended option"}

    tools = get_tools(config)
    case_id = _case_id_of(state)
    requested: list[Json] = []
    parts: list[str] = []
    for request_type in CONFIRMATION_TYPES:
        sites = sorted(set(wanted.get(request_type, ())))
        if not sites:
            continue
        result = tools.request_supplier_confirmation(case_id, sites, request_type)  # type: ignore[arg-type]
        asked = {r["site_id"] for r in result.get("requested") or []}
        asked |= {x["site_id"] for x in result.get("skipped") or [] if x.get("reason") == "ALREADY_REQUESTED"}
        done = [site for site in sites if site in asked]
        requested += [{"site_id": site, "request_type": request_type} for site in done]
        parts.append(f"{request_type} requested from {', '.join(done) or '-'}")

    option = state.get("recommended_option")
    subject = f"Option {option}" if option else "The recommended option"
    return {
        "confirmations_requested": requested,
        "decision": f"{subject} needs supplier confirmation → {'; '.join(parts)}",
        "input_summary": "; ".join(f"{t}: {', '.join(sorted(set(sites)))}" for t, sites in wanted.items()),
    }


def write_agenda(escalation: Mapping[str, Any], state: Mapping[str, Any], config: RunnableConfig) -> str | None:
    """Agenda rapat dari LLM (prompt ``meeting_agenda``): satu panggilan, batas ``AGENDA_TIMEOUT_S``. Galat, timeout, teks kosong,
    atau terlalu panjang → ``None`` (server memakai template R-22). Angka divalidasi G-04 di TL-13, bukan di sini.

    Masukan LLM = data TL-28 + ``rule_reason`` dan ``recommended_option`` dari state. ``meeting`` dan ``decision`` (rapat dan catatan
    lama, memuat tautan Zoom dan teks bebas) tidak ikut: agenda hanya membahas keadaan sekarang."""
    payload = {k: v for k, v in escalation.items() if k not in ("meeting", "decision")}
    payload["rule_reason"] = state.get("rule_reason")
    payload["recommended_option"] = state.get("recommended_option") or escalation.get("recommended_option")
    messages = [("system", load_prompt(AGENDA_PROMPT)), ("human", json.dumps(payload, ensure_ascii=False))]

    def call() -> str:
        llm = get_llm(config, read_timeout=int(AGENDA_TIMEOUT_S), max_attempts=1)
        return message_text(llm.invoke(messages))

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        text = pool.submit(call).result(timeout=AGENDA_TIMEOUT_S)
    except FutureTimeout:
        log.warning("Meeting agenda LLM timed out after %ss → agenda null", AGENDA_TIMEOUT_S)
        return None
    except Exception as exc:  # model / konfigurasi / jaringan model: rapat tetap dibuat dengan agenda template
        log.warning("Meeting agenda LLM failed: %s → agenda null", type(exc).__name__)
        return None
    finally:
        pool.shutdown(wait=False)

    from graphs.schedule import fit_words  # impor lambat: graphs.schedule mengimpor modul ini

    fitted = fit_words(text, AGENDA_MAX_WORDS) if text else None
    return fitted if fitted and len(fitted) <= AGENDA_MAX_CHARS else None


def _meeting_result(tier: int, label: str, codes: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Respons TL-13 v2 → pembaruan state + ``decision`` trace."""
    status = result.get("status")
    shown_tier = result.get("tier", tier)
    head = f"tier {shown_tier} {label}{f' ({codes})' if codes else ''}"
    if status == "CREATED":
        meeting = result.get("meeting") or {}
        notification = result.get("notification") or {}
        email = notification.get("status") or "UNKNOWN"
        if meeting.get("status") == "FAILED":
            decision = f"{head} → Zoom failed, email {email} without a link"
        else:
            decision = (f"{head} → Zoom meeting scheduled, email {email} to "
                        f"{len(notification.get('recipients') or [])} roles")
        if (result.get("agenda_validator") or {}).get("fallback_template_used"):
            decision += "; agenda template used"
        return {"meeting_status": "CREATED", "decision": decision, "output_summary": f"tier {shown_tier}; CREATED"}
    if status == "NOT_NEEDED":
        return {"meeting_status": "NOT_NEEDED", "trace_status": "SKIPPED", "output_summary": "NOT_NEEDED",
                "decision": f"server reports tier {shown_tier} needs no new meeting → skipped"}
    if status == "SKIPPED_FEATURE_OFF":
        return {"meeting_status": "SKIPPED_FEATURE_OFF", "trace_status": "SKIPPED", "output_summary": "SKIPPED_FEATURE_OFF",
                "decision": f"{head} but the meeting room feature is off → skipped"}
    return {"meeting_status": "ERROR", "trace_status": "ERROR", "output_summary": str(status),
            "decision": f"unexpected notify_meeting_room status {status}"}


@traced_node(step="meeting_room_check", tool="notify_meeting_room")
def meeting_room_check(state: Mapping[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Meeting Room / Executive Meeting Room (R-22, DEC-039), SESUDAH ``recommend`` karena tingkat bergantung pada opsi yang
    direkomendasikan. Membaca tingkat dari TL-28; rapat hanya diminta (TL-13) bila tingkat naik di atas ``meeting_tier`` dan F-07
    aktif (``needs_meeting``). Agen tidak mengirim tingkat, peserta, atau approver."""
    tools = get_tools(config)
    case_id = _case_id_of(state)
    try:
        escalation = tools.get_escalation(case_id)
    except ToolError as exc:
        if exc.status != 404:
            raise
        return {"meeting_status": "ERROR", "trace_status": "SKIPPED", "output_summary": "TL-28 not available",
                "decision": "escalation not available (backend without TL-28)"}

    tier = int(escalation.get("tier") or 0)
    meeting_tier = int(escalation.get("meeting_tier") or 0)
    codes = ", ".join(str(r.get("code")) for r in escalation.get("reasons") or [])
    skipped = {"escalation_tier": tier, "trace_status": "SKIPPED", "meeting_status": "NOT_NEEDED",
               "output_summary": f"tier {tier}; NOT_NEEDED"}
    if tier == 0:
        return {**skipped, "decision": "escalation tier 0 → handled in the app, no meeting"}
    if tier <= meeting_tier:
        return {**skipped, "decision": f"tier {tier} already has a meeting → no new meeting or email"}
    if not escalation.get("needs_meeting"):
        return {**skipped, "meeting_status": "SKIPPED_FEATURE_OFF", "output_summary": f"tier {tier}; SKIPPED_FEATURE_OFF",
                "decision": f"tier {tier} ({codes}) but the meeting room feature is off → skipped"}

    label = MEETING_LABELS.get(escalation.get("kind") or TIER_KINDS.get(tier), "Meeting Room")
    agenda = write_agenda(escalation, state, config)
    result = tools.notify_meeting_room(case_id, agenda)
    return {"escalation_tier": tier, **_meeting_result(tier, label, codes, result)}


def escalation_data(state: Mapping[str, Any]) -> Json:
    """Field D.3 v1.11 yang ditambahkan ``analyze_case`` dan ``resume_case`` ke ``data`` (hanya yang sudah ada di state)."""
    return {key: state[key] for key in ("confirmations_requested", "escalation_tier", "meeting_status")
            if state.get(key) is not None}


def escalation_clauses(state: Mapping[str, Any]) -> list[str]:
    """Klausa ringkasan D.3: ``escalation tier {t}`` dan, bila rapat dibuat, ``Meeting Room scheduled`` /
    ``Executive Meeting Room scheduled``."""
    tier = state.get("escalation_tier")
    parts = [f"escalation tier {tier}"] if tier is not None else []
    label = MEETING_LABELS.get(TIER_KINDS.get(tier, ""))
    if state.get("meeting_status") == "CREATED" and label:
        parts.append(f"{label} scheduled")
    return parts
