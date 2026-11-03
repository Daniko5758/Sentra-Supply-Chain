"""Pabrik model SENTRA lewat SAP Cloud SDK for AI (``sap-ai-sdk-gen``) — PRD 7.6, DEC-013.

Satu-satunya cara agen memakai model di SAP Generative AI Hub:

- ``get_model("claude-4.6-sonnet")`` → ``ChatBedrockConverse`` (LangChain) ke deployment
  ``anthropic--claude-4.6-sonnet``. Tool calling dan ``with_structured_output`` didukung.
- ``get_sonar()`` → ``SonarClient`` ke deployment ``sonar`` (klien OpenAI native dari SDK),
  supaya field sitasi mentah (``citations`` / ``search_results``) tetap terbaca (G-02).
  ``get_sonar(purpose="scan")`` → klien kedua dengan batas waktu pendek dan tanpa retry SDK untuk ``scan_external`` (DEC-017).

Token OAuth di-cache dan diperbarui otomatis oleh SDK (thread-safe, diperbarui saat sisa
masa berlaku < 60 menit); kode ini tidak mengurus token sendiri.

Daftar deployment diisi dari env ``DEPLOYMENT_*`` (``StaticDeploymentsProxyClient``), jadi
SDK tidak memanggil API daftar deployment AI Core saat cold start.

Kredensial: env ``AICORE_CLIENT_ID`` / ``AICORE_CLIENT_SECRET`` (``agentcore dev``), atau
Secrets Manager ``AICORE_SECRET_ID`` (default ``sentra/aicore``, key ``client_id`` dan
``client_secret``) di runtime. Tidak ada nilai rahasia di kode ini.

Init lazy (A-15, DEC-016): mengimpor modul ini TIDAK memuat SAP Cloud SDK for AI (``gen_ai_hub``), ``langchain_aws``,
``openai``, ``botocore``, atau ``boto3`` (beberapa detik); semuanya dimuat saat pertama dibutuhkan lewat ``_sdk``. Nama SDK yang dulu
ada di level modul (``ChatBedrockConverse``, ``SapOpenAI``, ``GenAIHubProxyClient``, ``Deployment``, ``Config``,
``StaticDeploymentsProxyClient``) tetap bisa dibaca sebagai atribut modul (``__getattr__``) dan diganti tes lewat ``monkeypatch``.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from tools import news_config

if TYPE_CHECKING:  # hanya untuk anotasi; tidak dimuat saat runtime
    from gen_ai_hub.proxy.gen_ai_hub_proxy import GenAIHubProxyClient
    from gen_ai_hub.proxy.gen_ai_hub_proxy.client import Deployment
    from gen_ai_hub.proxy.langchain.amazon import ChatBedrockConverse

log = logging.getLogger(__name__)

AWS_REGION = "ap-southeast-2"  # SCP akun menolak region lain (dipakai untuk Secrets Manager)
DEFAULT_RESOURCE_GROUP = "default"
DEFAULT_SECRET_ID = "sentra/aicore"
CHAT_MAX_TOKENS = 4096
CHAT_TEMPERATURE = 0.0
READ_TIMEOUT_S = 60
CONNECT_TIMEOUT_S = 10

CHAT_MODEL = "claude-4.6-sonnet"
SONAR_MODEL = "sonar"


@dataclass(frozen=True)
class ModelSpec:
    ai_core_name: str  # nama model di SAP AI Core (Deployment.model_name)
    deployment_env: str  # env var berisi ID deployment


# nama di kode SENTRA -> model di SAP AI Core
MODELS: dict[str, ModelSpec] = {
    CHAT_MODEL: ModelSpec("anthropic--claude-4.6-sonnet", "DEPLOYMENT_CLAUDE"),
    SONAR_MODEL: ModelSpec("sonar", "DEPLOYMENT_SONAR"),
}
CHAT_MODELS = (CHAT_MODEL,)


class ModelConfigError(RuntimeError):
    """Konfigurasi model/kredensial kurang. Pesan hanya memuat NAMA variabel, bukan nilainya."""


# --------------------------------------------------------------------------- impor lambat SDK (A-15)

# nama di modul ini -> (modul, atribut) di SDK; dimuat saat pertama dipakai, lalu disimpan di ``globals()`` supaya tes bisa menggantinya.
_SDK_NAMES: dict[str, tuple[str, str]] = {
    "Config": ("botocore.config", "Config"),
    "GenAIHubProxyClient": ("gen_ai_hub.proxy.gen_ai_hub_proxy", "GenAIHubProxyClient"),
    "Deployment": ("gen_ai_hub.proxy.gen_ai_hub_proxy.client", "Deployment"),
    "ChatBedrockConverse": ("gen_ai_hub.proxy.langchain.amazon", "ChatBedrockConverse"),
    "SapOpenAI": ("gen_ai_hub.proxy.native.openai", "OpenAI"),
}
_sdk_lock = threading.RLock()


def _sdk(name: str) -> Any:
    """Objek SDK ``name`` (impor lambat). Nilai yang sudah ada di modul ini (impor sebelumnya atau pengganti tes) dipakai apa adanya."""
    value = globals().get(name)
    if value is not None:
        return value
    if name == "StaticDeploymentsProxyClient":
        return _static_proxy_class()
    module, attribute = _SDK_NAMES[name]
    value = getattr(importlib.import_module(module), attribute)
    globals()[name] = value
    return value


def __getattr__(name: str) -> Any:  # PEP 562: ``load.SapOpenAI`` dst. tetap terbaca walau dimuat lambat
    if name in _SDK_NAMES or name == "StaticDeploymentsProxyClient":
        return _sdk(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _static_proxy_class() -> Any:
    """Kelas ``StaticDeploymentsProxyClient`` (dibuat sekali, saat SDK pertama dibutuhkan; lihat ``build_proxy_client``)."""
    with _sdk_lock:
        cls = globals().get("StaticDeploymentsProxyClient")
        if cls is None:
            from pydantic import PrivateAttr

            class StaticDeploymentsProxyClient(_sdk("GenAIHubProxyClient")):  # type: ignore[misc]
                """Proxy client SDK dengan daftar deployment tetap (dari env).

                ``get_deployments`` / ``update_deployments`` adalah method publik SDK; di sini keduanya
                mengembalikan daftar statis sehingga tidak ada panggilan API daftar deployment.
                """

                _static_deployments: list = PrivateAttr(default_factory=list)

                def set_static_deployments(self, deployments: Sequence[Deployment]) -> None:
                    self._static_deployments = list(deployments)

                def get_deployments(self):
                    return list(self._static_deployments)

                def update_deployments(self):
                    return list(self._static_deployments)

            cls = globals()["StaticDeploymentsProxyClient"] = StaticDeploymentsProxyClient
        return cls


def _env(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _v2(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v2") else base + "/v2"


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class AICoreSettings:
    auth_url: str  # boleh dengan atau tanpa /oauth/token (SDK menormalkan)
    base_url: str  # AI_API_URL, boleh dengan atau tanpa /v2 (SDK menormalkan)
    resource_group: str
    deployments: Mapping[str, str] = field(default_factory=dict)  # nama SENTRA -> deployment id
    api_version: str | None = None  # opsional, hanya untuk endpoint chat/completions (sonar)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AICoreSettings":
        env = os.environ if env is None else env
        missing = [k for k in ("AICORE_AUTH_URL", "AICORE_BASE_URL") if not _env(env, k)]
        if missing:
            raise ModelConfigError("Missing environment variables: " + ", ".join(missing))
        deployments = {
            name: dep for name, spec in MODELS.items() if (dep := _env(env, spec.deployment_env))
        }
        return cls(
            auth_url=_env(env, "AICORE_AUTH_URL").rstrip("/"),  # type: ignore[union-attr]
            base_url=_env(env, "AICORE_BASE_URL").rstrip("/"),  # type: ignore[union-attr]
            resource_group=_env(env, "AICORE_RESOURCE_GROUP") or DEFAULT_RESOURCE_GROUP,
            deployments=deployments,
            api_version=_env(env, "AICORE_API_VERSION"),
        )

    def deployment_id(self, name: str) -> str:
        if name not in MODELS:
            raise ValueError(f"Unknown model '{name}'; expected one of {sorted(MODELS)}")
        dep = self.deployments.get(name)
        if not dep:
            raise ModelConfigError(f"Missing environment variables: {MODELS[name].deployment_env}")
        return dep

    def deployment_url(self, name: str) -> str:
        return f"{_v2(self.base_url)}/inference/deployments/{self.deployment_id(name)}"


# --------------------------------------------------------------------------- credentials


def load_credentials(
    env: Mapping[str, str] | None = None,
    secrets_client_factory: Callable[[], Any] | None = None,
) -> tuple[str, str]:
    """(client_id, client_secret): env dulu (agentcore dev), lalu Secrets Manager (runtime)."""
    env = os.environ if env is None else env
    client_id, client_secret = _env(env, "AICORE_CLIENT_ID"), _env(env, "AICORE_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    secret_id = _env(env, "AICORE_SECRET_ID") or DEFAULT_SECRET_ID
    if secrets_client_factory is None:
        import boto3  # impor lambat: tidak dibutuhkan saat env sudah lengkap

        def secrets_client_factory() -> Any:
            return boto3.client("secretsmanager", region_name=AWS_REGION)

    try:
        raw = secrets_client_factory().get_secret_value(SecretId=secret_id)["SecretString"]
        secret = json.loads(raw)
        return str(secret["client_id"]), str(secret["client_secret"])
    except Exception as exc:  # jangan bocorkan isi secret lewat pesan error
        raise ModelConfigError(
            f"Cannot read AI Core credentials from env or Secrets Manager secret '{secret_id}' "
            f"({type(exc).__name__})"
        ) from None


# --------------------------------------------------------------------------- proxy client


def build_proxy_client(
    settings: AICoreSettings, client_id: str, client_secret: str
) -> StaticDeploymentsProxyClient:
    proxy = _sdk("StaticDeploymentsProxyClient")(
        base_url=settings.base_url,
        auth_url=settings.auth_url,
        client_id=client_id,
        client_secret=client_secret,
        resource_group=settings.resource_group,
    )
    now = datetime.now(timezone.utc)
    proxy.set_static_deployments(
        [
            _sdk("Deployment")(
                url=settings.deployment_url(name),
                config_id="sentra-static",
                config_name="sentra-static",
                deployment_id=dep,
                model_name=MODELS[name].ai_core_name,
                model_version="latest",
                created_at=now,
            )
            for name, dep in settings.deployments.items()
        ]
    )
    return proxy


# --------------------------------------------------------------------------- models


def build_chat_model(
    name: str, settings: AICoreSettings, proxy: GenAIHubProxyClient, **overrides: Any
) -> ChatBedrockConverse:
    """Buat satu model chat LangChain. ``overrides`` dipakai tes.

    Hanya ``temperature`` yang dikirim (tanpa ``top_p``): model Claude terbaru menolak
    keduanya sekaligus. ``supports_tool_choice_values`` mengaktifkan tool wajib untuk
    ``with_structured_output``.
    """
    if name not in CHAT_MODELS:
        raise ValueError(
            f"'{name}' is not a chat model; expected one of {list(CHAT_MODELS)} "
            f"(use get_sonar() for sonar)"
        )
    kwargs: dict[str, Any] = dict(
        deployment_id=settings.deployment_id(name),
        proxy_client=proxy,
        max_tokens=CHAT_MAX_TOKENS,
        temperature=CHAT_TEMPERATURE,
        supports_tool_choice_values=["auto", "any", "tool"],
        config=_sdk("Config")(
            read_timeout=READ_TIMEOUT_S,
            connect_timeout=CONNECT_TIMEOUT_S,
            retries={"total_max_attempts": 2},
        ),
    )
    kwargs.update(overrides)
    return _sdk("ChatBedrockConverse")(**kwargs)


@dataclass(frozen=True)
class SonarAnswer:
    text: str
    citations: list[str]  # hanya dari field respons model (G-02); kosong = tidak ada sumber
    # DEC-015: ``search_results`` mentah (url, title, snippet, date; tanpa url dibuang) dan, bila ``response_format`` dipakai,
    # isi pesan sebagai JSON mentah. ``date`` BUKAN tanggal terbit tepercaya (SP-03); default kosong agar kode lama jalan.
    search_results: list[dict[str, Any]] = field(default_factory=list)
    raw_json: str | None = None


def extract_citations(extra: Mapping[str, Any] | None) -> list[str]:
    """URL sitasi dari field mentah respons sonar: ``citations`` lalu ``search_results[].url``."""
    extra = extra or {}
    urls: list[str] = []
    for item in extra.get("citations") or []:
        url = item if isinstance(item, str) else (item or {}).get("url")
        if url and url not in urls:
            urls.append(str(url))
    for item in extra.get("search_results") or []:
        url = item.get("url") if isinstance(item, dict) else None
        if url and url not in urls:
            urls.append(str(url))
    return urls


def extract_search_results(extra: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """``search_results`` mentah -> ``[{url, title, snippet, date}]``; entri tanpa ``url`` dibuang."""
    out: list[dict[str, Any]] = []
    for item in (extra or {}).get("search_results") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
            out.append({"url": item["url"], "title": item.get("title"), "snippet": item.get("snippet"),
                        "date": item.get("date")})
    return out


def sonar_answer(resp: Any, *, structured: bool = False) -> SonarAnswer:
    """Respons ``chat.completions`` mentah -> ``SonarAnswer`` (sitasi hanya dari field respons).

    ``structured=True`` (``response_format`` dipakai): isi pesan juga disimpan di ``raw_json`` untuk di-parse pemanggil.
    """
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    return SonarAnswer(
        text=text,
        citations=extract_citations(resp.model_extra),
        search_results=extract_search_results(resp.model_extra),
        raw_json=text if structured else None,
    )


class SonarClient:
    """Pemanggil sonar lewat klien OpenAI native SDK (bukan LangChain) agar sitasi tidak hilang."""

    def __init__(
        self,
        deployment_id: str,
        proxy: GenAIHubProxyClient,
        api_version: str | None = None,
        **client_kwargs: Any,
    ):
        self.deployment_id = deployment_id
        kwargs: dict[str, Any] = dict(proxy_client=proxy, timeout=READ_TIMEOUT_S, max_retries=1)
        if api_version:
            kwargs["api_version"] = api_version
        kwargs.update(client_kwargs)
        self._client = _sdk("SapOpenAI")(**kwargs)

    def create(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Respons mentah ``chat.completions`` (``resp.model_extra`` berisi field sitasi)."""
        return self._client.chat.completions.create(
            deployment_id=self.deployment_id, messages=messages, **kwargs
        )

    def ask(self, messages: list[dict[str, Any]], **kwargs: Any) -> SonarAnswer:
        """``kwargs`` (``extra_body``, ``response_format``, ``temperature``, ``max_tokens``) diteruskan apa adanya."""
        return sonar_answer(self.create(messages, **kwargs), structured="response_format" in kwargs)


# --------------------------------------------------------------------------- per-process cache

_lock = threading.Lock()
_settings: AICoreSettings | None = None
_proxy: StaticDeploymentsProxyClient | None = None
_models: dict[str, ChatBedrockConverse] = {}
_sonar: SonarClient | None = None
_sonar_scan: SonarClient | None = None


def _get_proxy() -> tuple[AICoreSettings, StaticDeploymentsProxyClient]:
    global _settings, _proxy
    if _proxy is None or _settings is None:
        settings = AICoreSettings.from_env()
        client_id, client_secret = load_credentials()
        _proxy = build_proxy_client(settings, client_id, client_secret)
        _settings = settings
    return _settings, _proxy


def get_ai_core() -> tuple[AICoreSettings, StaticDeploymentsProxyClient]:
    """Settings + proxy client per proses (untuk spike, mis. orchestration SP-03)."""
    with _lock:
        return _get_proxy()


def get_model(name: str = CHAT_MODEL, *, read_timeout: int | None = None,
              max_attempts: int | None = None) -> ChatBedrockConverse:
    """Model chat per proses (dibuat sekali per nama, ``read_timeout``, dan ``max_attempts``). Token diperbarui otomatis oleh SDK.

    ``read_timeout`` (detik) dan ``max_attempts`` (jumlah percobaan total, 1 = tanpa ulang) membatasi panggilan yang harus patuh
    anggaran waktu G-05 (mis. ekstraksi ``scan_external``): batas bawaan 60 s x 2 percobaan bisa menghabiskan hampir seluruh jatah
    120 s run (uji live 13 Okt)."""
    if name not in CHAT_MODELS:
        if name in MODELS:
            raise ValueError(f"'{name}' is not a chat model; use get_sonar()")
        raise ValueError(f"Unknown model '{name}'; expected one of {list(CHAT_MODELS)}")
    key = name if read_timeout is None and max_attempts is None else f"{name}@{read_timeout}@{max_attempts}"
    with _lock:
        if key not in _models:
            settings, proxy = _get_proxy()
            overrides: dict[str, Any] = {}
            if read_timeout is not None or max_attempts is not None:
                overrides["config"] = _sdk("Config")(
                    read_timeout=READ_TIMEOUT_S if read_timeout is None else read_timeout,
                    connect_timeout=CONNECT_TIMEOUT_S,
                    retries={"total_max_attempts": 2 if max_attempts is None else max(1, int(max_attempts))})
            _models[key] = build_chat_model(name, settings, proxy, **overrides)
        return _models[key]


def get_sonar(purpose: str | None = None) -> SonarClient:
    """Klien sonar per proses. ``purpose="scan"``: klien kedua untuk ``scan_external`` (``SONAR_SCAN_TIMEOUT_S``, ``max_retries=0``)
    supaya satu pertanyaan lambat tidak menghabiskan jatah waktu run dengan retry SDK; ``None`` = klien bawaan (tidak berubah)."""
    global _sonar, _sonar_scan
    if purpose not in (None, "scan"):
        raise ValueError(f"Unknown sonar purpose '{purpose}'; expected None or 'scan'")
    with _lock:
        settings, proxy = _get_proxy()
        if purpose == "scan":
            if _sonar_scan is None:
                _sonar_scan = SonarClient(settings.deployment_id(SONAR_MODEL), proxy, settings.api_version,
                                          timeout=news_config.SONAR_SCAN_TIMEOUT_S, max_retries=0)
            return _sonar_scan
        if _sonar is None:
            _sonar = SonarClient(settings.deployment_id(SONAR_MODEL), proxy, settings.api_version)
        return _sonar


def reset_model_cache() -> None:
    """Hanya untuk tes."""
    global _settings, _proxy, _sonar, _sonar_scan
    with _lock:
        _settings = None
        _proxy = None
        _sonar = None
        _sonar_scan = None
        _models.clear()
        if globals().get("StaticDeploymentsProxyClient") is not None:  # SDK belum pernah dimuat -> tidak ada cache instance
            _sdk("StaticDeploymentsProxyClient").refresh_instance_cache()
