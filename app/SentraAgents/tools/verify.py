"""Verifikasi tanggal terbit dan grounding angka (TL-27 ``verify_sources``, G-08, DEC-015, ``docs/NEWS_PIPELINE.md`` 3.5, 3.6).

Kode murni (bukan LLM, bukan metadata mesin pencari). Untuk tiap kandidat bukti:

1. halaman indeks/tag/kategori/pencarian TIDAK diambil (``INDEX_PAGE``);
2. halaman diambil (maks ``max_pages`` per scan, ``workers`` paralel, timeout connect 3 s / baca 6 s dan jatah total ``deadline``,
   baca maks 400 KB, User-Agent tetap);
   HTTP bukan 200 atau galat jaringan -> ``FETCH_FAILED`` (bukan galat run);
3. kandidat tanggal terbit dikumpulkan: JSON-LD ``datePublished`` -> meta (``article:published_time`` ...) -> ``<time datetime>``
   -> tanggal di path URL -> tanggal pertama di teks; ``published_at`` = kandidat TERAWAL, ``date_conflict`` bila selisih > 3 hari
   (``<meta itemprop="datePublished">`` sering berisi waktu pengambilan halaman, jadi yang terawal paling aman);
4. petunjuk tanggal mesin pencari (``hint_dates``) hanya boleh membuat artikel tampak LEBIH TUA (selisih > 3 hari), tidak pernah
   lebih baru, dan tidak pernah menggantikan tanggal halaman yang tidak ditemukan;
5. ``date_status``: ``IN_WINDOW`` / ``STALE`` / ``FUTURE`` / ``NOT_FOUND`` / ``FETCH_FAILED`` / ``INDEX_PAGE`` terhadap jendela per
   target (``EVENT_WINDOW_DAYS`` untuk ``LOCATION``, ``POLICY_WINDOW_DAYS`` untuk ``COUNTRY``);
6. grounding klaim sonar: ``rate_change_pp`` dan ``effective_date`` harus TERTULIS di teks halaman.

Isi halaman tidak dikirim ke LLM, tidak dicatat ke log, dan tidak disimpan; hanya judul/deskripsi og (maks 300 karakter) yang
dikembalikan. ``verify_sources`` tidak pernah melempar exception.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

import requests

from tools import news_config as cfg
from tools.news import iso_prefix_date, registered_domain

log = logging.getLogger(__name__)

Json = dict[str, Any]
MAX_PAGE_BYTES = 400_000
FETCH_TIMEOUT_S = cfg.FETCH_TIMEOUT_S  # (connect, read); jatah total satu batch: ``deadline`` (DEC-017)
MIN_FETCH_TIMEOUT_S = 0.5
FETCH_WORKERS = 6  # N-17 (DEC-032): 4 -> 6; 48 halaman ~ 6-7 s, di bawah jatah VERIFY_TIME_BUDGET_S (10 s)
DATE_TOLERANCE_DAYS = 3  # selisih kandidat tanggal / petunjuk yang masih dianggap sama (zona waktu, pembaruan)
MAX_TEXT_CHARS = 300
USER_AGENT = "Mozilla/5.0 (compatible; SENTRA-verify/1.0)"

INDEX_PATH_MARKERS = ("/tag/", "/tags/", "/label/", "/category/", "/topic/", "/topics/", "/city/", "/search")
_META_NAMES = r"article:published_time|og:published_time|datepublished|pubdate|publishdate|dc\.date\.issued|date"
_MONTHS = {**cfg.MONTHS_ID, **cfg.MONTHS_EN, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12, "agu": 8, "okt": 10, "des": 12, "agt": 8}
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_MONTH_FIRST = re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_CJK_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_REIWA_DATE = re.compile(r"令和\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# DEC-026: tanggal angka + jam (jam wajib); dipakai setelah NFKC, jadi angka/pemisah lebar penuh sudah menjadi ASCII
_ISO_DATETIME = re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})[ T]+(\d{1,2}):(\d{2})(?!\d)")
_URL_PATH_DATE = re.compile(r"/((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})(?=[/\-_.]|$)")
_URL_COMPACT_DATE = re.compile(r"/((?:19|20)\d{2})(\d{2})(\d{2})(?:\d{2,8})?(?=[/\-_.]|$)")


# --------------------------------------------------------------------------- jenis halaman


def looks_like_index_page(url: str) -> bool:
    """Heuristik kasar: halaman tag/kategori/pencarian/beranda, bukan satu artikel."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return (any(marker in path + "/" for marker in INDEX_PATH_MARKERS) or path in ("", "/")
            or parsed.query.lower().startswith(("s=", "q=", "search=")))


# --------------------------------------------------------------------------- tanggal terbit


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _iso_date(value: str) -> date | None:
    text = iso_prefix_date(value)
    return date.fromisoformat(text) if text else None


def url_path_date(url: str) -> date | None:
    """Tanggal di path URL: ``/2026/10/12/`` atau ``/20261012110157-...``."""
    path = urlparse(url).path
    for pattern in (_URL_PATH_DATE, _URL_COMPACT_DATE):
        if m := pattern.search(path):
            if d := _safe_date(int(m[1]), int(m[2]), int(m[3])):
                return d
    return None


def _first_text_date(text: str, country: str | None = None) -> date | None:
    """Tanggal pertama (menurut posisi) di teks tampak: ``14 November 2026``, ``Senin, 12 Oktober 2026``, ``November 14, 2026``.

    DEC-025 (N-12 2.4): teks dinormalkan NFKC lebih dulu (angka lebar penuh ``２０２６`` menjadi ``2026``); untuk negara target CN/JP
    juga ``2026年10月16日``, dan untuk JP era ``令和8年10月16日`` (``TEXT_DATE_STYLES_BY_COUNTRY``). DEC-026: CN/JP juga ``2026-10-14 14:34``
    (tanggal angka dengan jam wajib). Pola Indonesia/Inggris tetap aktif."""
    text = unicodedata.normalize("NFKC", text)
    styles = cfg.TEXT_DATE_STYLES_BY_COUNTRY.get((country or "").strip().upper(), ())
    found: list[tuple[int, date]] = []
    if "cjk" in styles:
        for m in _CJK_DATE.finditer(text):
            if d := _safe_date(int(m[1]), int(m[2]), int(m[3])):
                found.append((m.start(), d))
    if "reiwa" in styles:
        for m in _REIWA_DATE.finditer(text):
            year = cfg.REIWA_BASE_YEAR + (1 if m[1] == "元" else int(m[1]))
            if d := _safe_date(year, int(m[2]), int(m[3])):
                found.append((m.start(), d))
    if "iso_datetime" in styles:
        for m in _ISO_DATETIME.finditer(text):
            if d := _safe_date(int(m[1]), int(m[2]), int(m[3])):
                found.append((m.start(), d))
    for m in _DAY_FIRST.finditer(text):
        if d := _safe_date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])):
            found.append((m.start(), d))
    for m in _MONTH_FIRST.finditer(text):
        if d := _safe_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2])):
            found.append((m.start(), d))
    return min(found)[1] if found else None


def page_text(html: str) -> str:
    """Teks tampak (tanpa script/style/tag); dipakai untuk tanggal teks dan grounding, tidak pernah dicatat."""
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def collect_date_candidates(html: str, url: str = "", country: str | None = None) -> list[tuple[date, str]]:
    """Semua kandidat tanggal terbit: meta, JSON-LD, ``<time datetime>``, path URL, teks pertama (``country`` = negara target
    kandidat; menentukan pola tanggal teks tambahan, DEC-025)."""
    found: list[tuple[date, str]] = []
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
        name = re.search(r"(?:property|name|itemprop)\s*=\s*[\"']([^\"']+)[\"']", tag, flags=re.I)
        content = re.search(r"content\s*=\s*[\"']([^\"']+)[\"']", tag, flags=re.I)
        if name and content and re.fullmatch(_META_NAMES, name.group(1).strip().lower()):
            if parsed := _iso_date(content.group(1)):
                found.append((parsed, f"meta:{name.group(1).strip().lower()}"))
    for value in re.findall(r"\"datePublished\"\s*:\s*\"([^\"]+)\"", html)[:3]:
        if parsed := _iso_date(value):
            found.append((parsed, "json-ld:datePublished"))
    if m := re.search(r"<time\b[^>]*\bdatetime\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.I):
        if parsed := _iso_date(m.group(1)):
            found.append((parsed, "time[datetime]"))
    if url and (d := url_path_date(url)):
        found.append((d, "url:path"))
    if d := _first_text_date(page_text(html), country):
        found.append((d, "text"))
    return found


def extract_publish_date(html: str, url: str = "", country: str | None = None) -> tuple[date | None, str, bool]:
    """(``published_at``, sumber, ``date_conflict``): kandidat TERAWAL; konflik bila kandidat lain > 3 hari lebih baru."""
    found = collect_date_candidates(html, url, country)
    if not found:
        return None, "none", False
    best, source = min(found, key=lambda item: item[0])  # min stabil: seri -> kandidat pertama
    conflict = (max(d for d, _ in found) - best) > timedelta(days=DATE_TOLERANCE_DAYS)
    return best, source, conflict


def apply_hints(published: date | None, source: str, hints: Iterable[Mapping[str, Any]]) -> tuple[date | None, str]:
    """Petunjuk mesin pencari hanya boleh MEMPERTUA tanggal halaman (> 3 hari lebih awal). Tanpa tanggal halaman: tidak berlaku."""
    if published is None:
        return None, source
    for hint in hints:
        d = iso_prefix_date(hint.get("date"))
        if d and (published - date.fromisoformat(d)) > timedelta(days=DATE_TOLERANCE_DAYS):
            published, source = date.fromisoformat(d), f"hint:{hint.get('source', 'unknown')}"
    return published, source


def window_days(target: Mapping[str, Any] | None) -> int:
    """Jendela per target: ``EVENT_WINDOW_DAYS`` (LOCATION) atau ``POLICY_WINDOW_DAYS`` (COUNTRY)."""
    return cfg.POLICY_WINDOW_DAYS if (target or {}).get("scope") == "COUNTRY" else cfg.EVENT_WINDOW_DAYS


def date_status(published: date | None, today: date, window: int) -> str:
    if published is None:
        return "NOT_FOUND"
    age = (today - published).days
    if age < -1:
        return "FUTURE"
    return "IN_WINDOW" if age <= window else "STALE"


def page_meta_text(html: str) -> tuple[str, str]:
    """(judul, deskripsi) dari og:title / og:description (cadangan ``<title>`` / meta description), maks 300 karakter."""
    def meta(*names: str) -> str:
        for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
            name = re.search(r"(?:property|name)\s*=\s*[\"']([^\"']+)[\"']", tag, flags=re.I)
            content = re.search(r"content\s*=\s*[\"']([^\"']*)[\"']", tag, flags=re.I)
            if name and content and name.group(1).strip().lower() in names:
                return " ".join(content.group(1).split())
        return ""

    title = meta("og:title")
    if not title and (m := re.search(r"(?is)<title[^>]*>(.*?)</title>", html)):
        title = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
    description = meta("og:description", "description")
    return title[:MAX_TEXT_CHARS], description[:MAX_TEXT_CHARS]


# --------------------------------------------------------------------------- grounding angka dan tanggal klaim


def rate_in_text(rate: float | int | None, text: str) -> bool | None:
    """Apakah ``rate`` (poin persen) tertulis di ``text`` sebagai ``10%``, ``10 percent``, ``10 persen``, ``10 percentage points``,
    ``10 poin persen`` (desimal titik/koma). ``None`` bila ``rate`` kosong."""
    if rate is None:
        return None
    number = float(rate)
    if number == int(number):
        body = rf"{int(number)}(?:[.,]0+)?"
    else:
        whole, _, frac = f"{number:.6f}".rstrip("0").partition(".")
        body = rf"{whole}[.,]{frac}"
    unit = r"\s*(?:%|percent\b|persen\b|percentage\s+points?\b|poin\s+persen\b|persentase\s+poin\b)"
    return bool(re.search(rf"(?<![\d.,]){body}(?!\d){unit}", text, flags=re.IGNORECASE))


def date_in_text(iso: str | None, text: str) -> bool | None:
    """Apakah tanggal ISO tertulis di ``text`` (ISO, ``14 November 2026``, ``November 14, 2026``, ``14 Nov 2026``, bulan Indonesia).
    ``None`` bila ``iso`` kosong; ``False`` bila tidak ada atau bukan tanggal sah."""
    if iso is None:
        return None
    d = iso_prefix_date(iso)
    if d is None:
        return False
    parsed = date.fromisoformat(d)
    names = {n for n, v in _MONTHS.items() if v == parsed.month}
    mon = "(?:" + "|".join(sorted(names, key=len, reverse=True)) + r")\.?"
    day = rf"0?{parsed.day}(?:st|nd|rd|th)?"
    patterns = [
        rf"(?<!\d){parsed.year}-{parsed.month:02d}-{parsed.day:02d}(?!\d)",
        rf"(?<!\d){day}\s+{mon},?\s+{parsed.year}\b",
        rf"\b{mon}\s+{day},?\s+{parsed.year}\b",
    ]
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def ground_claims(claims: Sequence[Mapping[str, Any]], text: str) -> tuple[list[Json], Json]:
    """(per klaim, agregat). Agregat per URL: ``True`` bila ada klaim yang cocok, ``False`` bila ada klaim berangka/tanggal tetapi
    tak satu pun cocok, ``None`` bila tak ada klaim yang memuat nilai itu."""
    per_claim = [{"claim_index": i, "rate": rate_in_text(c.get("rate_change_pp"), text),
                  "effective_date": date_in_text(c.get("effective_date"), text)} for i, c in enumerate(claims)]
    aggregate: Json = {}
    for name in ("rate", "effective_date"):
        values = [g[name] for g in per_claim if g[name] is not None]
        aggregate[name] = None if not values else any(values)
    return per_claim, aggregate


# --------------------------------------------------------------------------- pengambilan halaman


def _clamped(timeout: float | tuple[float, float], remaining: float | None) -> float | tuple[float, float]:
    """Batas connect/baca tidak boleh melewati sisa jatah waktu batch (minimal ``MIN_FETCH_TIMEOUT_S``)."""
    if remaining is None:
        return timeout
    cap = max(remaining, MIN_FETCH_TIMEOUT_S)
    if isinstance(timeout, tuple):
        return tuple(min(t, cap) for t in timeout)  # type: ignore[return-value]
    return min(timeout, cap)


_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.IGNORECASE)
SNIFF_BYTES = 4096


def decode_page(raw: bytes, content_type: str = "") -> str:
    """Teks halaman dari byte mentah (DEC-025: halaman CJK harus terbaca supaya pembaca tanggal ``年月日`` berguna). Urutan: charset di
    header ``Content-Type``, lalu ``<meta charset>`` di ``SNIFF_BYTES`` awal, selain itu UTF-8. ``requests`` memberi ISO-8859-1 pada
    ``text/*`` tanpa charset, yang merusak teks Mandarin/Jepang; nama charset tak dikenal -> UTF-8. Tidak melempar exception."""
    found = _HEADER_CHARSET.search(content_type or "")
    charset = found.group(1) if found else None
    if charset is None and (meta := _META_CHARSET.search(raw[:SNIFF_BYTES])):
        charset = meta.group(1).decode("ascii", "ignore")
    for name in (charset, "utf-8"):
        if not name:
            continue
        try:
            return raw.decode(name, "replace")
        except LookupError:
            continue
    return raw.decode("utf-8", "replace")


def fetch_html(session: requests.Session, url: str, timeout: float | tuple[float, float] = FETCH_TIMEOUT_S, *,
               deadline: float | None = None, clock: Callable[[], float] = time.monotonic) -> tuple[str | None, str]:
    """(html, galat). Membaca maks 400 KB; tidak melempar exception; isi tidak dicatat. Bukan-HTML -> ("", "NON_HTML").

    ``deadline`` (waktu ``clock``) = jatah batch: sudah lewat sebelum mulai -> ``(None, "TIME_BUDGET")``; lewat saat isi dibaca
    -> berhenti dengan galat yang sama (halaman separuh tidak dipakai)."""
    if deadline is not None and clock() >= deadline:
        return None, "TIME_BUDGET"
    try:
        resp = session.get(url, timeout=_clamped(timeout, None if deadline is None else deadline - clock()),
                           stream=True, allow_redirects=True,
                           headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    except Exception as exc:  # noqa: BLE001 - jaringan, TLS, URL rusak
        return None, type(exc).__name__
    try:
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        content_type = resp.headers.get("content-type", "").lower()
        if content_type and not any(t in content_type for t in ("html", "xml", "text/plain")):
            return "", "NON_HTML"
        chunks, size = [], 0
        for chunk in resp.iter_content(chunk_size=16_384):
            if deadline is not None and clock() >= deadline:
                return None, "TIME_BUDGET"
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_PAGE_BYTES:
                break
        return decode_page(b"".join(chunks)[:MAX_PAGE_BYTES], content_type), ""
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    finally:
        resp.close()


# --------------------------------------------------------------------------- TL-27


def _result(url: str, status: str, **fields: Any) -> Json:
    return {"url": url, "domain": registered_domain(url), "published_at": None, "date_source": None, "date_status": status,
            "date_conflict": False, "page_title": "", "page_description": "", "grounded": {"rate": None, "effective_date": None},
            "claim_grounding": [], **fields}


def _verify_one(candidate: Mapping[str, Any], html: str | None, error: str, today: date) -> Json:
    url = candidate["url"]
    if html is None:
        return _result(url, "FETCH_FAILED", fetch_error=error)
    published, source, conflict = extract_publish_date(html, url, (candidate.get("target") or {}).get("country"))
    published, source = apply_hints(published, source, candidate.get("hint_dates") or [])
    status = date_status(published, today, window_days(candidate.get("target")))
    title, description = page_meta_text(html)
    per_claim, aggregate = ground_claims(candidate.get("claims") or [], page_text(html))
    return _result(url, status, published_at=published.isoformat() if published else None,
                   date_source=source if published else None, date_conflict=conflict, page_title=title,
                   page_description=description, grounded=aggregate, claim_grounding=per_claim)


def verify_sources(
    candidates: Sequence[Mapping[str, Any]], today: date, *, max_pages: int = cfg.MAX_VERIFY_PAGES,
    workers: int = FETCH_WORKERS, timeout: float | tuple[float, float] = FETCH_TIMEOUT_S,
    session: requests.Session | None = None, deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Json:
    """TL-27. ``candidates`` bentuk 3.5 (``url``, ``hint_dates``, ``target``, ``claims``). Mengembalikan
    ``{"results": [...], "stats": {...}}``; hasil mengikuti urutan masukan (urutan = prioritas). Halaman indeks tidak diambil dan
    tidak memakai jatah ``max_pages``; kandidat di luar jatah dilaporkan di ``stats["skipped_budget"]`` (tanpa hasil).
    ``deadline`` (waktu ``clock``) membatasi total waktu batch: halaman yang belum mulai diambil saat tenggat lewat menjadi
    ``FETCH_FAILED`` dengan ``fetch_error = "TIME_BUDGET"`` (tidak pernah galat run)."""
    session = session or requests.Session()
    ordered: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for c in candidates:
        if isinstance(c, Mapping) and isinstance(c.get("url"), str) and c["url"] not in seen:
            seen.add(c["url"])
            ordered.append(c)

    to_fetch: list[Mapping[str, Any]] = []
    plan: list[tuple[Mapping[str, Any], str]] = []  # (kandidat, "index" | "fetch" | "budget")
    for c in ordered:
        if looks_like_index_page(c["url"]):
            plan.append((c, "index"))
        elif len(to_fetch) < max_pages:
            to_fetch.append(c)
            plan.append((c, "fetch"))
        else:
            plan.append((c, "budget"))

    def fetch(c: Mapping[str, Any]) -> tuple[str | None, str]:
        return fetch_html(session, c["url"], timeout, deadline=deadline, clock=clock)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            fetched = dict(zip((c["url"] for c in to_fetch), pool.map(fetch, to_fetch)))
    except Exception as exc:  # noqa: BLE001 - tidak boleh melempar
        log.warning("verify_sources fetch pool failed: %s", type(exc).__name__)
        fetched = {c["url"]: (None, type(exc).__name__) for c in to_fetch}

    results: list[Json] = []
    skipped_budget = 0
    for candidate, action in plan:
        if action == "index":
            results.append(_result(candidate["url"], "INDEX_PAGE"))
        elif action == "budget":
            skipped_budget += 1
        else:
            html, error = fetched[candidate["url"]]
            try:
                results.append(_verify_one(candidate, html, error, today))
            except Exception as exc:  # noqa: BLE001 - satu halaman rusak tidak boleh menjatuhkan batch
                log.warning("verify_sources could not parse a page: %s", type(exc).__name__)
                results.append(_result(candidate["url"], "NOT_FOUND"))

    counts = {s: sum(1 for r in results if r["date_status"] == s)
              for s in ("IN_WINDOW", "STALE", "FUTURE", "NOT_FOUND", "FETCH_FAILED", "INDEX_PAGE")}
    stats = {"requested": len(ordered), "pages_fetched": len(to_fetch), "skipped_budget": skipped_budget,
             **{k.lower(): v for k, v in counts.items()}}
    return {"results": results, "stats": stats}
