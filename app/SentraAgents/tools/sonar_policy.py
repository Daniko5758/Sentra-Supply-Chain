"""sonar untuk KEBIJAKAN DAGANG per negara asal impor (TL-04 v1.2, DEC-015, ``docs/NEWS_PIPELINE.md`` 3.4).

sonar bukan sumber kejadian lokal (SP-03) dan bukan penentu fakta: hasilnya hanya KLAIM KANDIDAT bernomor sumber. Kode ini:

- membangun pertanyaan dari watchlist (nama negara dari ``pycountry``, barang dari data; tanpa nama tempat di kode);
- memanggil sonar dengan parameter Perplexity lewat ``extra_body`` (``search_after_date_filter`` ``MM/DD/YYYY``,
  ``search_domain_filter``, ``web_search_options``) dan ``response_format`` ``TradeMeasures``;
- bila gateway menolak parameter (400/422): ulang SEKALI per run tanpa parameter itu, lalu parameter itu mati untuk sisa run
  (hak ulang diambil atomik: pertanyaan paralel lain yang juga ditolak menjadi ``ERROR``, bukan ulang kedua; DEC-017);
- mengubah JSON model menjadi klaim: nomor sumber (1-based) -> ``citations``; URL tidak pernah diambil dari teks model (G-02);
  klaim ``PROPOSED`` / status tak dikenal, negara salah, atau tanpa sumber sah dibuang dengan alasan;
- menormalkan tanggal ke ISO (model sering menulis ``15/9/2026``, SP-09) dan menggabung klaim kembar.

Tanggal dan angka klaim BELUM tepercaya di sini; ``tools/verify.py`` (TL-27) yang menentukan tanggal terbit dan grounding.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from prompts import load_prompt
from tools import news_config as cfg

log = logging.getLogger(__name__)

Json = dict[str, Any]
SYSTEM_PROMPT_NAME = "sonar_trade_policy_system"
MAX_GOODS_PER_QUESTION = 3
MAX_CLAIMS = 5
MEASURE_TYPES = ("TARIFF", "EXPORT_RESTRICTION", "SANCTION", "OTHER")
ADOPTED_STATUSES = ("ANNOUNCED", "IN_FORCE")  # PROPOSED dan status tak dikenal dibuang (NOT_ADOPTED)
OPTIONAL_PARAMS = ("search_after_date_filter", "search_domain_filter", "web_search_options", "response_format")

_DATE_DESC = "ISO 8601 date, exactly YYYY-MM-DD as in the source; null if the source does not state it"
TRADE_MEASURES_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["found", "measures", "notes"],
    "properties": {
        "found": {"type": "boolean"},
        "measures": {"type": "array", "maxItems": MAX_CLAIMS, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["measure_type", "imposing_country", "origin_country", "goods", "rate_text", "rate_change_pp",
                         "announced_date", "effective_date", "status", "source_numbers"],
            "properties": {
                "measure_type": {"enum": list(MEASURE_TYPES)},
                "imposing_country": {"type": "string"}, "origin_country": {"type": "string"},
                "goods": {"type": "string"},
                "rate_text": {"type": ["string", "null"]},
                "rate_change_pp": {"type": ["number", "null"],
                                   "description": "change in percentage points as written in the source; null if not stated"},
                "announced_date": {"type": ["string", "null"], "description": _DATE_DESC},
                "effective_date": {"type": ["string", "null"], "description": _DATE_DESC},
                "status": {"enum": ["PROPOSED", "ANNOUNCED", "IN_FORCE"]},
                "source_numbers": {"type": "array", "items": {"type": "integer"}, "minItems": 1}}}},
        "notes": {"type": "string"}},
}


# --------------------------------------------------------------------------- tanggal


def us_date(d: date) -> str:
    """Format ``search_after_date_filter``: ``MM/DD/YYYY``."""
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def long_date(d: date) -> str:
    """``29 September 2026`` (nama bulan Inggris, tidak bergantung locale)."""
    names = {v: k.capitalize() for k, v in cfg.MONTHS_EN.items()}
    return f"{d.day} {names[d.month]} {d.year}"


def _safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_loose_date(value: Any) -> str | None:
    """Tanggal dari teks model -> ``YYYY-MM-DD`` atau ``None``. Pola angka dibaca hari-dulu (``15/9/2026``)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if m := re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?$", text):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", text):
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))
    months = {**cfg.MONTHS_ID, **cfg.MONTHS_EN}
    names = "|".join(sorted(months, key=len, reverse=True))
    if m := re.match(rf"^(\d{{1,2}})(?:st|nd|rd|th)?\s+({names})\.?,?\s+(\d{{4}})$", text, flags=re.IGNORECASE):
        return _safe_date(int(m[3]), months[m[2].lower()], int(m[1]))
    if m := re.match(rf"^({names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})$", text, flags=re.IGNORECASE):
        return _safe_date(int(m[3]), months[m[1].lower()], int(m[2]))
    return None


# --------------------------------------------------------------------------- pertanyaan dari watchlist


def policy_question(goods: Sequence[str], origin_name: str, home_name: str, start: date, end: date) -> str:
    """Pesan user sonar (template 3.4): satu negara asal, barang dari data, rentang tanggal tertulis (aturan S3, S5)."""
    return (
        "New or changed import tariffs, import duties, anti-dumping duties, export bans or export restrictions, "
        f"or trade sanctions that affect {', '.join(goods)} made in {origin_name} and shipped to {home_name}, "
        f"announced or taking effect between {long_date(start)} and {long_date(end)}."
    )


def build_policy_questions(
    watchlist: Mapping[str, Any], today: date, *, home_country: str | None = None,
    window_days: int = cfg.POLICY_WINDOW_DAYS, max_questions: int = cfg.MAX_SONAR_QUESTIONS,
) -> tuple[list[Json], list[Json]]:
    """(pertanyaan, dilewati). Maks ``max_questions``; satu negara asal ≠ negara rumah per pertanyaan, diurutkan menurut
    ``po_exposure_share`` (data). Negara tanpa barang di watchlist dilewati; negara di luar anggaran juga (``skipped``)."""
    home = (home_country or watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY).upper()
    home_name = cfg.country_name(home)
    start = today - timedelta(days=window_days)
    candidates = [c for c in watchlist.get("countries") or [] if str(c.get("code", "")).upper() != home]
    candidates.sort(key=lambda c: -(c.get("po_exposure_share") or 0.0))

    questions: list[Json] = []
    skipped: list[Json] = []
    for country in candidates:
        code = str(country["code"]).upper()
        goods = [g for g in dict.fromkeys(country.get("goods") or []) if g][:MAX_GOODS_PER_QUESTION]
        if not goods:
            skipped.append({"target": code, "reason": "NO_GOODS"})
        elif len(questions) >= max_questions:
            skipped.append({"target": code, "reason": "BUDGET"})
        else:
            name = country.get("name") or cfg.country_name(code)
            questions.append({"origin_country": code, "origin_name": name, "goods": goods,
                              "question": policy_question(goods, name, home_name, start, today)})
    return questions, skipped


# --------------------------------------------------------------------------- pemanggilan (parameter, ulang sekali, cache)


@dataclass
class PolicyRun:
    """Keadaan satu run scan: parameter yang ditolak gateway dan jatah ulang (maks 1 ulang per run).

    Pertanyaan berjalan paralel (<= ``MAX_SONAR_QUESTIONS`` thread), jadi semua perubahan lewat ``lock``."""

    rejected: set[str] = field(default_factory=set)
    retry_used: bool = False
    requests: int = 0  # request HTTP sungguhan ke sonar (untuk batas G-05)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def count_request(self) -> set[str]:
        """Catat satu request dan kembalikan salinan parameter yang sudah ditolak (untuk membangun argumen request)."""
        with self.lock:
            self.requests += 1
            return set(self.rejected)

    def claim_retry(self, exc: BaseException, ok: Callable[[], bool]) -> list[str]:
        """Ambil hak ulang satu-satunya untuk run ini secara atomik. Mengembalikan parameter yang harus dibuang (kosong = tidak
        boleh mengulang: hak sudah terpakai, parameter tak dikenali, atau ``ok()`` bilang jatah waktu tidak cukup)."""
        with self.lock:
            if self.retry_used or not ok():
                return []
            dropped = rejected_param_names(exc, active_optional_params(self.rejected))
            if dropped:
                self.retry_used = True
                self.rejected.update(dropped)
            return dropped


_cache: dict[str, tuple[float, Json]] = {}
_cache_lock = threading.Lock()


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def build_call_kwargs(today: date, home_country: str, rejected: set[str], *,
                      window_days: int = cfg.POLICY_WINDOW_DAYS) -> Json:
    """Argumen ``sonar.ask``: ``temperature=0``, ``max_tokens=1200``, parameter opsional yang aktif dan belum ditolak."""
    kwargs: Json = {"temperature": 0, "max_tokens": 1200}
    extra: Json = {}
    enabled = cfg.SONAR_PARAMS_ENABLED - rejected
    if "search_after_date_filter" in enabled:
        extra["search_after_date_filter"] = us_date(today - timedelta(days=window_days))
    if "search_domain_filter" in enabled:
        extra["search_domain_filter"] = list(cfg.SONAR_ALLOWLIST_TRADE)
    if "web_search_options" in enabled:
        extra["web_search_options"] = {"search_context_size": "high", "user_location": {"country": home_country}}
    if extra:
        kwargs["extra_body"] = extra
    if cfg.SONAR_RESPONSE_FORMAT and "response_format" not in rejected:
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "TradeMeasures", "schema": TRADE_MEASURES_SCHEMA}}
    return kwargs


def active_optional_params(rejected: set[str]) -> list[str]:
    active = list(cfg.SONAR_PARAMS_ENABLED - rejected)
    if cfg.SONAR_RESPONSE_FORMAT and "response_format" not in rejected:
        active.append("response_format")
    return sorted(active)


def rejected_param_names(exc: BaseException, active: Sequence[str]) -> list[str]:
    """Parameter yang disebut dalam galat 400/422; bila tidak bisa dikenali, semua parameter opsional yang aktif."""
    text = " ".join(str(x) for x in (exc, getattr(exc, "body", ""), getattr(exc, "message", ""))).lower()
    named = [name for name in active if name in text]
    return named or list(active)


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def ask_policy(spec: Mapping[str, Any], sonar: Any, today: date, run: PolicyRun, *,
               home_country: str | None = None, window_days: int = cfg.POLICY_WINDOW_DAYS,
               clock: Callable[[], float] = time.time, retry_ok: Callable[[], bool] = lambda: True) -> Json:
    """Satu pertanyaan kebijakan -> bentuk TL-04 v1.2. Tidak pernah melempar exception (galat -> ``status = "ERROR"``).

    Aman dipanggil paralel dengan satu ``run`` bersama. ``retry_ok`` = apakah jatah waktu masih cukup untuk satu request lagi."""
    question = spec["question"]
    origin = spec["origin_country"]
    with _cache_lock:
        hit = _cache.get(question)
        if hit and clock() < hit[0]:
            return {**hit[1], "requests": 0, "cached": True}

    home = (home_country or cfg.NEWS_HOME_COUNTRY).upper()
    messages = [{"role": "system", "content": load_prompt(SYSTEM_PROMPT_NAME)}, {"role": "user", "content": question}]
    base: Json = {"question": question, "origin_country": origin, "answer": "", "citations": [], "search_results": [],
                  "claims": [], "dropped_claims": [], "params_rejected": [], "requests": 0, "cached": False}
    answer = None
    for attempt in (1, 2):
        kwargs = build_call_kwargs(today, home, run.count_request(), window_days=window_days)
        base["requests"] += 1
        try:
            answer = sonar.ask(messages, **kwargs)
            break
        except Exception as exc:  # noqa: BLE001 - konfigurasi, jaringan, penolakan gateway
            status = _status_of(exc)
            if status in (400, 422) and attempt == 1:
                dropped = run.claim_retry(exc, retry_ok)
                if dropped:
                    base["params_rejected"] = sorted(set(base["params_rejected"]) | set(dropped))
                    log.warning("sonar rejected parameter(s) %s; retrying once without them", dropped)
                    continue
            log.warning("sonar request failed: %s status=%s", type(exc).__name__, status)
            return {**base, "status": "ERROR", "error": type(exc).__name__}

    claims, dropped_claims = parse_claims(answer, origin)
    with run.lock:
        rejected_now = set(run.rejected)
    result = {**base, "status": "OK", "answer": answer.text, "citations": list(answer.citations),
              "search_results": list(answer.search_results), "claims": claims, "dropped_claims": dropped_claims,
              "params_rejected": sorted(set(base["params_rejected"]) | (rejected_now & set(OPTIONAL_PARAMS)))}
    with _cache_lock:
        _cache[question] = (clock() + cfg.SONAR_CACHE_TTL_S, result)
    return result


# --------------------------------------------------------------------------- JSON model -> klaim


def parse_measures(content: str | None) -> tuple[bool, Json | None]:
    """(JSON valid berbentuk TradeMeasures, payload). Pagar kode ```json``` dibuang."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
    try:
        payload = json.loads(text)
    except ValueError:
        return False, None
    ok = isinstance(payload, dict) and isinstance(payload.get("found"), bool) and isinstance(payload.get("measures"), list)
    return ok, payload if ok else None


def numbered_sources(citations: Sequence[str], search_results: Sequence[Mapping[str, Any]]) -> list[str]:
    """URL menurut nomor ``[n]``: ``citations`` bila ada, kalau tidak ``search_results[].url`` (urutan asli)."""
    return list(citations) if citations else [str(r["url"]) for r in search_results if r.get("url")]


def origin_matches(value: Any, origin_code: str) -> bool:
    return isinstance(value, str) and cfg.country_code(value) == origin_code.upper()


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _claim_key(claim: Mapping[str, Any]) -> tuple:
    return (claim["measure_type"], claim["origin_country"], " ".join(str(claim["goods"]).lower().split()),
            claim["effective_date"])


def parse_claims(answer: Any, origin_country: str) -> tuple[list[Json], list[Json]]:
    """(klaim, dibuang). Pasca-proses 3.4 langkah 1-4: nomor sumber -> URL, buang ``NOT_ADOPTED`` / ``WRONG_COUNTRY`` /
    ``NO_VALID_SOURCE``; JSON rusak -> ``INVALID_JSON`` tanpa exception. Klaim kembar digabung (sumbernya disatukan)."""
    ok, payload = parse_measures(getattr(answer, "raw_json", None) or getattr(answer, "text", ""))
    if not ok or payload is None:
        return [], [{"index": None, "reason": "INVALID_JSON"}]

    sources = numbered_sources(answer.citations, answer.search_results)
    claims: list[Json] = []
    dropped: list[Json] = []
    index_of: dict[tuple, int] = {}
    for i, m in enumerate(payload["measures"][:MAX_CLAIMS]):
        if not isinstance(m, dict):
            dropped.append({"index": i, "reason": "INVALID_JSON"})
            continue
        numbers = [n for n in m.get("source_numbers") or [] if isinstance(n, int) and not isinstance(n, bool)]
        urls = list(dict.fromkeys(sources[n - 1] for n in numbers if 1 <= n <= len(sources)))
        if not urls:
            dropped.append({"index": i, "reason": "NO_VALID_SOURCE"})
        elif str(m.get("status", "")).upper() not in ADOPTED_STATUSES:
            dropped.append({"index": i, "reason": "NOT_ADOPTED"})
        elif not origin_matches(m.get("origin_country"), origin_country):
            dropped.append({"index": i, "reason": "WRONG_COUNTRY"})
        else:
            claim: Json = {
                "measure_type": m.get("measure_type") if m.get("measure_type") in MEASURE_TYPES else "OTHER",
                "imposing_country": cfg.country_code(str(m.get("imposing_country") or "")) or m.get("imposing_country"),
                "origin_country": origin_country.upper(), "goods": str(m.get("goods") or "").strip(),
                "rate_text": m.get("rate_text") if isinstance(m.get("rate_text"), str) else None,
                "rate_change_pp": _number(m.get("rate_change_pp")),
                "announced_date": parse_loose_date(m.get("announced_date")),
                "effective_date": parse_loose_date(m.get("effective_date")),
                "status": str(m["status"]).upper(), "source_urls": urls,
            }
            key = _claim_key(claim)
            if key in index_of:  # klaim kembar: satukan sumber, isi angka/tanggal yang kosong
                kept = claims[index_of[key]]
                kept["source_urls"] = list(dict.fromkeys([*kept["source_urls"], *urls]))
                for name in ("rate_text", "rate_change_pp", "announced_date"):
                    if kept[name] is None:
                        kept[name] = claim[name]
            else:
                index_of[key] = len(claims)
                claims.append(claim)
    return claims, dropped


# --------------------------------------------------------------------------- kandidat bukti dari hasil sonar


def candidates_from_sonar(result: Mapping[str, Any]) -> list[Json]:
    """Kandidat bukti (3.5): URL yang dikutip klaim + ``search_results`` lain yang judul/snippet-nya menyebut negara asal.
    ``sonar:date`` hanya petunjuk, bukan tanggal terbit."""
    code = result["origin_country"]
    name = cfg.country_name(code).lower()
    by_url = {r["url"]: r for r in result.get("search_results") or []}
    cited: dict[str, list[int]] = {}
    for idx, claim in enumerate(result.get("claims") or []):
        for url in claim["source_urls"]:
            cited.setdefault(url, []).append(idx)

    urls = list(cited)
    for url, r in by_url.items():
        text = f"{r.get('title') or ''} {r.get('snippet') or ''}".lower()
        if url not in cited and (name in text or re.search(rf"\b{re.escape(code.lower())}\b", text)):
            urls.append(url)

    out = []
    for url in urls:
        r = by_url.get(url, {})
        date_hint = parse_loose_date(r.get("date"))
        out.append({
            "url": url, "engines": ["SONAR"], "title": r.get("title") or "", "snippet": r.get("snippet") or "",
            "domain": (urlparse(url).hostname or "").lower().removeprefix("www."),
            "hint_dates": [{"date": date_hint, "source": "sonar:date"}] if date_hint else [],
            "target": {"scope": "COUNTRY", "kabupaten": None, "country": code},
            "claims": [result["claims"][i] for i in cited.get(url, [])],
        })
    return out
