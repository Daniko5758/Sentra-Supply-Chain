"""Calls the SENTRA agent (AgentCore runtime, contract D) from a background worker, and keeps the run queue.

Rules (PRD 6.4, CLAUDE.md): the agent is called only here, never inside a user request; one run per case at a time,
runs of different cases in parallel; triggers that arrive while a case is busy merge into one `resume_case`
(`services.runs.request_resume`); when a scan finishes, `analyze_case` is queued for the cases it touched (TL-05).

The queue is the `agent_run` table: a run is QUEUED until the dispatcher claims it (RUNNING, and the case moves
NEW -> ANALYZING), then the answer sets the run status and the case status (contract D.3: "sentra-api uses this answer
for the run status and the case status"). The agent answers are read tolerantly: unknown `data` keys never fail a run.
"""

import asyncio
import json
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.case_state import CLOSED_STATUSES, CaseEvent
from sentra_api.db import utc_now
from sentra_api.errors import ApiError
from sentra_api.models import AgentRun, Case, SupplierRequest
from sentra_api.priority import round1
from sentra_api.routers.internal_tools import TOKEN_HEADER
from sentra_api.services.case_service import apply_event
from sentra_api.services.case_tools import add_trace
from sentra_api.services.runs import GENERATE_REPORT_TASK, SCAN_TASK, enqueue_run
from sentra_api.services.system_status import record_integration_call, record_model_call

log = logging.getLogger(__name__)

PING_TASK = "ping"  # warm-up and keep-alive (contract D.1, D.2): one short model call, no tool, never an agent_run
REGION = "ap-southeast-2"
READ_TIMEOUT_S = 150  # longer than the 120 s run limit of the agent (G-05); boto3's default of 60 s is too short
INTEGRATION = "agentcore"
MAX_PARALLEL_RUNS = 4
POLL_S = 2.0  # safety net: the dispatcher also wakes whenever something queues a run
RESULT_STATUSES = ("OK", "ERROR", "AGENT_TIMEOUT")
CASE_SESSION_TASKS = ("analyze_case", "resume_case", "recommend", "generate_report")  # contract D.1
ERROR_MESSAGE_LIMIT = 200
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

# DEC-046: a call that failed before the agent got the request (the connection could not be opened, or AWS closed a kept
# connection within FAST_FAILURE_S) is tried again, at most MAX_ATTEMPTS calls in all. After the first such failure the boto3
# client is built again, so every kept connection (several, one per parallel run) is dropped; before the third attempt the
# runner waits RETRY_PAUSE_S. Anything else (read timeout, a late close, an AWS error, the agent's own ERROR) is not repeated.
MAX_ATTEMPTS = 3
FAST_FAILURE_S = 1.0
RETRY_PAUSE_S = 1.0
NOT_SENT_ERRORS = (ConnectionClosedError, EndpointConnectionError, ConnectTimeoutError)


def make_agentcore_client() -> Any:
    """No automatic boto3 retry: a retry could run the same task twice (DEC-046 repeats only calls that never reached the
    agent). Credentials come from the usual AWS chain (CF: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY of `sentra-cf-invoker`),
    resolved at the first call."""
    return boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        config=Config(read_timeout=READ_TIMEOUT_S, connect_timeout=10, retries={"total_max_attempts": 1}),
    )


# One client per process (PRD 6.4); replaced by a new one only after a call that failed before reaching the agent (DEC-046).
agentcore_client = make_agentcore_client()


class AgentInvokeError(Exception):
    """The agent did not give a usable answer. `kind` is TIMEOUT (run -> AGENT_TIMEOUT) or ERROR (run -> ERROR).

    `code` is the short, secret-free reason kept in `integration_call.message`; `called` is False when no call was made.
    """

    def __init__(self, kind: str, message: str, *, code: str | None = None, called: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code or kind
        self.called = called


class AgentRunner(Protocol):
    integration: str | None  # name recorded in `integration_call`; None when no outside service is called

    def invoke(self, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        """Run one task and return the D.3 answer (`status`, `summary`, `data`, ...). May raise AgentInvokeError."""


# --- response parsing (D.3) -------------------------------------------------------------------------------------------

def read_response_text(response: dict[str, Any]) -> str:
    """Body of an `invoke_agent_runtime` response as text; for `text/event-stream`, the `data:` lines joined first."""
    body = response["response"]
    if "text/event-stream" in (response.get("contentType") or ""):
        chunks = []
        for raw in body.iter_lines():
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line.startswith("data:"):
                chunk = line[len("data:"):]
                chunks.append(chunk[1:] if chunk.startswith(" ") else chunk)
        return "".join(chunks)
    data = body.read()
    return data.decode("utf-8") if isinstance(data, bytes) else data


def _bad_response(message: str) -> AgentInvokeError:
    return AgentInvokeError("ERROR", f"Unreadable AgentCore response: {message}", code="BAD_RESPONSE")


def parse_agent_result(text: str) -> dict[str, Any]:
    """`{"result": "<json string>"}` -> the D.3 object (two JSON layers). A body that already is the D.3 object is accepted."""
    try:
        outer = json.loads(text)
    except ValueError:
        raise _bad_response("not JSON") from None
    if not isinstance(outer, dict):
        raise _bad_response("not a JSON object")
    inner = outer["result"] if "result" in outer else outer
    if isinstance(inner, (str, bytes)):
        try:
            inner = json.loads(inner)
        except ValueError:
            raise _bad_response("`result` is not JSON") from None
    if not isinstance(inner, dict) or inner.get("status") not in RESULT_STATUSES:
        raise _bad_response("no valid `status`")
    return inner


# --- runners ---------------------------------------------------------------------------------------------------------

class AgentCoreRunner:
    """Calls the deployed runtime with boto3 (contract D.1)."""

    integration = INTEGRATION

    def __init__(self, runtime_arn: str | None, client: Any = None, *, client_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.runtime_arn = runtime_arn
        self.client = client if client is not None else agentcore_client
        # the default client is rebuilt with make_agentcore_client (looked up at call time, so tests can replace it)
        self._client_factory = client_factory if client is not None else (lambda: make_agentcore_client())
        self._clock, self._sleep = clock, sleep

    def invoke(self, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        if not self.runtime_arn:
            raise AgentInvokeError(
                "ERROR", "AGENT_RUNTIME_ARN is not configured (or set AGENT_RUNNER=fake for local tests).",
                code="NOT_CONFIGURED", called=False,
            )
        callback = urlparse(str(payload.get("callback_base_url") or ""))
        # A ping calls no tool, so it does not need an address the agent can reach.
        if payload.get("task") != PING_TASK and (callback.scheme != "https" or callback.hostname in LOCAL_HOSTS):
            # The agent runs in AWS and calls the tools back: it rejects plain http and cannot reach localhost.
            raise AgentInvokeError(
                "ERROR", "The tool callback URL must be the public https URL of sentra-api: set APP_BASE_URL.",
                code="NOT_CONFIGURED", called=False,
            )
        body = json.dumps(payload).encode()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = self._clock()
            try:
                return parse_agent_result(self._call(body, session_id))
            except NOT_SENT_ERRORS as error:
                never_reached = not isinstance(error, ConnectionClosedError) or self._clock() - started < FAST_FAILURE_S
                if not never_reached or attempt == MAX_ATTEMPTS:
                    name = type(error).__name__
                    tries = f" ({attempt} attempts)" if attempt > 1 else ""
                    raise AgentInvokeError("ERROR", f"AgentCore call failed: {name}{tries}", code=name) from None
                log.warning("AgentCore call %d of %d failed before reaching the agent (%s); trying again",
                            attempt, MAX_ATTEMPTS, type(error).__name__)
                if attempt == 1 and self._client_factory is not None:
                    self.client = self._client_factory()  # drop every kept connection
                else:
                    self._sleep(RETRY_PAUSE_S)
        raise AssertionError("unreachable")

    def _call(self, body: bytes, session_id: str) -> str:
        """One `invoke_agent_runtime` call -> response text. Errors that may be repeated (NOT_SENT_ERRORS) pass through."""
        try:
            response = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.runtime_arn, runtimeSessionId=session_id, payload=body,
            )
            return read_response_text(response)
        except NOT_SENT_ERRORS:
            raise
        except ReadTimeoutError:
            raise AgentInvokeError("TIMEOUT", f"AgentCore did not answer within {READ_TIMEOUT_S} s.", code="TIMEOUT") from None
        except ClientError as error:
            detail = error.response.get("Error", {})
            code = detail.get("Code") or "ClientError"
            message = f"AgentCore call failed: {code}: {detail.get('Message', '')}"
            raise AgentInvokeError("ERROR", message[:ERROR_MESSAGE_LIMIT].rstrip(": "), code=code) from None
        except UnicodeDecodeError:
            raise _bad_response("not UTF-8") from None
        except Exception as error:
            name = type(error).__name__
            raise AgentInvokeError("ERROR", f"AgentCore call failed: {name}", code=name) from None


class _FakeSteps:
    """The tool calls and trace entries of one fake run: the internal tools of this same app, called over ASGI."""

    def __init__(self, client: httpx.AsyncClient, token: str, payload: dict[str, Any]) -> None:
        self.client, self.token = client, token
        self.run_id, self.task, self.case_id = payload["run_id"], payload["task"], payload.get("case_id")

    async def call(self, tool: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = await self.client.post(
            f"/internal/tools/{tool}", json={**body, "run_id": self.run_id}, headers={TOKEN_HEADER: self.token}
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data if isinstance(data, dict) else {}

    async def log(self, step: str, tool: str | None, status: str, *, output: str | None = None,
                  decision: str | None = None, duration_ms: int | None = None) -> None:
        code, _ = await self.call("log_trace", {
            "case_id": self.case_id, "task": self.task, "step": step, "tool": tool, "output_summary": output,
            "decision": decision, "duration_ms": duration_ms, "status": status,
        })
        if code != 200:
            raise AgentInvokeError("ERROR", f"log_trace failed: HTTP {code}", code="TOOL_ERROR")

    async def step(self, step: str, tool: str, body: dict[str, Any], *, output=None, decision=None, status_of=None,
                   required: bool = True) -> dict[str, Any] | None:
        """Call a tool and write the trace entry. A tool that is not implemented yet (501) is skipped unless `required`."""
        started = time.perf_counter()
        code, data = await self.call(tool, body)
        ms = int((time.perf_counter() - started) * 1000)
        if code == 501 and not required:
            await self.log(step, tool, "SKIPPED", output="tool not implemented yet", duration_ms=ms)
            return None
        if code != 200:
            reason = (data.get("error") or {}).get("code", code)
            await self.log(step, tool, "ERROR", output=f"HTTP {code} {reason}", duration_ms=ms)
            raise AgentInvokeError("ERROR", f"Tool {tool} failed: HTTP {code} ({reason})", code="TOOL_ERROR")
        await self.log(
            step, tool, status_of(data) if status_of else "OK", output=output(data) if output else None,
            decision=decision(data) if decision else None, duration_ms=ms,
        )
        return data

    async def note(self, step: str, status: str, decision: str) -> None:
        await self.log(step, None, status, output=decision, decision=decision)


def _answer(payload: dict[str, Any], status: str, summary: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "run_id": payload.get("run_id"), "task": payload["task"], "case_id": payload.get("case_id"),
            "summary": summary, "data": data or {}}


def _scored_summary(scored: dict[str, Any], requested: list[str]) -> str:
    parts = [f"Case scored {round1(scored['case_score']):.1f} ({scored['level']})"]
    if requested:
        parts.append(f"supplier confirmation requested from {', '.join(requested)}")
    parts.append("recommendation not generated (fake runner)")
    return "; ".join(parts) + "."


class FakeRunner:
    """AGENT_RUNNER=fake: the graph order of the agent without any LLM or AWS, for local tests.

    It calls the internal tools of this app like the agent does (same token, same endpoints), writes one trace entry per
    node, skips the tools that are still `501 NOT_IMPLEMENTED`, and never marks a recommendation. It records no
    `integration_call`: nothing outside the process was called. A test may set `scan_signals` for `scan_external`.
    """

    integration = None

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.scan_signals: list[dict[str, Any]] = []

    def invoke(self, payload: dict[str, Any], session_id: str) -> dict[str, Any]:
        if payload["task"] == PING_TASK:  # needs no token: it calls no tool
            return _answer(payload, "OK", "Fake runner replied.", {"model": "fake", "answer": "OK"})
        if payload["task"] == "chat":  # EP-18: no model, so a canned reply that claims no number check
            return _answer(payload, "OK", "Fake runner answered.", {
                "answer": "The fake runner has no language model, so this is a canned reply.", "citations": [], "validated": False})
        token = self.app.state.settings.sentra_service_token
        if token is None:
            raise AgentInvokeError(
                "ERROR", "SENTRA_SERVICE_TOKEN is not configured; the fake runner calls the internal tools with it.",
                code="NOT_CONFIGURED", called=False,
            )
        flows = {"analyze_case": self._analyze_case, "resume_case": self._resume_case, "scan_external": self._scan_external,
                 "generate_report": self._generate_report}
        task = payload["task"]
        if task not in flows:
            return _answer(payload, "ERROR", f"Task '{task}' is not supported by the fake runner")
        return asyncio.run(self._run(flows[task], payload, token.get_secret_value()))

    async def _run(self, flow, payload: dict[str, Any], token: str) -> dict[str, Any]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sentra.internal", timeout=60) as client:
            return await flow(_FakeSteps(client, token, payload), payload)

    async def _analyze_case(self, s: _FakeSteps, payload: dict[str, Any]) -> dict[str, Any]:
        cid = {"case_id": s.case_id}
        await s.step("load_case", "get_case", cid, output=lambda r: f"status={r.get('status')}; scope={r.get('scope')}")
        await s.step("schedule_check", "get_po_schedule", {}, output=lambda r: "PO schedule read")
        mapped = await s.step("map_network", "map_event_to_sites", cid,
                              output=lambda r: f"scope={r['scope']}; {len(r['candidates'])} candidate sites")
        await s.step("compute_impact", "compute_impact", {**cid, "persist": True, "trigger": "initial"},
                     output=lambda r: f"version={r.get('version')}")
        scored = await self._score(s, cid)
        await self._meeting_room(s, cid)
        scope = mapped["scope"]
        requested: list[str] = []
        if scope == "LOCATION":
            await s.note("branch_scope", "OK", "scope=LOCATION → assess_confidence")
            assessed = await s.step("assess_confidence", "assess_confidence", cid, required=False,
                                    output=lambda r: f"{len(r.get('sites') or [])} sites assessed")
            flagged = [site["site_id"] for site in (assessed or {}).get("sites") or [] if site.get("needs_verification")]
            if flagged:
                asked = await s.step("request_verification", "request_supplier_confirmation",
                                     {**cid, "site_ids": flagged}, required=False,
                                     output=lambda r: f"requested: {', '.join(x['site_id'] for x in r.get('requested') or [])}")
                requested = [x["site_id"] for x in (asked or {}).get("requested") or []]
        else:
            await s.note("branch_scope", "OK", f"scope={scope} → recommend")
        await s.note("recommend", "SKIPPED", "the fake runner does not write recommendations")
        await s.step("report_refresh", "get_report_data", cid, output=lambda r: f"version={r.get('version')}")
        data = {"case_score": scored["case_score"], "level": scored["level"], "verification_requested": requested,
                "recommended_option": None}
        return _answer(payload, "OK", _scored_summary(scored, requested), data)

    async def _resume_case(self, s: _FakeSteps, payload: dict[str, Any]) -> dict[str, Any]:
        cid = {"case_id": s.case_id}
        await s.step("load_case", "get_case", cid, output=lambda r: f"status={r.get('status')}; scope={r.get('scope')}")
        await s.step("apply_replies", "get_supplier_messages", cid,
                     output=lambda r: f"{len(r.get('applied') or [])} supplier replies applied")
        trigger = payload.get("snapshot_trigger") or "supplier_reply"  # B-44: erp_execution after the ERP execution of the case
        await s.step("compute_impact", "compute_impact", {**cid, "persist": True, "trigger": trigger},
                     output=lambda r: f"version={r.get('version')}")
        scored = await self._score(s, cid)
        await self._meeting_room(s, cid)
        await s.note("recommend", "SKIPPED", "the fake runner does not write recommendations")
        await s.step("report_refresh", "get_report_data", cid, output=lambda r: f"version={r.get('version')}")
        data = {"case_score": scored["case_score"], "level": scored["level"], "recommended_option": None}
        return _answer(payload, "OK", _scored_summary(scored, []), data)

    async def _generate_report(self, s: _FakeSteps, payload: dict[str, Any]) -> dict[str, Any]:
        """Reads the report data (TL-19) and stops there: without a language model there is no narrative to save, and the fake
        runner does not pass a template off as one (TL-20 would store it as `llm`)."""
        report = await s.step("report_data", "get_report_data", {"case_id": s.case_id},
                              output=lambda r: f"version={r.get('version')}")
        await s.note("write_narrative", "SKIPPED", "the fake runner has no language model, so no narrative is written")
        return _answer(payload, "OK", f"Report data of version {report['version']} read; no narrative written (fake runner).",
                       {"version": None, "validator_fallback": False})

    async def _scan_external(self, s: _FakeSteps, payload: dict[str, Any]) -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        if self.scan_signals:
            result = await s.step("save_signals", "save_signals", {"mode": "LIVE", "signals": self.scan_signals},
                                  output=lambda r: f"{len(r.get('saved') or [])} saved")
            saved, dropped = result.get("saved") or [], result.get("dropped") or []
        else:
            await s.note("save_signals", "SKIPPED", "no verified signals to save")
        cases = list(dict.fromkeys(item["case_id"] for item in saved if item.get("case_id")))
        data = {
            "signals_saved": len(saved), "signals_dropped": len(dropped), "unverified": [], "cases": cases,
            "sources_used": [],
            "evidence_stats": {"candidates": 0, "screened": 0, "in_window": 0, "stale": 0, "not_found": 0,
                               "fetch_failed": 0, "skipped_budget": 0},
            "skipped_targets": [], "partition": {"index": 0, "count": 1, "locations": [], "countries": []},
        }
        return _answer(payload, "OK", f"{len(saved)} signals saved (fake runner).", data)

    @staticmethod
    async def _score(s: _FakeSteps, cid: dict[str, Any]) -> dict[str, Any]:
        return await s.step(
            "score", "score_case", cid,
            decision=lambda r: f"case score {round1(r['case_score']):.1f} → {r['level']}",
            output=lambda r: f"{r['case_score']} ({r['level']})",
        )

    @staticmethod
    async def _meeting_room(s: _FakeSteps, cid: dict[str, Any]) -> None:
        await s.step("meeting_room_check", "notify_meeting_room", cid, output=lambda r: str(r.get("status")),
                     status_of=lambda r: "SKIPPED" if r.get("status") == "SKIPPED_FEATURE_OFF" else "OK")


def make_runner(app: FastAPI) -> AgentRunner | None:
    """The runner chosen by AGENT_RUNNER; None (`off`) starts no worker."""
    settings = app.state.settings
    if settings.agent_runner == "fake":
        return FakeRunner(app)
    if settings.agent_runner == "agentcore":
        return AgentCoreRunner(settings.agent_runtime_arn)
    return None


# --- payload (D.2) and session (D.1) ----------------------------------------------------------------------------------

def callback_base_url(settings: Any) -> str:
    """Base URL the agent calls the internal tools on. APP_BASE_URL when it is a real address; otherwise, on Cloud
    Foundry, the first route of this app (`VCAP_APPLICATION`), so a missing APP_BASE_URL does not break the callbacks."""
    configured = settings.app_base_url.rstrip("/")
    if urlparse(configured).hostname not in LOCAL_HOSTS:
        return configured
    try:
        uris = json.loads(os.environ.get("VCAP_APPLICATION") or "{}").get("application_uris") or []
    except (ValueError, AttributeError):
        uris = []
    return f"https://{uris[0]}" if uris and isinstance(uris[0], str) and uris[0] else configured


def build_payload(run: AgentRun, case: Case | None, *, app_mode: str, callback_base_url: str) -> dict[str, Any]:
    """Payload D.2 of a run. `mode` is only forwarded: a scan is always LIVE, a case task carries the origin of the case
    (not the application mode, DEC-024), other tasks the application mode."""
    if run.task == SCAN_TASK:
        mode = "LIVE"
    elif case is not None:
        mode = case.mode
    else:
        mode = app_mode
    payload = {"task": run.task, "run_id": run.run_id, "trigger": run.trigger, "case_id": run.case_id, "mode": mode,
               "callback_base_url": callback_base_url, "message": None, "history": None}
    if run.task == "resume_case" and case is not None and case.status == "EXECUTED":
        # B-44: the ERP execution of the case finished. Optional field of `resume_case` (D.2 proposal); the agent derives the
        # same value from the case status when the field is absent.
        payload["snapshot_trigger"] = "erp_execution"
    return payload


def session_id_for(run: AgentRun, case: Case | None, pool: "WarmSessionPool | None" = None) -> str:
    """`runtimeSessionId` (UUID, 33 characters or more): the case session for case tasks; for the others (scan, daily check)
    a warm session from the pool (contract D.1 v1.3), or a fresh UUID when there is no pool."""
    if run.task in CASE_SESSION_TASKS and case is not None:
        return case.agent_session_id
    return pool.take(run.task) if pool is not None else str(uuid4())


# --- warm session pool and keep-alive (B-13b, DEC-016) -----------------------------------------------------------------

class WarmSessionPool:
    """Session ids that AgentCore has already started a microVM for (contract D.1 v1.3).

    A new `runtimeSessionId` is a new microVM, so its first call is a cold start. The pool holds up to
    AGENT_WARM_POOL_SIZE ids that answered a `ping`; a new case, a scan or a daily check takes one (`take`) and the pool is
    filled again in the background. An empty pool is not an error: the caller gets a fresh UUID (logged, counted in
    `fallbacks`). With AGENT_KEEPALIVE_ENABLED the pool is filled at start and every AGENT_KEEPALIVE_MIN minutes the ids in
    the pool and of the open cases are pinged so their microVMs do not go idle (rehearsal and demo only).

    The pool works only when a runner exists and either keep-alive is on or the runner is the fake one: otherwise a take
    returns a plain UUID and nothing is pinged, so the default setup makes no extra model calls. A ping is logged in
    `integration_call` and creates no `agent_run`. Every method is safe to call from several threads; only `ping` and
    `keepalive_tick` wait for the agent, and the background threads are the ones that call them.
    """

    def __init__(self, app: FastAPI) -> None:
        settings = app.state.settings
        self.app = app
        self.size: int = settings.agent_warm_pool_size
        self.keepalive_enabled: bool = settings.agent_keepalive_enabled
        self.interval_s: float = settings.agent_keepalive_min * 60
        self.fallbacks = 0
        self._lock = threading.Lock()
        self._ready: deque[str] = deque()
        self._warming = 0  # pings of new ids in flight; they count towards the size
        self._pinging: set[str] = set()
        self._threads: set[threading.Thread] = set()
        self._stop = threading.Event()
        self._keepalive: threading.Thread | None = None

    @property
    def active(self) -> bool:
        runner = self.app.state.run_queue.runner
        fake = self.app.state.settings.agent_runner == "fake"
        return self.size > 0 and runner is not None and (self.keepalive_enabled or fake)

    @property
    def keepalive_running(self) -> bool:
        return self._keepalive is not None and self._keepalive.is_alive()

    def ready_ids(self) -> list[str]:
        with self._lock:
            return list(self._ready)

    def start(self) -> None:
        """Warm the pool and start the keep-alive loop, only with AGENT_KEEPALIVE_ENABLED. Does not wait for the pings."""
        if not (self.keepalive_enabled and self.active) or self.keepalive_running:
            return
        self._stop.clear()
        self.refill()
        self._keepalive = threading.Thread(target=self._keepalive_loop, name="agent-keepalive", daemon=True)
        self._keepalive.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + timeout
        if self._keepalive is not None:
            self._keepalive.join(max(0.0, deadline - time.monotonic()))
            self._keepalive = None
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    # taking and filling

    def take(self, purpose: str) -> str:
        """A session id for a new case, a scan or a daily check; the pool is refilled afterwards. Never blocks."""
        if not self.active:
            return str(uuid4())
        with self._lock:
            session_id = self._ready.popleft() if self._ready else None
            if session_id is None:
                self.fallbacks += 1
        if session_id is None:
            log.warning("warm pool is empty (%s): using a new session id, so this call starts cold", purpose)
            session_id = str(uuid4())
        self.refill()
        return session_id

    def refill(self) -> None:
        """Start a ping of a new id for every place missing in the pool (ids still warming count as present)."""
        if not self.active or self._stop.is_set():
            return
        with self._lock:
            missing = max(0, self.size - len(self._ready) - self._warming)
            self._warming += missing
        for _ in range(missing):
            self._spawn(self._warm_one)

    def _warm_one(self) -> None:
        session_id = str(uuid4())
        warm = False
        try:
            warm = self.ping(session_id)
        finally:
            with self._lock:
                self._warming -= 1
                if warm and len(self._ready) < self.size:
                    self._ready.append(session_id)

    def _spawn(self, target: Callable[..., None], *args: Any) -> threading.Thread:
        def run() -> None:
            try:
                target(*args)
            except Exception:
                log.exception("warm session pool task failed")
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, name="agent-warm-pool", daemon=True)
        with self._lock:
            self._threads.add(thread)
        thread.start()
        return thread

    # ping

    def ping(self, session_id: str, *, report_unreached: bool = False) -> bool:
        """Invoke the `ping` task on a session and log it in `integration_call` (`agentcore`, and the model when the agent
        answered). True when the agent answered OK; False when it did not, or when this session is already being pinged (never
        two pings for one id at the same time).

        A ping that could not be sent (the runtime is not configured) is not logged, unless `report_unreached`: the button
        `Run checks` wants to show that failure, a keep-alive round does not."""
        runner = self.app.state.run_queue.runner
        if runner is None:
            return False
        with self._lock:
            if session_id in self._pinging:
                return False
            self._pinging.add(session_id)
        started = time.perf_counter()
        result: dict[str, Any] | None = None
        failure: AgentInvokeError | None = None
        try:
            try:
                result = runner.invoke(self._payload(), session_id)
            except AgentInvokeError as error:
                failure = error
            except Exception as error:
                failure = AgentInvokeError("ERROR", f"Unexpected error: {type(error).__name__}", code=type(error).__name__)
            status = result.get("status") if result is not None else None
            self._record(runner, failure, status, int((time.perf_counter() - started) * 1000), report_unreached)
            return failure is None and status == "OK"
        finally:
            with self._lock:
                self._pinging.discard(session_id)

    def _payload(self) -> dict[str, Any]:
        return {"task": PING_TASK, "run_id": None, "trigger": "auto", "case_id": None, "mode": self.app.state.app_mode,
                "callback_base_url": callback_base_url(self.app.state.settings), "message": None, "history": None}

    def _record(self, runner: AgentRunner, failure: AgentInvokeError | None, status: Any, duration_ms: int,
                report_unreached: bool = False) -> None:
        if runner.integration is None or (failure is not None and not failure.called and not report_unreached):
            return
        answered = failure is None and status == "OK"
        reason = None if answered else (failure.code if failure is not None else str(status))
        try:
            with self.app.state.database.session_factory() as session, session.begin():
                now = utc_now()
                record_integration_call(session, runner.integration, "OK" if answered else "FAILED", duration_ms, reason, now)
                if failure is None:  # the agent answered, and a ping is one model call: its status is the model's status
                    record_model_call(session, answered=answered, duration_ms=duration_ms, now=now)
        except Exception:
            log.exception("could not record a ping")

    # keep-alive

    def keepalive_tick(self) -> None:
        """One round: top up the pool, then ping (in parallel) every id in the pool and of every open case. A case with a run
        in flight is left out: that run keeps its session warm. Returns when the pings are done."""
        if not self.active:
            return
        self.refill()
        busy = self.app.state.run_queue.active_lanes()
        with self._lock:
            ids = list(self._ready)
        with self.app.state.database.session_factory() as session:
            open_cases = session.execute(
                select(Case.case_id, Case.agent_session_id).where(Case.status.not_in(CLOSED_STATUSES))
            ).all()
        ids += [session_id for case_id, session_id in open_cases if case_id not in busy]
        threads = [self._spawn(self.ping, session_id) for session_id in dict.fromkeys(ids)]
        for thread in threads:
            while thread.is_alive() and not self._stop.is_set():
                thread.join(0.2)

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.keepalive_tick()
            except Exception:
                log.exception("keep-alive round failed")


# --- case status after a run ------------------------------------------------------------------------------------------

def _listed(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, str) for item in value)


def settle_case(session: Session, run: AgentRun, data: dict[str, Any], now: datetime) -> None:
    """PRD 8.3 after `analyze_case` / `resume_case` finished OK: the case waits for suppliers while a request of any kind is
    still outstanding (a `supplier_request` REQUESTED in the database; for `analyze_case` also the sites the agent says it asked),
    otherwise it is ready for a decision. A move the state machine does not allow (the case already moved on) is ignored."""
    case = session.get(Case, run.case_id)
    if case is None or case.status in CLOSED_STATUSES:
        return
    pending = session.scalar(
        select(SupplierRequest.request_id).where(SupplierRequest.case_id == run.case_id, SupplierRequest.status == "REQUESTED").limit(1)
    ) is not None
    if run.task == "analyze_case":
        pending = pending or _listed(data.get("verification_requested"))
    event = CaseEvent.CONFIRMATION_REQUESTED if pending else CaseEvent.RECOMMENDATION_READY
    try:
        apply_event(session, run.case_id, event, now=now)
    except ApiError:
        pass


RETRY_STATUSES = ("ERROR", "AGENT_TIMEOUT")


def retry_failed_analysis(session: Session, run: AgentRun, status: str, now: datetime) -> AgentRun | None:
    """DEC-044: an `analyze_case` that ended ERROR / AGENT_TIMEOUT is queued again ONCE, so a passing fault (a slow RPT-1.6
    call, a cold AgentCore session) does not leave the case in ANALYZING. When the analysis just before it in this case also
    failed, this run was already the retry: nothing more is queued and the failure stays visible. Other tasks are not retried."""
    if run.task != "analyze_case" or status not in RETRY_STATUSES or run.case_id is None:
        return None
    case = session.get(Case, run.case_id)
    if case is None or case.status in CLOSED_STATUSES:
        return None
    previous = session.scalar(
        select(AgentRun.status)
        .where(AgentRun.case_id == run.case_id, AgentRun.task == "analyze_case", AgentRun.run_id < run.run_id)
        .order_by(AgentRun.run_id.desc())
        .limit(1)
    )
    if previous in RETRY_STATUSES:
        return None
    retry = enqueue_run(session, task="analyze_case", trigger=run.trigger, case_id=run.case_id, now=now)
    add_trace(session, run_id=retry.run_id, case_id=run.case_id, task="analyze_case", step="agent_run_retry", tool=None,
              input_summary=f"after {run.run_id} {status}", output_summary=f"{retry.run_id} queued",
              decision="the analysis is queued again once (DEC-044)", duration_ms=None, status="OK", now=now)
    return retry


def queue_follow_ups(session: Session, run: AgentRun, data: dict[str, Any], now: datetime) -> None:
    """After a scan: `analyze_case` for every open case in `data.cases` (new or changed, TL-05)."""
    cases = data.get("cases")
    if not isinstance(cases, list):
        return
    for case_id in dict.fromkeys(item for item in cases if isinstance(item, str)):
        case = session.get(Case, case_id)
        if case is not None and case.status not in CLOSED_STATUSES:
            enqueue_run(session, task="analyze_case", trigger=run.trigger, case_id=case_id, now=now)


def queue_report_refresh(session: Session, run: AgentRun, now: datetime) -> AgentRun | None:
    """After a `resume_case` (the data of the case changed): queue `generate_report` so the report follows the newest snapshot
    (FR-15). A report run already waiting or running for the case is reused. None when the case no longer exists."""
    if run.case_id is None or session.get(Case, run.case_id) is None:
        return None
    return enqueue_run(session, task=GENERATE_REPORT_TASK, trigger=run.trigger, case_id=run.case_id, now=now)


# --- the queue and its worker -----------------------------------------------------------------------------------------

def _lane(run: AgentRun) -> str:
    """Runs of one lane never overlap: a case, or one task without a case (scan, daily check)."""
    return run.case_id if run.case_id is not None else f"task:{run.task}"


class RunQueue:
    """A dispatcher thread claims QUEUED runs and starts one daemon thread per run (the call blocks up to 150 s)."""

    def __init__(self, app: FastAPI, runner: AgentRunner | None, *, max_parallel: int = MAX_PARALLEL_RUNS,
                 poll_s: float = POLL_S) -> None:
        self.app = app
        self.runner = runner
        self.max_parallel = max_parallel
        self.poll_s = poll_s
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._jobs: set[threading.Thread] = set()
        self._dispatcher: threading.Thread | None = None

    def start(self) -> None:
        if self.runner is None or self._dispatcher is not None:
            return
        self._stop.clear()
        self._dispatcher = threading.Thread(target=self._loop, name="agent-run-dispatcher", daemon=True)
        self._dispatcher.start()
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout)
            self._dispatcher = None
        deadline = time.monotonic() + timeout
        with self._lock:
            jobs = list(self._jobs)
        for job in jobs:
            job.join(max(0.0, deadline - time.monotonic()))

    def wake(self) -> None:
        """Tell the dispatcher that a run was queued (call after the transaction that queued it committed)."""
        self._wake.set()

    def active_lanes(self) -> set[str]:
        """Lanes (case ids, or `task:<name>`) with a run in flight."""
        with self._lock:
            return set(self._active)

    # dispatcher

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                for run_id, lane in self._claim():
                    job = threading.Thread(target=self._job, args=(run_id, lane), name=f"agent-run-{run_id}", daemon=True)
                    with self._lock:
                        self._jobs.add(job)
                    job.start()
            except Exception:
                log.exception("agent run dispatcher failed")
            self._wake.wait(self.poll_s)

    def _claim(self) -> list[tuple[str, str]]:
        """QUEUED -> RUNNING for the runs whose lane is free, oldest first; an analysis moves its case NEW -> ANALYZING."""
        now = utc_now()
        claimed: list[tuple[str, str]] = []
        with self.app.state.database.session_factory() as session, session.begin():
            with self._lock:
                busy = set(self._active)
            queued = session.scalars(select(AgentRun).where(AgentRun.status == "QUEUED").order_by(AgentRun.run_id)).all()
            for run in queued:
                lane = _lane(run)
                if len(busy) >= self.max_parallel:
                    break
                if lane in busy:
                    continue
                busy.add(lane)
                run.status, run.started_at = "RUNNING", now
                if run.task == "analyze_case" and run.case_id is not None:
                    case = session.get(Case, run.case_id)
                    if case is not None and case.status == "NEW":
                        apply_event(session, run.case_id, CaseEvent.ANALYSIS_STARTED, now=now)
                claimed.append((run.run_id, lane))
        with self._lock:
            self._active.update(lane for _, lane in claimed)
        return claimed

    # one run

    def _job(self, run_id: str, lane: str) -> None:
        try:
            self._execute(run_id)
        except Exception:
            log.exception("agent run %s crashed", run_id)
        finally:
            with self._lock:
                self._active.discard(lane)
                self._jobs.discard(threading.current_thread())
            self.wake()

    def _execute(self, run_id: str) -> None:
        settings = self.app.state.settings
        with self.app.state.database.session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None:  # removed by Reset demo data
                return
            case = session.get(Case, run.case_id) if run.case_id is not None else None
            payload = build_payload(run, case, app_mode=self.app.state.app_mode,
                                    callback_base_url=callback_base_url(settings))
            session_id = session_id_for(run, case, getattr(self.app.state, "session_pool", None))
        started = time.perf_counter()
        result: dict[str, Any] | None = None
        failure: AgentInvokeError | None = None
        try:
            result = self.runner.invoke(payload, session_id)
        except AgentInvokeError as error:
            failure = error
        except Exception as error:
            log.exception("agent run %s failed", run_id)
            failure = AgentInvokeError("ERROR", f"Unexpected error: {type(error).__name__}", code=type(error).__name__)
        self._finish(run_id, result, failure, int((time.perf_counter() - started) * 1000))

    def _finish(self, run_id: str, result: dict[str, Any] | None, failure: AgentInvokeError | None, duration_ms: int) -> None:
        now = utc_now()
        data = result.get("data") if result is not None else None
        data = data if isinstance(data, dict) else {}
        with self.app.state.database.session_factory() as session, session.begin():
            if failure is not None:
                status = "AGENT_TIMEOUT" if failure.kind == "TIMEOUT" else "ERROR"
                summary, error = None, str(failure)
            else:
                status = result["status"]
                summary = result.get("summary") if isinstance(result.get("summary"), str) else None
                error = None if status == "OK" else (summary or f"The agent answered {status}.")
            if self.runner.integration is not None and (failure is None or failure.called):
                answered = failure is None and status == "OK"
                record_integration_call(
                    session, self.runner.integration, "OK" if answered else "FAILED", duration_ms,
                    None if answered else (failure.code if failure is not None else status), now,
                )
            run = session.get(AgentRun, run_id)
            if run is None:  # Reset demo data removed the run (and its case) while the agent worked
                return
            run.status, run.finished_at, run.summary, run.error, run.result = status, now, summary, error, result
            if failure is not None and run.case_id is not None and session.get(Case, run.case_id) is not None:
                add_trace(session, run_id=run_id, case_id=run.case_id, task=run.task, step="agent_run", tool=None,
                          input_summary=None, output_summary=error, decision=None, duration_ms=duration_ms,
                          status=status, now=now)
            if status != "OK":
                retry_failed_analysis(session, run, status, now)
                return
            if run.task in ("analyze_case", "resume_case") and run.case_id is not None:
                settle_case(session, run, data, now)
                if run.task == "resume_case":
                    queue_report_refresh(session, run, now)
            elif run.task == SCAN_TASK:
                queue_follow_ups(session, run, data, now)
