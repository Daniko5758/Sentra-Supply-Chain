"""Entrypoint runtime AgentCore SENTRA.

Membaca payload kontrak D.2, memilih handler menurut ``task``, dan selalu mengembalikan
``{"result": "<JSON string>"}`` (kontrak D.3). Exception tidak boleh lolos ke runtime.
"""

import importlib
import json
import logging
import threading
from types import ModuleType
from typing import Any, Callable

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from model.load import get_model  # ringan: SDK SAP dan langchain_aws baru dimuat saat model pertama dipakai (A-15)

# A-15 (DEC-016): impor main.py tidak memuat SAP Cloud SDK for AI, langchain_aws, langgraph, observability, maupun graph apa pun.
# Modul graph dimuat per task saat task itu pertama dipanggil (``load_graph``) dan di-cache per proses.

app = BedrockAgentCoreApp()
log = app.logger

PING_MODEL = "claude-4.6-sonnet"
PING_PROMPT = "reply OK"
MAX_ERROR_CHARS = 300

Payload = dict[str, Any]
Handler = Callable[[Payload], Payload]


def _result(
    payload: Payload,
    task: str | None,
    status: str,
    summary: str,
    data: Payload | None = None,
) -> Payload:
    return {
        "status": status,
        "run_id": payload.get("run_id"),
        "task": task,
        "case_id": payload.get("case_id"),
        "summary": summary,
        "data": data if data is not None else {},
    }


def _text(content: Any) -> str:
    """Isi pesan model sebagai teks (content bisa str atau daftar blok)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b if isinstance(b, str) else b.get("text", "") for b in content if b]
        return "".join(parts).strip()
    return str(content).strip()


def _ping(payload: Payload) -> Payload:
    """Cek kesehatan: satu panggilan model, tanpa tool."""
    answer = _text(get_model(PING_MODEL).invoke(PING_PROMPT).content)
    return _result(
        payload, "ping", "OK", f"Model {PING_MODEL} replied.", {"model": PING_MODEL, "answer": answer}
    )


GRAPH_TASKS = ("analyze_case", "resume_case", "recommend", "generate_report", "chat", "scan_external")

_graphs: dict[str, ModuleType] = {}
_graphs_lock = threading.Lock()
_observability_started = False


def _start_observability() -> None:
    """Observability template (FR-26): instrumentasi LangChain. Dipanggil sekali per proses, sebelum graph pertama dimuat (mengimpor
    LangChain dan OpenTelemetry, ±1,7 s), jadi tidak menambah waktu start maupun task ``ping``. Gagal -> peringatan, agen tetap jalan."""
    global _observability_started
    if _observability_started:
        return
    _observability_started = True
    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor

        LangchainInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).warning("LangChain instrumentation disabled: %s", exc)


def load_graph(task: str) -> ModuleType:
    """Modul ``graphs.<task>`` (``importlib``), dimuat saat pertama dibutuhkan lalu di-cache per proses."""
    if task not in GRAPH_TASKS:
        raise ValueError(f"No graph for task {task!r}")
    with _graphs_lock:
        module = _graphs.get(task)
        if module is None:
            _start_observability()
            module = _graphs[task] = importlib.import_module(f"graphs.{task}")
        return module


def _graph_handler(task: str) -> Handler:
    def handler(payload: Payload) -> Payload:
        out = load_graph(task).run(payload)
        return _result(payload, task, out["status"], out["summary"], out["data"])

    return handler


def _not_implemented(task: str) -> Handler:
    def handler(payload: Payload) -> Payload:
        return _result(payload, task, "ERROR", f"Task '{task}' not implemented")

    return handler


# Task yang belum punya graph mengembalikan ERROR "not implemented"; ganti entri ini
# saat graph-nya dibuat (A-07 dst.).
HANDLERS: dict[str, Handler] = {
    **{
        t: _not_implemented(t)
        for t in (
            "daily_schedule_check",
        )
    },
    **{t: _graph_handler(t) for t in GRAPH_TASKS},
    "ping": _ping,
}


def _load_json(value: Any) -> Any:
    """str/bytes berisi JSON -> objek; selain itu dikembalikan apa adanya (gagal parse -> None)."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def dispatch(payload: Any) -> Payload:
    """Payload D.2 -> dict hasil D.3. Tidak pernah melempar exception."""
    payload = _load_json(payload)
    if isinstance(payload, dict) and "task" not in payload:
        # `agentcore dev` / `agentcore invoke` hanya mengirim teks dan membungkusnya sebagai
        # {"prompt": "<teks>"}; buka bungkusnya bila isinya payload D.2.
        inner = _load_json(payload.get("prompt"))
        if isinstance(inner, dict) and "task" in inner:
            payload = inner
    if not isinstance(payload, dict):
        return _result({}, None, "ERROR", "Payload must be a JSON object")

    task = payload.get("task")
    handler = HANDLERS.get(task) if isinstance(task, str) else None
    if handler is None:
        shown = task if isinstance(task, str) else None
        return _result(payload, shown, "ERROR", f"Unknown task: {shown!r}")

    try:
        return handler(payload)
    except Exception as exc:
        log.exception("Task %s failed", task)
        detail = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        return _result(payload, task, "ERROR", f"Task '{task}' failed: {detail}")


def to_response(result: Payload) -> dict[str, str]:
    return {"result": json.dumps(result, ensure_ascii=False)}


@app.entrypoint
def invoke(payload, context=None):
    log.info("Invoking task=%s", payload.get("task") if isinstance(payload, dict) else None)
    return to_response(dispatch(payload))


if __name__ == "__main__":
    app.run()
