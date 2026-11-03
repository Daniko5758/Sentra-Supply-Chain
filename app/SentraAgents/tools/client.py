"""Klien tool internal ``sentra-api`` (TL-01..TL-24 dan TL-28, kontrak bagian C v1.11).

SATU-SATUNYA tempat agen memanggil HTTP ke ``sentra-api`` (``CLAUDE.md`` bagian 3): graph dan ``@tool`` tidak
memanggil ``requests`` / ``httpx`` langsung. Setiap fungsi = satu tool:

    POST {callback_base_url}/internal/tools/{nama_tool}
    Header X-Sentra-Service-Token: <token>     body & respons JSON, timeout 30 detik

Error kontrak A.3 (``{"error": {"code", "message", "details"}}``) menjadi ``ToolError``; ``404 FEATURE_DISABLED``
menjadi ``FeatureDisabledError`` (subkelas ``ToolError``). Kegagalan jaringan menjadi ``ToolError`` dengan kode
``TOOL_TIMEOUT`` / ``TOOL_UNREACHABLE`` / ``INVALID_RESPONSE``. Tidak ada retry otomatis: node graph yang
memutuskan (G-05), dan tool tulis tidak boleh diulang diam-diam.

Tidak dimasukkan di sini: TL-04 ``search_web_sonar``, TL-25 ``search_brave_news``, dan TL-27 ``verify_sources``. Ketiganya bukan endpoint
``sentra-api``; agen memanggilnya langsung (``tools/sonar_policy.py``, ``tools/news.py``, ``tools/verify.py``). TL-03 (GDELT) dan TL-26
(Currents) tidak dipakai agen sejak N-17 (DEC-032).

Token layanan: env ``SENTRA_SERVICE_TOKEN`` (``agentcore dev``) atau Secrets Manager
``SENTRA_SERVICE_TOKEN_SECRET_ID`` (default ``sentra/service-token``, key ``token``) di runtime. Nilainya
tidak pernah masuk pesan error, ``repr``, atau log.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Literal, Mapping, Sequence
from urllib.parse import urlparse

import requests

Json = dict[str, Any]
Mode = Literal["LIVE", "DEMO"]
SnapshotTrigger = Literal["initial", "supplier_reply", "erp_execution"]  # A.4 snapshot_trigger
RequestType = Literal["IMPACT", "BACKUP_ACTIVATION", "PULL_IN"]  # A.4 request_type (v1.11, DEC-038)
TraceStatus = Literal["RUNNING", "OK", "ERROR", "SKIPPED", "AGENT_TIMEOUT", "LLM_OVERRIDDEN"]  # A.4 trace_status

DEFAULT_TIMEOUT_S = 30.0
# DEC-045: tool yang di sentra-api memanggil layanan lambat mendapat batas tunggu sendiri (minimum; batas klien yang lebih besar tetap
# dipakai). TL-10 memanggil RPT-1.6 dua kali berurutan (K_A lalu K_B, masing-masing sampai 30 s di backend), jadi 30 s memotongnya.
TOOL_TIMEOUTS_S: dict[str, float] = {"assess_confidence": 60.0}
TOKEN_HEADER = "X-Sentra-Service-Token"
SERVICE_TOKEN_ENV = "SENTRA_SERVICE_TOKEN"
SERVICE_TOKEN_SECRET_ENV = "SENTRA_SERVICE_TOKEN_SECRET_ID"
DEFAULT_TOKEN_SECRET_ID = "sentra/service-token"
AWS_REGION = "ap-southeast-2"  # SCP akun menolak region lain (sama dengan model/load.py)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Katalog kontrak C yang dipanggil ke sentra-api (TL-03 dan TL-04 sengaja tidak ada).
READ_TOOLS = frozenset({
    "get_po_schedule", "get_production_schedule", "get_supplier_network", "map_event_to_sites",
    "get_supplier_messages", "list_alternate_sources", "get_case", "get_report_data", "get_country_heatmap",
    "get_recommendation_options", "run_what_if", "simulate_plan", "compute_impact", "validate_numbers",
    "get_escalation",
})  # termasuk tool "hitung": compute_impact dengan persist=false, simulate_plan, validate_numbers tidak menulis
WRITE_TOOLS = frozenset({
    "save_signals", "score_case", "assess_confidence", "request_supplier_confirmation", "notify_meeting_room",
    "save_recommendation", "save_report", "log_trace",
})
TOOL_NAMES = READ_TOOLS | WRITE_TOOLS


class ToolError(Exception):
    """Error dari tool internal (kontrak A.3) atau dari transport."""

    def __init__(self, code: str, message: str, *, status: int | None = None, details: Json | None = None,
                 tool: str | None = None) -> None:
        super().__init__(f"{tool + ': ' if tool else ''}{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.tool = tool

    def as_dict(self) -> Json:
        error: Json = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return error


class FeatureDisabledError(ToolError):
    """``404 FEATURE_DISABLED``: fitur opsional (mis. F-10 what-if) sedang mati."""


class ToolConfigError(ToolError):
    """Konfigurasi klien tidak lengkap atau tidak aman (URL, token)."""

    def __init__(self, message: str) -> None:
        super().__init__("TOOL_CONFIG", message)


# --------------------------------------------------------------------------- token layanan

_token_lock = threading.Lock()
_token_cache: str | None = None


def load_service_token(
    env: Mapping[str, str] | None = None,
    secrets_client_factory: Callable[[], Any] | None = None,
) -> str:
    """Token layanan: env dulu (agentcore dev), lalu Secrets Manager (runtime). Tanpa cache."""
    env = os.environ if env is None else env
    token = (env.get(SERVICE_TOKEN_ENV) or "").strip()
    if token:
        return token

    secret_id = (env.get(SERVICE_TOKEN_SECRET_ENV) or "").strip() or DEFAULT_TOKEN_SECRET_ID
    if secrets_client_factory is None:
        import boto3  # impor lambat: tidak dibutuhkan saat env sudah ada

        def secrets_client_factory() -> Any:
            return boto3.client("secretsmanager", region_name=AWS_REGION)

    try:
        raw = secrets_client_factory().get_secret_value(SecretId=secret_id)["SecretString"]
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw  # secret berupa teks polos
        token = str(parsed["token"] if isinstance(parsed, dict) else parsed).strip()
    except Exception as exc:  # jangan bocorkan isi secret lewat pesan error
        raise ToolConfigError(
            f"Cannot read the service token from env {SERVICE_TOKEN_ENV} or Secrets Manager secret "
            f"'{secret_id}' ({type(exc).__name__})"
        ) from None
    if not token:
        raise ToolConfigError(f"Secrets Manager secret '{secret_id}' has an empty service token")
    return token


def get_service_token() -> str:
    """``load_service_token`` dengan cache per proses (Secrets Manager dipanggil sekali)."""
    global _token_cache
    with _token_lock:
        if _token_cache is None:
            _token_cache = load_service_token()
        return _token_cache


def reset_token_cache() -> None:
    global _token_cache
    with _token_lock:
        _token_cache = None


# --------------------------------------------------------------------------- sesi HTTP per proses (A-15)

SESSION_POOL_SIZE = 16  # koneksi keep-alive ke sentra-api per proses; cukup untuk node paralel (rekomendasi, laporan) dan beberapa run
_session_lock = threading.Lock()
_shared_session: requests.Session | None = None


def shared_session() -> requests.Session:
    """Satu ``requests.Session`` per proses untuk semua ``ToolClient``: koneksi keep-alive (TCP/TLS ke CF) dipakai ulang antar tool call
    dan antar run, tidak dibuat ulang per run. Aman dipakai dari beberapa thread (kolam koneksi ``SESSION_POOL_SIZE``)."""
    global _shared_session
    with _session_lock:
        if _shared_session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=SESSION_POOL_SIZE)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _shared_session = session
        return _shared_session


def reset_shared_session() -> None:
    """Hanya untuk tes."""
    global _shared_session
    with _session_lock:
        if _shared_session is not None:
            _shared_session.close()
        _shared_session = None


# --------------------------------------------------------------------------- klien


def _check_base_url(base_url: str) -> str:
    parsed = urlparse(base_url or "")
    if not parsed.hostname or parsed.scheme not in ("http", "https"):
        raise ToolConfigError("callback_base_url must be an http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ToolConfigError("callback_base_url must use https (plain http only for localhost)")
    return base_url.rstrip("/")


class ToolClient:
    """Satu klien per run: membawa ``callback_base_url``, ``run_id``, dan ``mode`` dari payload D.2.

    ``run_id`` ikut di body setiap tool (field opsional kontrak C) supaya ``sentra-api`` bisa mengaitkan
    pemanggilan dengan jejak. ``calls`` menghitung percobaan request (untuk batas G-05: maks 25 per run); thread-safe.
    Sesi HTTP bawaan = ``shared_session()`` (satu per proses).
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        run_id: str | None = None,
        mode: Mode | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = _check_base_url(base_url)
        self.run_id = run_id
        self.mode = mode
        self.timeout = timeout
        self.calls = 0  # dinaikkan di bawah ``_calls_lock``: node paralel (A-15) memanggil tool dari beberapa thread
        self._calls_lock = threading.Lock()
        self._token = token
        self._session = session or shared_session()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], **kwargs: Any) -> "ToolClient":
        """Dari payload kontrak D.2 (``callback_base_url``, ``run_id``, ``mode``)."""
        return cls(
            str(payload.get("callback_base_url") or ""),
            run_id=payload.get("run_id"),
            mode=payload.get("mode"),
            **kwargs,
        )

    def __repr__(self) -> str:  # token tidak boleh tercetak
        return f"ToolClient(base_url={self.base_url!r}, run_id={self.run_id!r}, calls={self.calls})"

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = get_service_token()
        return self._token

    # ---- transport

    def _call(self, name: str, fields: Mapping[str, Any] | None = None, *, keep_null: Sequence[str] = ()) -> Json:
        """POST satu tool. ``None`` dibuang kecuali field di ``keep_null`` (yang contoh kontraknya ``null``)."""
        if name not in TOOL_NAMES:  # G-01: hanya katalog kontrak bagian C
            raise ValueError(f"Unknown tool '{name}'")
        body: Json = {k: v for k, v in (fields or {}).items() if v is not None or k in keep_null}
        if self.run_id is not None:
            body.setdefault("run_id", self.run_id)

        with self._calls_lock:
            self.calls += 1
        timeout = max(self.timeout, TOOL_TIMEOUTS_S.get(name, 0.0))
        try:
            resp = self._session.post(
                f"{self.base_url}/internal/tools/{name}",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={TOKEN_HEADER: self.token, "Content-Type": "application/json"},
                timeout=timeout,
            )
        except requests.Timeout:
            raise ToolError("TOOL_TIMEOUT", f"no response within {timeout:g}s", tool=name) from None
        except requests.RequestException as exc:
            raise ToolError("TOOL_UNREACHABLE", f"{type(exc).__name__} while calling sentra-api", tool=name) from None

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if resp.status_code >= 400:
            raise self._error_from(name, resp.status_code, payload, resp.text)
        if not isinstance(payload, dict):
            raise ToolError("INVALID_RESPONSE", "response is not a JSON object", status=resp.status_code, tool=name)
        return payload

    @staticmethod
    def _error_from(name: str, status: int, payload: Any, text: str) -> ToolError:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("code"):
            code, message, details = str(error["code"]), str(error.get("message") or ""), error.get("details")
        else:  # badan bukan bentuk A.3 (mis. proxy / gateway)
            code, message, details = f"HTTP_{status}", (text or "").strip()[:200], None
        cls = FeatureDisabledError if code == "FEATURE_DISABLED" else ToolError
        return cls(code, message, status=status, details=details if isinstance(details, dict) else None, tool=name)

    # ---- jadwal

    def get_po_schedule(self) -> Json:
        """TL-01: ``totals`` dan ``po_lines`` (R-19)."""
        return self._call("get_po_schedule")

    def get_production_schedule(self) -> Json:
        """TL-02: ``production_orders``, ``bom``, ``stock``, ``sales_orders``."""
        return self._call("get_production_schedule")

    # ---- sinyal dan jaringan

    def save_signals(self, signals: Sequence[Mapping[str, Any]], mode: Mode | None = None) -> Json:
        """TL-05: ``{"saved": [...], "dropped": [...]}``. ``mode`` dari argumen atau klien."""
        mode = mode or self.mode
        if mode not in ("LIVE", "DEMO"):
            raise ValueError("save_signals needs mode LIVE or DEMO (argument or ToolClient.mode)")
        return self._call("save_signals", {"mode": mode, "signals": list(signals)})

    def get_supplier_network(self, case_id: str | None = None) -> Json:
        """TL-06: graf supplier (EP-05); ``case_id`` opsional (tanpa itu semua ``highlight = none``)."""
        return self._call("get_supplier_network", {"case_id": case_id})

    def map_event_to_sites(self, case_id: str) -> Json:
        """TL-07: site kandidat (R-01)."""
        return self._call("map_event_to_sites", {"case_id": case_id})

    # ---- dampak, skor, keyakinan

    def compute_impact(self, case_id: str, *, persist: bool = False, trigger: SnapshotTrigger | None = None) -> Json:
        """TL-08: objek Impact (G.1); ``persist=True`` menyimpan snapshot baru dan butuh ``trigger``."""
        if persist and trigger not in ("initial", "supplier_reply", "erp_execution"):
            raise ValueError("compute_impact(persist=True) needs trigger initial | supplier_reply | erp_execution")
        fields: Json = {"case_id": case_id, "persist": persist}
        if persist:
            fields["trigger"] = trigger
        return self._call("compute_impact", fields)

    def score_case(self, case_id: str) -> Json:
        """TL-09: skor kasus, level, alert, ``priority`` (R-07, R-08)."""
        return self._call("score_case", {"case_id": case_id})

    def assess_confidence(self, case_id: str) -> Json:
        """TL-10: K_A / K_B per site (R-10, R-11). ``scope = COUNTRY`` → ``{"sites": [], "skipped": true}``."""
        return self._call("assess_confidence", {"case_id": case_id})

    # ---- verifikasi supplier

    def request_supplier_confirmation(self, case_id: str, site_ids: Sequence[str],
                                      request_type: RequestType = "IMPACT") -> Json:
        """TL-11: kirim permintaan konfirmasi (SIMULATED); balasan lewat ``resume_case`` (R-12 v2, R-13 v2).
        ``request_type`` (v1.11) selalu dikirim: ``IMPACT`` (keyakinan supplier), ``BACKUP_ACTIVATION`` (Opsi C), ``PULL_IN`` (Opsi E)."""
        return self._call("request_supplier_confirmation",
                          {"case_id": case_id, "site_ids": list(site_ids), "request_type": request_type})

    def get_supplier_messages(self, case_id: str, site_id: str | None = None) -> Json:
        """TL-12: ``messages`` dan ``applied`` (R-13)."""
        return self._call("get_supplier_messages", {"case_id": case_id, "site_id": site_id}, keep_null=("site_id",))

    def notify_meeting_room(self, case_id: str, agenda: str | None = None) -> Json:
        """TL-13 v2 [F-07]: server menghitung ulang tingkat, memvalidasi ``agenda`` (G-04, gagal → template), membuat rapat Zoom
        dan satu email. Agen hanya mengirim ``case_id`` dan ``agenda`` (boleh ``null``); tidak 404 saat flag mati
        (``status = SKIPPED_FEATURE_OFF``)."""
        return self._call("notify_meeting_room", {"case_id": case_id, "agenda": agenda}, keep_null=("agenda",))

    def get_escalation(self, case_id: str) -> Json:
        """TL-28 (v1.11): eskalasi R-22 (G.7): ``tier``, ``reasons``, ``participants``, ``needs_meeting``, ``meeting``. Tidak menulis."""
        return self._call("get_escalation", {"case_id": case_id})

    # ---- rekomendasi

    def list_alternate_sources(self, part_ids: Sequence[str]) -> Json:
        """TL-14: ``sources`` dan ``missing``."""
        return self._call("list_alternate_sources", {"part_ids": list(part_ids)})

    def simulate_plan(self, case_id: str, actions: Sequence[Mapping[str, Any]]) -> Json:
        """TL-15: ``before``, ``after``, ``impact_after`` untuk daftar aksi (G.3, tanpa ``action_id``). Tidak menulis."""
        return self._call("simulate_plan", {"case_id": case_id, "actions": list(actions)})

    def get_recommendation_options(self, case_id: str) -> Json:
        """TL-23: opsi, aksi, sebelum/sesudah, ``recommended_option`` (tanpa ``rationale``); semua dari kode."""
        return self._call("get_recommendation_options", {"case_id": case_id})

    def save_recommendation(self, case_id: str, version: int, rationales: Mapping[str, str]) -> Json:
        """TL-16: hanya teks alasan per opsi; ``validator`` G-04 berjalan di server."""
        return self._call("save_recommendation", {"case_id": case_id, "version": version,
                                                  "rationales": dict(rationales)})

    # ---- kasus, laporan, chat

    def get_case(self, case_id: str, version: int | None = None) -> Json:
        """TL-17: detail kasus (EP-12); ``version`` kosong = terbaru."""
        return self._call("get_case", {"case_id": case_id, "version": version}, keep_null=("version",))

    def run_what_if(self, location: str) -> Json:
        """TL-18 [F-10]: EP-25. Flag mati → ``FeatureDisabledError``."""
        return self._call("run_what_if", {"location": location})

    def get_report_data(self, case_id: str) -> Json:
        """TL-19: data laporan dan grafik (FR-15)."""
        return self._call("get_report_data", {"case_id": case_id})

    def save_report(self, case_id: str, version: int, narrative_md: str) -> Json:
        """TL-20: narasi laporan; respons validator seperti TL-16."""
        return self._call("save_report", {"case_id": case_id, "version": version, "narrative_md": narrative_md})

    def get_country_heatmap(self) -> Json:
        """TL-22: heatmap negara (EP-16, R-21)."""
        return self._call("get_country_heatmap")

    # ---- jejak dan validator

    def log_trace(
        self,
        *,
        task: str,
        step: str,
        status: TraceStatus,
        case_id: str | None = None,
        tool: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        decision: str | None = None,
        duration_ms: int | None = None,
        trace_id: str | None = None,
    ) -> Json:
        """TL-21: ``{"trace_id": ...}``. Langkah yang sedang berjalan: kirim ``RUNNING``, lalu panggil lagi dengan
        ``trace_id`` dan status akhir."""
        return self._call(
            "log_trace",
            {"case_id": case_id, "task": task, "step": step, "tool": tool, "input_summary": input_summary,
             "output_summary": output_summary, "decision": decision, "duration_ms": duration_ms,
             "status": status, "trace_id": trace_id},
            keep_null=("case_id",),
        )

    def validate_numbers(self, text: str, allowed: Any) -> Json:
        """TL-24 (G-04): ``{"passed": bool, "offending_numbers": [...]}``; ``allowed`` = JSON yang diberikan ke LLM."""
        return self._call("validate_numbers", {"text": text, "allowed": allowed})
