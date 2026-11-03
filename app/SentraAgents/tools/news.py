"""Klien Brave News Search (TL-25) + pemuat kunci berita (DEC-015, DEC-032, ``docs/NEWS_PIPELINE.md`` 2, 3.9, 3.11).

N-17 (DEC-032): Brave adalah satu-satunya mesin berita (selain sonar). Currents dan GDELT dihapus. Klien thread-safe dengan konkurensi terbatas
dan jarak antar-mulai request; lock HANYA menjaga cache, penghitung ``requests_made`` (batas G-05), dan jadwal mulai, TIDAK dipegang selama
HTTP berjalan. Cache 30 menit HANYA untuk hasil ``OK``. Hasil dinormalisasi ke bentuk TL-25 dengan ``status`` = ``OK`` | ``RATE_LIMITED`` |
``ERROR`` | ``DISABLED`` | ``TIME_BUDGET`` (request tidak dimulai karena jatah waktu node habis; tidak dihitung sebagai request).

Mode laju (``BRAVE_RATE_MODE``):
- ``"auto"``: baca ``X-RateLimit-Limit`` / ``-Remaining`` / ``-Reset`` dari setiap respons (nilai dipisah koma per jendela; jendela pertama = per
  detik). Batas per detik >= 2 -> konkurensi ``min(BRAVE_MAX_CONCURRENCY, batas)`` dan jarak antar-mulai ``BRAVE_MIN_INTERVAL_S``. Batas = 1,
  header tidak ada, atau header rusak -> mode aman (konkurensi 1, jeda ``BRAVE_SAFE_INTERVAL_S``). Sebelum respons pertama klien selalu aman.
  Sisa per detik = 0 -> request berikutnya menunggu ``X-RateLimit-Reset`` (maks ``BRAVE_429_RETRY_MAX_WAIT_S``).
- ``"safe"``: saklar darurat, selalu mode aman.
HTTP 429: satu ulangan setelah menunggu ``X-RateLimit-Reset`` (maks ``BRAVE_429_RETRY_MAX_WAIT_S``; tanpa header: ``BRAVE_SAFE_INTERVAL_S``),
hanya bila ``admit`` (aturan jatah waktu dan jatah request milik node pemanggil) mengizinkan. Ulangan dihitung di ``requests_made``.

Kunci API:
- dibaca ``load_news_keys``: env ``BRAVE_API_KEY`` dulu, lalu Secrets Manager ``NEWS_SECRET_ID`` (default ``sentra/news``, key ``brave_api_key``,
  region ``ap-southeast-2``). ``CURRENTS_API_KEY`` / ``currents_api_key`` diabaikan (tanpa galat). Kunci hilang -> klien ``DISABLED``
  (bukan exception);
- hanya dikirim di header ``X-Subscription-Token``. Tidak pernah muncul di log, exception, ``repr``, atau hasil: pesan galat hanya memuat NAMA
  kelas exception dan kode HTTP.

``page_age`` hanya petunjuk tanggal (``hint_date``); tanggal terbit ditentukan ``tools/verify.py`` (TL-27).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from tools import news_config as cfg

log = logging.getLogger(__name__)

Json = dict[str, Any]
BRAVE_URL = "https://api.search.brave.com/res/v1/news/search"
PAGE_SIZE = 20
MAX_SNIPPET_CHARS = 800
AWS_REGION = "ap-southeast-2"  # SCP akun menolak region lain
DEFAULT_NEWS_SECRET_ID = "sentra/news"

# Label kedua yang lazim pada domain negara (``co.id``, ``com.cn``, ``co.uk`` ...). Cukup untuk eTLD+1 praktis tanpa daftar
# sufiks publik; salah menghitung hanya berarti dua subdomain dianggap domain berbeda/sama.
_SECOND_LEVEL = frozenset({"co", "com", "go", "or", "ac", "net", "org", "gov", "edu", "sch", "web", "mil", "ne", "gob",
                           "gc", "govt"})  # DEC-025: gc (Kanada: weather.gc.ca), govt (Selandia Baru: civildefence.govt.nz)


def registered_domain(host_or_url: str) -> str:
    """eTLD+1 praktis: ``www.a.co.id`` -> ``a.co.id``; ``news.example.com`` -> ``example.com``."""
    host = (urlparse(host_or_url).hostname if "//" in host_or_url else host_or_url) or ""
    host = host.lower().strip(".")
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) or ":" in host:  # alamat IP bukan domain terdaftar
        return host
    labels = [p for p in host.split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def iso_prefix_date(value: Any) -> str | None:
    """``YYYY-MM-DD`` dari awal string ISO (``2026-10-12T08:15:00``, ``2026-10-12 08:15:00 +0000``); selain itu ``None``."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- kunci


@dataclass(frozen=True)
class NewsKeys:
    brave: str | None = None

    def __repr__(self) -> str:  # kunci tidak boleh tercetak
        return f"NewsKeys(brave={'set' if self.brave else None})"


def load_news_keys(env: Mapping[str, str] | None = None,
                   secrets_client_factory: Callable[[], Any] | None = None) -> NewsKeys:
    """Env dulu, lalu Secrets Manager bila kunci Brave kosong. Tidak pernah melempar exception dan tidak mencetak kunci. Kunci Currents di
    env/secret diabaikan (N-17)."""
    env = os.environ if env is None else env
    brave = (env.get("BRAVE_API_KEY") or "").strip() or None
    if brave:
        return NewsKeys(brave)

    secret_id = (env.get("NEWS_SECRET_ID") or "").strip() or DEFAULT_NEWS_SECRET_ID
    try:
        if secrets_client_factory is None:
            import boto3  # impor lambat: tidak dibutuhkan saat env lengkap

            def secrets_client_factory() -> Any:
                return boto3.client("secretsmanager", region_name=AWS_REGION)

        raw = secrets_client_factory().get_secret_value(SecretId=secret_id)["SecretString"]
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("secret is not a JSON object")
        brave = str(parsed.get("brave_api_key") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 - jangan bocorkan isi secret lewat pesan error
        log.warning("news keys unavailable from Secrets Manager (%s): client will be DISABLED", type(exc).__name__)
    return NewsKeys(brave)


# --------------------------------------------------------------------------- header batas laju


@dataclass(frozen=True)
class RateInfo:
    """Jendela PERTAMA (per detik) dari header ``X-RateLimit-*``; ``None`` bila header tidak ada atau rusak."""

    limit: int | None = None
    remaining: float | None = None
    reset: float | None = None


def _first_number(value: Any) -> float | None:
    """Angka pertama dari nilai header yang dipisah koma (``"50, 15000"`` -> 50); ``None`` bila bukan angka berhingga >= 0."""
    if not isinstance(value, str):
        return None
    try:
        number = float(value.split(",")[0].strip())
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_rate_headers(headers: Mapping[str, Any] | None) -> RateInfo:
    """``X-RateLimit-Limit`` / ``-Remaining`` / ``-Reset`` (nama header tidak peka huruf besar). Batas < 1 dianggap rusak."""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    limit = _first_number(lowered.get("x-ratelimit-limit"))
    return RateInfo(limit=int(limit) if limit is not None and limit >= 1 else None,
                    remaining=_first_number(lowered.get("x-ratelimit-remaining")),
                    reset=_first_number(lowered.get("x-ratelimit-reset")))


# --------------------------------------------------------------------------- Brave (TL-25)


def _brave_items(payload: Any) -> list[Json]:
    if not isinstance(payload, dict):
        raise TypeError("unexpected payload")
    raw = payload.get("results")
    if raw is None and isinstance(payload.get("news"), dict):
        raw = payload["news"].get("results")
    items = []
    for r in raw or []:
        if not isinstance(r, dict) or not isinstance(r.get("url"), str) or not r["url"].startswith(("http://", "https://")):
            continue  # tanpa URL sah tidak berguna sebagai sumber (G-02)
        extra = [s for s in (r.get("extra_snippets") or []) if isinstance(s, str)]
        snippet = " ".join([str(r.get("description") or ""), *extra]).strip()
        hint = iso_prefix_date(r.get("page_age"))
        items.append({
            "url": r["url"], "title": str(r.get("title") or "").strip(), "snippet": snippet[:MAX_SNIPPET_CHARS],
            "domain": registered_domain(r["url"]),
            "hint_date": {"date": hint, "source": "brave:page_age"} if hint else None,
        })
    return items


Admit = Callable[[float, bool], bool]
"""``admit(wait_s, retry)``: dipanggil klien tepat sebelum request dimulai, dengan lama tunggu (detik) sampai request itu boleh mulai dan
apakah ini ulangan 429. ``False`` -> request tidak dikirim (``TIME_BUDGET`` untuk percobaan pertama; ulangan: tetap ``RATE_LIMITED``)."""


class BraveNewsClient:
    source = "brave"
    default_timeout: float | tuple[float, float] = cfg.BRAVE_TIMEOUT_S  # (connect, read); jatah waktu per sumber di news_config (DEC-017)

    def __init__(self, api_key: str | None, base_url: str = BRAVE_URL, *, min_interval: float | None = None,
                 cache_ttl: float = cfg.NEWS_CACHE_TTL_S, timeout: float | tuple[float, float] | None = None,
                 session: requests.Session | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, rate_mode: str | None = None,
                 max_concurrency: int | None = None) -> None:
        """``min_interval`` (opsional, terutama tes): jarak antar-mulai tetap di SEMUA mode; tanpa itu: ``BRAVE_MIN_INTERVAL_S`` di mode auto
        paralel dan ``BRAVE_SAFE_INTERVAL_S`` di mode aman. ``rate_mode`` / ``max_concurrency`` bawaan dari ``news_config``."""
        self._key = (api_key or "").strip() or None
        self.base_url = base_url
        self._fixed_interval = min_interval
        self.cache_ttl = cache_ttl
        self.timeout = self.default_timeout if timeout is None else timeout
        self.rate_mode = cfg.BRAVE_RATE_MODE if rate_mode is None else rate_mode
        self.max_concurrency = max(1, cfg.BRAVE_MAX_CONCURRENCY if max_concurrency is None else max_concurrency)
        self.requests_made = 0  # request HTTP sungguhan termasuk ulangan 429 (cache dan DISABLED tidak dihitung); untuk batas G-05
        self._session = session or requests.Session()
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._inflight = 0
        self._next_start: float | None = None  # saat paling awal request berikutnya boleh mulai (jam klien)
        self._not_before = 0.0  # sisa per detik 0 / 429: tidak ada request mulai sebelum ini
        self._limit: int | None = None  # batas per detik dari respons terakhir; None = belum ada / rusak -> mode aman
        self._cache: dict[tuple, tuple[float, Json]] = {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(enabled={self.enabled}, requests_made={self.requests_made})"

    @property
    def enabled(self) -> bool:
        return self._key is not None

    @property
    def read_timeout(self) -> float:
        """Batas baca (detik) satu request; dipakai graph untuk memutuskan apakah request baru masih muat di jatah waktu."""
        return float(self.timeout[1] if isinstance(self.timeout, tuple) else self.timeout)

    @property
    def concurrency(self) -> int:
        """Jumlah request yang boleh berjalan bersamaan SAAT INI (1 = serial: mode aman, belum ada respons, atau batas per detik = 1)."""
        with self._lock:
            return self._max_inflight()

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()
            self._next_start = None
            self._not_before = 0.0
            self._limit = None
            self.requests_made = 0

    # --- jadwal (semua dipanggil dengan ``self._lock`` dipegang)

    def _fast(self) -> bool:
        return self.rate_mode != "safe" and self._limit is not None and self._limit >= 2

    def _max_inflight(self) -> int:
        return min(self.max_concurrency, self._limit) if self._fast() and self._limit else 1

    def _interval(self) -> float:
        if self._fixed_interval is not None:
            return self._fixed_interval
        return cfg.BRAVE_MIN_INTERVAL_S if self._fast() else cfg.BRAVE_SAFE_INTERVAL_S

    def _acquire(self, admit: Admit | None, retry: bool) -> float | None:
        """Tunggu slot konkurensi, tentukan saat mulai, tanya ``admit``, lalu catat request. Mengembalikan saat mulai (jam klien) atau ``None``
        bila ``admit`` menolak. Tidur sampai saat mulai terjadi DI LUAR lock."""
        with self._cond:
            while self._inflight >= self._max_inflight():
                self._cond.wait()
            now = self._clock()
            start_at = max(now, self._next_start if self._next_start is not None else now, self._not_before)
            wait = start_at - now
            if admit is not None and not admit(wait, retry):
                return None
            self._inflight += 1
            self.requests_made += 1
            self._next_start = start_at + self._interval()
        if wait > 0:
            self._sleep(wait)
        return start_at

    def _release(self, resp: requests.Response | None) -> None:
        """Lepas slot dan (bila ada respons) perbarui mode laju dari header."""
        info = parse_rate_headers(getattr(resp, "headers", None)) if resp is not None else None
        with self._cond:
            if info is not None:
                was_fast = self._fast()
                self._limit = info.limit
                now = self._clock()
                if resp is not None and resp.status_code == 429:
                    wait = min(info.reset, cfg.BRAVE_429_RETRY_MAX_WAIT_S) if info.reset is not None else cfg.BRAVE_SAFE_INTERVAL_S
                    self._not_before = max(self._not_before, now + wait)
                elif info.remaining is not None and info.remaining <= 0 and info.reset is not None:
                    self._not_before = max(self._not_before, now + min(info.reset, cfg.BRAVE_429_RETRY_MAX_WAIT_S))
                if self._fast() and not was_fast and self._next_start is not None and self._fixed_interval is None:
                    self._next_start = min(self._next_start, now + cfg.BRAVE_MIN_INTERVAL_S)  # jeda aman pertama tidak berlaku lagi
                elif not self._fast():
                    self._next_start = max(self._next_start or now, now + self._interval())  # mode aman: jeda dihitung dari akhir request
            self._inflight -= 1
            self._cond.notify_all()

    def _result(self, query: str, target: Mapping[str, Any] | None, status: str, items: list[Json] | None = None,
                *, cached: bool = False) -> Json:
        return {"query": query, "target": dict(target or {}), "status": status, "cached": cached,
                "items": items or []}

    def _fetch(self, cache_key: tuple, query: str, target: Mapping[str, Any] | None,
               request: Callable[[], requests.Response], parse: Callable[[Any], list[Json]], admit: Admit | None) -> Json:
        if not self.enabled:
            return self._result(query, target, "DISABLED")
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and self._clock() < hit[0]:
                return {**hit[1], "target": dict(target or {}), "cached": True}

        result: Json | None = None
        for attempt in (0, 1):  # satu ulangan untuk HTTP 429 (N-17 4.3)
            if self._acquire(admit, retry=bool(attempt)) is None:
                return result if result is not None else self._result(query, target, "TIME_BUDGET")
            try:
                resp = request()
            except requests.RequestException as exc:
                log.warning("%s request failed: %s", self.source, type(exc).__name__)
                self._release(None)
                return self._result(query, target, "ERROR")
            except BaseException:
                self._release(None)
                raise
            self._release(resp)

            status, items = self._classify(resp, parse)
            result = self._result(query, target, status, items)
            if status == "OK":
                with self._lock:
                    self._cache[cache_key] = (self._clock() + self.cache_ttl, result)
            elif status != "RATE_LIMITED":
                log.warning("%s returned HTTP %s", self.source, resp.status_code)
            if status != "RATE_LIMITED":
                break
        return result  # type: ignore[return-value]

    @staticmethod
    def _classify(resp: requests.Response, parse: Callable[[Any], list[Json]]) -> tuple[str, list[Json]]:
        if resp.status_code == 429:
            return "RATE_LIMITED", []
        if resp.status_code != 200:
            return "ERROR", []
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR", []
        try:
            return "OK", parse(payload)
        except (TypeError, ValueError, KeyError, AttributeError):
            return "ERROR", []

    def search(self, query: str, *, country: str = "ALL", search_lang: str | None = None, start: date, end: date,
               target: Mapping[str, Any] | None = None, admit: Admit | None = None) -> Json:
        """TL-25. ``country`` di luar ``BRAVE_COUNTRY_SUPPORTED`` dikirim sebagai ``ALL``; ``search_lang=None`` = tidak dikirim.
        ``admit`` (opsional): lihat ``Admit``."""
        country = country.upper() if country.upper() in cfg.BRAVE_COUNTRY_SUPPORTED else "ALL"
        params: Json = {"q": query, "country": country, "count": PAGE_SIZE, "extra_snippets": "true",
                        "freshness": f"{start.isoformat()}to{end.isoformat()}"}
        if search_lang:
            params["search_lang"] = search_lang
        return self._fetch(
            (query, country, search_lang, params["freshness"]), query, target,
            lambda: self._session.get(self.base_url, params=params, timeout=self.timeout, headers={
                "X-Subscription-Token": self._key or "", "Accept": "application/json"}),
            _brave_items, admit,
        )


_client: BraveNewsClient | None = None
_client_lock = threading.Lock()


def get_news_clients() -> BraveNewsClient:
    """Satu klien Brave per proses (jadwal laju dan cache dibagi semua run); kunci dari ``load_news_keys``. Nama dipertahankan dari sebelum
    N-17 (dulu tuple Brave + Currents)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = BraveNewsClient(load_news_keys().brave)
        return _client


def reset_news_clients() -> None:
    """Hanya untuk tes."""
    global _client
    with _client_lock:
        _client = None
