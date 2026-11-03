"""Graph ``scan_external`` — pipeline berita terverifikasi (DEC-015, DEC-032, ``docs/NEWS_PIPELINE.md`` 3.1-3.10; N-06, N-17).

    build_watchlist ─(bagian kosong, N-16A)→ empty_partition (OK, tanpa pencarian)
    build_watchlist → plan_queries → ┬ search_brave ─┬→ join_sources   (Brave ‖ sonar, DEC-022, DEC-026, DEC-032)
                                     └ search_sonar ─┘
        ─(semua sumber gagal)→ sources_unavailable (ERROR)
        → screen_candidates → verify_dates ─(0 bukti dalam jendela)→ no_recent_evidence (OK, 0 sinyal)
        → extract_signals → filter_signals → save_signals

Prinsip (G-02, G-03, G-06, G-08, D-36):
- Sumber hanya mengusulkan KANDIDAT. Tanggal terbit, lokasi/negara, angka tarif, dan status ``VERIFIED`` diputuskan KODE
  (``tools/verify.py``, ``filter_signals``). Sejak N-09 (DEC-018, cara FAME) LLM hanya menjawab ya/tidak per bukti atas pertanyaan
  yang disusun kode (``tools/evidence_judge.py``); kode memeriksa jawaban dan menyusun sinyal dari target kandidat.
- Query dan pertanyaan sonar dibangun kode dari watchlist (TL-06 / TL-22); tidak ada nama tempat di kode atau prompt.
- Konten luar selalu di dalam ``<external_content>`` dan tag penutup palsu di dalamnya dinetralkan (G-03).
- URL sinyal berasal dari bukti (G-02), bukan dari teks LLM. Sinyal LIVE disimpan hanya bila ``VERIFIED`` (Fase 1).
- Satu jalur untuk tombol dan scheduler (DEC-006) dan untuk DEMO/LIVE: tidak ada percabangan ``trigger`` atau ``mode``.
- Anggaran G-05: request Brave/sonar dan satu batch verifikasi dihitung lewat ``extra_tool_calls``.
- N-17 (DEC-032): Currents dan GDELT dihapus. Satu node ``search_brave`` (nama tool trace tetap ``search_brave_news``) menjalankan query
  kejadian per daerah DAN query kebijakan/logistik per negara asal (``plan["brave"]`` lalu ``plan["brave_country"]``) dengan konkurensi
  klien, lalu query ulang nama-saja. ``search_sonar`` berjalan BERSAMAAN; keduanya menulis key sendiri (``hits_*``, ``skipped_*``) dan
  ``join_sources`` menggabungkannya ke ``raw_hits`` / ``skipped_targets`` dalam urutan tetap Brave lalu sonar. Hit Brave membawa ``topic``
  (``event`` | ``policy`` | ``logistics``) dari item rencana; logika kebijakan di ``screen`` memakai ``topic``, bukan nama mesin.
- N-16A (DEC-029): ``scan_partition`` ``{"index", "count"}`` (payload D.2 v1.9) membatasi run ke bagian ke-``index`` dari watchlist penuh
  (``tools.news_queries.partition_watchlist``); semua langkah sesudahnya bekerja atas bagian itu. Bawaan ``{0, 1}`` = watchlist penuh.
- N-13 (DEC-026): bila tidak ada hasil Brave yang menyebut sebuah lokasi, ``search_brave`` mengulang dengan query nama saja
  (``"<nama>"``, maks ``BRAVE_MAX_FALLBACKS``, di dalam ``MAX_BRAVE_REQUESTS``).
"""

from __future__ import annotations

import copy
import re
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Annotated, Any, Callable, Collection, Literal, Mapping, Sequence, get_args

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from graphs import common as graph_common
from graphs.common import LLM_KEY, RunOutcome, RunState, get_llm, get_tools, new_run_state, run_guarded, traced_node
from prompts import load_prompt
from tools import evidence_judge as judge
from tools import news_config as cfg
from tools import verify
from tools.client import Json, ToolClient, ToolError
from tools.evidence_judge import (  # noqa: F401 - dipindah ke tools/evidence_judge.py (N-09); nama lama tetap bisa diimpor dari sini
    RiskType, core_name, country_names, mentioned_locations, mentions_country, mentions_location, mentions_place,
    sanitize_external,
)
from tools.news import BraveNewsClient, get_news_clients, registered_domain
from tools.news_queries import (
    brave_fallback_query, build_news_plan, origin_countries, partition_overflow, partition_watchlist,
    smallest_fitting_partition_count,
)
from tools.sonar_policy import PolicyRun, ask_policy, build_policy_questions, candidates_from_sonar

TASK = "scan_external"
BRAVE_KEY, SONAR_KEY = "brave", "sonar"
SESSION_KEY, TODAY_KEY = "page_session", "today"  # sesi HTTP verifikasi halaman dan tanggal "hari ini" (tes)
CLOCK_KEY = "clock"  # jam monotonik untuk semua cek jatah waktu per sumber (tes menyuntik jam tiruan)

SOURCES_UNAVAILABLE = "Live sources unavailable — try again or use demo scenario"
WATCHLIST_TIERS = ("1", "2")
MAX_FUTURE_DAYS = cfg.MAX_FUTURE_DAYS  # effective_date maksimal ke depan (3.8 butir 3)
DATE_SLACK_DAYS = 3  # effective_date tidak boleh lebih awal dari terbit terawal - 3 hari
ENGINES = ("BRAVE", "SONAR")  # N-17 (DEC-032): urutan ``sources_used``
EXTRACT_READ_TIMEOUT_S = cfg.EXTRACT_READ_TIMEOUT_S  # ekstraksi normal ~10 s; batas bawaan 60 s x 2 percobaan menghabiskan jatah 120 s

RISK_CATEGORIES = list(get_args(RiskType))
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- bentuk sinyal kandidat


class ExtractedSignal(BaseModel):
    """Bentuk sinyal kandidat yang dibaca ``judge_signal``. Sejak N-09 (DEC-018) sinyal DISUSUN KODE dari jawaban "ya" per bukti
    (``tools/evidence_judge.signals_from_judgements``); LLM tidak lagi menulis sinyal, lokasi/negara, atau URL."""

    risk_type: RiskType
    scope: Literal["LOCATION", "COUNTRY"]
    country: str = Field(description="ISO country code")
    kabupaten: str | None = Field(default=None, description="kabupaten/city name, LOCATION scope only")
    kecamatan: list[str] = Field(default_factory=list, description="sub-district names")
    severity: int | None = Field(default=None, ge=1, le=5, description="1-5 for LOCATION; None for COUNTRY (DEC-019)")
    effective_date: str = Field(description="YYYY-MM-DD")
    tariff_pp: float | None = Field(default=None, description="tariff events only; from a grounded claim")
    category: str | None = Field(default=None, description="affected goods, country-scope signals only (mapped to a data category by code)")
    downtime_days: int | None = Field(default=None, description="whole days the source says the disruption lasts; None if not stated (A-18)")
    summary: str
    evidence_ids: list[str] = Field(default_factory=list, description="ids of the evidence items, e.g. E1")


def _merge_status(current: dict[str, str] | None, update: dict[str, str] | None) -> dict[str, str]:
    """Reducer ``source_status``: node paralel (Brave, sonar) menulis kunci berbeda dalam satu superstep; digabung."""
    return {**(current or {}), **(update or {})}


class ScanExternalState(RunState, total=False):
    scan_partition: Json  # N-16A (DEC-029): {"index", "count"} dari payload D.2; tanpa itu {"index": 0, "count": 1}
    watchlist: Json  # bagian ke-index dari watchlist penuh (watchlist penuh bila count = 1)
    data_categories: list[str]  # A-18: kategori part/komponen dari TL-06 v1.11; tujuan pemetaan ``category`` sinyal negara
    plan: Json
    sonar_questions: list[Json]
    skipped_targets: list[Json]
    raw_hits: list[Json]
    hits_brave: list[Json]  # N-10: hasil node paralel; digabung ke raw_hits oleh join_sources (Brave, lalu sonar)
    hits_sonar: list[Json]  # N-13 (DEC-026): sonar ikut paralel; kandidat dari sitasi sonar
    skipped_brave: list[Json]
    skipped_sonar: list[Json]  # N-17: pertanyaan sonar yang tidak sempat mulai dalam jatah (``SONAR_MAX_PARALLEL`` diturunkan)
    source_status: Annotated[dict[str, str], _merge_status]  # BRAVE / SONAR -> OK | UNAVAILABLE | DISABLED | NOT_USED
    sonar_claims: list[Json]
    sources_used: list[str]
    candidates: list[Json]
    screen_counts: Json
    verified: dict[str, Json]
    evidence_stats: Json
    pages_checked: int
    evidence: list[Json]
    claims_for_llm: list[Json]  # klaim sonar yang lolos verifikasi; dipakai KODE (tarif, tanggal berlaku), tidak dikirim ke LLM
    judgements: list[Json]  # satu hasil per bukti setelah pemeriksaan kode (N-09)
    candidate_signals: list[Json]
    signals_to_save: list[Json]
    unverified: list[Json]
    local_dropped: list[Json]
    signals_saved: int
    signals_dropped: int
    signals_matched: int
    cases: list[str]
    error: str
    decision: str


# --------------------------------------------------------------------------- pembantu murni


def _clean_url(url: str) -> str:
    return url.split("#", 1)[0].strip()


def _fmt_dates(counter: Counter) -> str:
    return ", ".join(f"{k}: {n}" for k, n in sorted(counter.items())) or "-"


# --------------------------------------------------------------------------- kategori sinyal negara (A-18, DEC-037)


def _category_key(text: Any) -> str:
    """Bentuk pembanding kategori: huruf kecil (``casefold``), spasi dirapatkan, tanpa akhiran ``s`` (tunggal/jamak sederhana)."""
    key = " ".join(str(text or "").casefold().split())
    return key[:-1] if key.endswith("s") else key


def data_categories(network: Mapping[str, Any]) -> list[str]:
    """Kategori yang ada di data (TL-06 v1.11): ``category`` node part, lalu ``component_category`` edge ``tier2_to_tier1``. Unik menurut
    ``_category_key``, ejaan kemunculan pertama; kosong bila backend belum mengirim kategori."""
    found: dict[str, str] = {}
    candidates = [n.get("category") for n in network.get("nodes") or [] if isinstance(n, Mapping) and n.get("type") == "part"]
    candidates += [e.get("component_category") for e in network.get("edges") or []
                   if isinstance(e, Mapping) and e.get("type") == "tier2_to_tier1"]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            found.setdefault(_category_key(value), value.strip())
    return list(found.values())


def map_category(written: Any, categories: Sequence[str]) -> str | None:
    """``category`` sinyal negara (teks bebas dari bukti) -> kategori data yang setara (ejaan data), atau ``None`` bila tidak ada yang cocok
    (server lalu memakai semua site negara itu). Fungsi kode, bukan LLM (G-06): cocok bila sama setelah ``casefold`` dan tunggal/jamak."""
    key = _category_key(written)
    if not key:
        return None
    return next((c for c in categories if _category_key(c) == key), None)


# --------------------------------------------------------------------------- watchlist (3.2)


def _node_label(node: Mapping[str, Any]) -> str:
    return str(node.get("label") or node.get("id") or "").strip()


def _local_names_of(site: Mapping[str, Any]) -> list[str]:
    """``kabupaten_local`` node site (kontrak v1.8, opsional): daftar nama beraksara lokal. String tunggal diterima sebagai satu
    nama; bukan teks/daftar atau string kosong diabaikan. Kode tidak menerjemahkan nama (DEC-015, DEC-025)."""
    raw = site.get("kabupaten_local")
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else []
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def build_watchlist_from_data(network: Mapping[str, Any], heatmap: Mapping[str, Any] | None, home_country: str) -> Json:
    """Watchlist dari data (TL-06 + TL-22): lokasi site tier 1-2, negara, barang per negara, prioritas."""
    nodes = {n.get("id"): n for n in network.get("nodes") or [] if isinstance(n, Mapping)}
    sites = {i: n for i, n in nodes.items() if n.get("type") == "site" and str(n.get("tier")) in WATCHLIST_TIERS
             and n.get("kabupaten") and n.get("country")}
    by_location: dict[tuple[str, str], Json] = {}
    for site_id, n in sites.items():
        loc = by_location.setdefault((str(n["kabupaten"]), str(n["country"]).upper()),
                                     {"kabupaten": str(n["kabupaten"]), "country": str(n["country"]).upper(),
                                      "site_ids": [], "tiers": [], "local_names": []})
        loc["site_ids"].append(site_id)
        for local in _local_names_of(n):  # DEC-025: gabungan unik kabupaten_local semua site di (kabupaten, negara), urutan kemunculan
            if local not in loc["local_names"]:
                loc["local_names"].append(local)
        if str(n["tier"]) not in loc["tiers"]:
            loc["tiers"].append(str(n["tier"]))
    if not by_location:
        raise ValueError("supplier network has no tier 1-2 site with a location; nothing to watch")

    goods: dict[str, list[str]] = {}
    for edge in network.get("edges") or []:
        site = sites.get(edge.get("from"))
        if not site:
            continue
        code = str(site["country"]).upper()
        if edge.get("type") == "supplies" and (part := nodes.get(edge.get("to"))) and part.get("type") == "part":
            label = _node_label(part)
        elif edge.get("type") == "tier2_to_tier1":
            label = str(edge.get("component") or "").strip()
        else:
            continue
        if label and label not in goods.setdefault(code, []):
            goods[code].append(label)

    share: dict[str, float] = {}
    for row in (heatmap or {}).get("countries") or []:
        if isinstance(row, Mapping) and row.get("country"):
            share[str(row["country"]).upper()] = float(row.get("share") or 0.0)
    codes = list(dict.fromkeys(loc["country"] for loc in by_location.values()))
    site_count = {c: sum(len(l["site_ids"]) for l in by_location.values() if l["country"] == c) for c in codes}
    priority = {c: (share.get(c) if heatmap is not None else float(site_count[c])) or 0.0 for c in codes}

    locations = []
    for loc in by_location.values():
        tier1 = sum(1 for sid in loc["site_ids"] if str(sites[sid]["tier"]) == "1")
        locations.append({**loc, "priority": round(priority[loc["country"]] + tier1 / 1000.0, 4)})
    locations.sort(key=lambda l: -l["priority"])
    countries = [{"code": c, "name": cfg.country_name(c), "goods": goods.get(c, []),
                  "po_exposure_share": priority[c] if heatmap is not None else None}
                 for c in sorted(codes, key=lambda c: -priority[c])]
    return {"home_country": home_country, "locations": locations, "countries": countries}


# --------------------------------------------------------------------------- node: watchlist dan rencana


def parse_scan_partition(value: Any) -> tuple[int, int]:
    """``scan_partition`` payload D.2 (v1.9, DEC-029) -> ``(index, count)``. ``None`` (tidak dikirim) = ``(0, 1)``. ``ValueError`` bila
    bukan objek ``{"index": int >= 0, "count": int 1..MAX_SCAN_PARTITIONS}`` atau ``index >= count`` (``bool`` dan ``float`` ditolak)."""
    if value is None:
        return 0, 1
    if not isinstance(value, Mapping):
        raise ValueError("scan_partition must be an object {\"index\", \"count\"}")
    numbers = {}
    for name in ("index", "count"):
        number = value.get(name)
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"scan_partition.{name} must be an integer")
        numbers[name] = number
    index, count = numbers["index"], numbers["count"]
    if not 1 <= count <= cfg.MAX_SCAN_PARTITIONS:
        raise ValueError(f"scan_partition.count must be 1..{cfg.MAX_SCAN_PARTITIONS}, got {count}")
    if not 0 <= index < count:
        raise ValueError(f"scan_partition.index must be 0..{count - 1}, got {index}")
    return index, count


def _partition_of(state: Mapping[str, Any]) -> tuple[int, int]:
    """Bagian yang dikerjakan run ini; state tanpa ``scan_partition`` (tes graph langsung) = bagian tunggal."""
    return parse_scan_partition(state.get("scan_partition"))


def is_empty_watchlist(watchlist: Mapping[str, Any]) -> bool:
    return not (watchlist.get("locations") or watchlist.get("countries"))


def size_warning(part: Mapping[str, Any], full: Mapping[str, Any], count: int) -> str | None:
    """Peringatan bila bagian melewati BATAS KERAS (daerah > ``MAX_BRAVE_EVENT_REQUESTS`` atau negara asal > ``PARTITION_SAFE_ORIGINS``; DEC-029,
    N-17),
    dengan saran jumlah bagian yang membuat tiap bagian muat di ukuran target (``PARTITION_SAFE_LOCATIONS``); ``None`` bila tidak ada."""
    overflow = partition_overflow(part)
    if not overflow:
        return None
    better = smallest_fitting_partition_count(full, count)
    if better is not None:
        hint = f"consider SCAN_PARTITIONS={better}"
    elif count >= cfg.MAX_SCAN_PARTITIONS:
        hint = f"SCAN_PARTITIONS is already at its maximum ({cfg.MAX_SCAN_PARTITIONS})"
    else:
        hint = f"no SCAN_PARTITIONS up to {cfg.MAX_SCAN_PARTITIONS} makes every part fit"
    return f"partition exceeds safe size ({'; '.join(overflow)}); {hint}"


@traced_node(step="build_watchlist", tool="get_supplier_network")
def build_watchlist(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    tools = get_tools(config)
    index, count = _partition_of(state)
    network = tools.get_supplier_network()
    heatmap: Json | None
    try:
        heatmap = tools.get_country_heatmap()
        note = "priority from PO exposure (TL-22)"
    except ToolError as exc:
        heatmap, note = None, f"TL-22 unavailable ({exc.code}) → priority from site count"
    full = build_watchlist_from_data(network, heatmap, cfg.NEWS_HOME_COUNTRY)  # watchlist penuh; "nothing to watch" tetap berlaku
    watchlist = partition_watchlist(full, index, count)  # N-16A: hanya bagian ke-index; count = 1 -> watchlist penuh
    notes = [note]
    if count > 1:
        notes.append(f"partition {index + 1}/{count}: {len(watchlist['locations'])} locations, "
                     f"{len(origin_countries(watchlist))} origin countries")
    if warning := size_warning(watchlist, full, count):
        notes.append(warning)
    return {
        "watchlist": watchlist, "data_categories": data_categories(network), "decision": "; ".join(notes),
        "output_summary": f"{len(watchlist['locations'])} locations in {len(watchlist['countries'])} countries",
    }


@traced_node(step="empty_partition")
def empty_partition(state: ScanExternalState) -> dict[str, Any]:
    """N-16A: bagian tanpa daerah dan tanpa negara asal (``count`` lebih besar dari isi watchlist). Run selesai ``OK`` tanpa pencarian."""
    index, count = _partition_of(state)
    return {"output_summary": f"partition {index + 1}/{count} is empty",
            "decision": f"partition {index + 1}/{count} has no locations or origin countries → no news search, status OK"}


@traced_node(step="plan_queries")
def plan_queries(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    today = _today(config)
    plan = build_news_plan(state["watchlist"], today)
    questions, skipped_sonar = build_policy_questions(state["watchlist"], today)
    skipped = [*plan["skipped"], *({"source": "SONAR", **s} for s in skipped_sonar)]
    budget_skipped = Counter(s["source"] for s in skipped if s["reason"] == "BUDGET")
    notes = [f"{n} {source.title()} targets not searched this run (budget)" for source, n in sorted(budget_skipped.items())]
    return {
        "plan": plan, "sonar_questions": questions, "skipped_targets": skipped,
        "output_summary": (f"{len(plan['brave'])} Brave event, {len(plan['brave_country'])} Brave country, "
                           f"{len(questions)} sonar queries"),
        "decision": "; ".join(notes) or "all watchlist targets fit the budget",
    }


def _today(config: Mapping[str, Any]) -> date:
    return (config.get("configurable") or {}).get(TODAY_KEY) or date.today()


def _clock(config: Mapping[str, Any]) -> Callable[[], float]:
    return (config.get("configurable") or {}).get(CLOCK_KEY) or time.monotonic


def _target_name(target: Mapping[str, Any]) -> str:
    return str(target.get("country") or "").upper() if target.get("scope") == "COUNTRY" else str(target.get("kabupaten"))


# --------------------------------------------------------------------------- node: pencarian


def _hit(item: Mapping[str, Any], engine: str, target: Mapping[str, Any], topic: str = "event") -> Json:
    """``topic`` (N-09, N-17): ``event`` (Brave, query kejadian per daerah), ``policy`` / ``logistics`` (Brave, query negara asal; dari
    item rencana), ``policy`` (sonar)."""
    hint = item.get("hint_date")
    return {"url": _clean_url(item["url"]), "title": item.get("title") or "", "snippet": item.get("snippet") or "",
            "engine": engine, "hint_dates": [hint] if hint else [], "target": dict(target), "claims": [], "topic": topic,
            "query_target": dict(target)}


def _status(oks: int, failures: list[str], disabled: bool, planned: int) -> str:
    if planned == 0:
        return "NOT_USED"
    if disabled:
        return "DISABLED"
    return "OK" if oks else "UNAVAILABLE"


def _brave_client(config: Mapping[str, Any]) -> BraveNewsClient:
    return (config.get("configurable") or {}).get(BRAVE_KEY) or get_news_clients()


def _query_label(query: Mapping[str, Any]) -> str:
    """Nama target untuk ``skipped_targets``: kabupaten (kejadian) atau ``"<ISO2>:<jenis>"`` (query negara; bentuk sama dengan ``BUDGET`` di
    rencana)."""
    target = query["target"]
    if target.get("scope") == "COUNTRY":
        return f"{str(target.get('country') or '').upper()}:{query.get('kind') or query.get('topic') or ''}".rstrip(":")
    return str(target.get("kabupaten"))


def _run_ordered(items: Sequence[Any], fn: Callable[[Any], Json], concurrency: Callable[[], int],
                 stop_when: Callable[[Json], bool]) -> list[Json | None]:
    """Jalankan ``fn(item)`` untuk setiap item dan kembalikan hasilnya DALAM URUTAN ``items`` (bukan urutan selesai; ``None`` = tidak
    dijalankan). N-17 (DEC-032): selama ``concurrency()`` <= 1 (mode aman Brave, atau belum ada respons yang menunjukkan batas laju) item
    dijalankan satu per satu menurut urutan rencana, jadi bila jatah waktu habis yang tertinggal adalah item berprioritas terendah. Begitu
    ``concurrency()`` > 1 sisanya dibagi ke ``concurrency()`` thread. ``stop_when(hasil)`` benar -> item yang belum dimulai dilewati."""
    results: list[Json | None] = [None] * len(items)
    stop = threading.Event()

    def run_one(i: int) -> None:
        if stop.is_set():
            return
        results[i] = fn(items[i])
        if stop_when(results[i]):  # type: ignore[arg-type]
            stop.set()

    i = 0
    while i < len(items) and not stop.is_set() and concurrency() <= 1:
        run_one(i)
        i += 1
    if i < len(items) and not stop.is_set():
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency(), len(items) - i))) as pool:
            list(pool.map(run_one, range(i, len(items))))  # list(): galat thread muncul di sini, bukan hilang
    return results


def _brave_failed(result: Json) -> bool:
    """Hasil yang menghentikan item berikutnya: kegagalan sumber. ``TIME_BUDGET`` tidak (item lain dicatat ``TIME_BUDGET`` sendiri)."""
    return result["status"] not in ("OK", "TIME_BUDGET")


def _watchlist_location(watchlist: Mapping[str, Any], target: Mapping[str, Any]) -> Json:
    """Entri watchlist (dengan ``local_names``) untuk target ``LOCATION``; tanpa entri: hanya nama dari target."""
    key = (str(target.get("kabupaten")), str(target.get("country") or "").upper())
    found = next((loc for loc in watchlist.get("locations") or []
                  if (str(loc.get("kabupaten")), str(loc.get("country") or "").upper()) == key), None)
    return dict(found) if found else {"kabupaten": target.get("kabupaten"), "country": target.get("country")}


def _names_location(location: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    return any(mentions_location(location, str(i.get("title") or ""), str(i.get("snippet") or ""))
               for i in result.get("items") or [])


def locations_without_named_result(queries: list[Json], results: list[Json | None], watchlist: Mapping[str, Any]) -> list[Json]:
    """N-13 (DEC-026): target ``LOCATION`` yang querinya dijawab ``OK`` tetapi tidak satu pun hasilnya menyebut lokasi di judul atau
    snippet (``mentions_location``: nama Latin atau ``local_names``, fungsi yang sama dengan ``screen``). ``results[i]`` adalah
    hasil ``queries[i]`` (``None`` / bukan ``OK`` = diabaikan). Urutan = urutan query (prioritas watchlist). Fungsi murni. Query
    negara (``COUNTRY``) tidak pernah diulang (N-17)."""
    missing: list[Json] = []
    for query, result in zip(queries, results):
        target = query.get("target") or {}
        if target.get("scope") != "LOCATION" or (result or {}).get("status") != "OK":
            continue
        if not _names_location(_watchlist_location(watchlist, target), result):
            missing.append(target)
    return missing


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _brave_name_only_retries(brave: Any, queries: list[Json], answered: list[tuple[Json, Json]], watchlist: Mapping[str, Any],
                             admit: Callable[[float, bool], bool], used: int) -> tuple[list[Json], str | None]:
    """N-13 (DEC-026, ``docs/N-13_PERBAIKAN_BRAVE_CURRENTS_TANGGAL.md`` 2.1): satu query ulang ``"<nama>"`` per lokasi yang tidak
    disebut satu pun hasilnya, menurut urutan prioritas, maks ``BRAVE_MAX_FALLBACKS`` dan hanya memakai sisa
    ``MAX_BRAVE_REQUESTS - used`` (``used`` = request yang sudah dipakai node ini, minimal jumlah query rencana), dengan aturan jatah
    waktu yang sama dengan query rencana (``admit``). Query ulang memakai ``country``, ``search_lang``, ``start``, ``end``, dan ``target``
    query pertama. N-17: query ulang berjalan dengan konkurensi klien. Mengembalikan (hit tambahan, catatan trace)."""
    missing = locations_without_named_result([q for q, _ in answered], [r for _, r in answered], watchlist)
    if not missing:
        return [], None
    first = {_target_key(q["target"]): q for q in queries}
    allowed = min(max(0, cfg.MAX_BRAVE_REQUESTS - used), cfg.BRAVE_MAX_FALLBACKS)
    todo, over_budget = missing[:allowed], [str(t.get("kabupaten")) for t in missing[allowed:]]

    def call(target: Json) -> Json:
        query = first[_target_key(target)]
        location = _watchlist_location(watchlist, target)
        return brave.search(brave_fallback_query(location, str(target.get("country") or "")), country=query["country"],
                            search_lang=query["search_lang"], start=query["start"], end=query["end"], target=query["target"],
                            admit=admit)

    results = _run_ordered(todo, call, lambda: brave.concurrency, _brave_failed) if todo else []
    hits: list[Json] = []
    retried: list[str] = []
    still: list[str] = []
    out_of_time: list[str] = []
    stopped: str | None = None
    for target, result in zip(todo, results):
        if result is None:
            continue  # tidak dimulai karena query ulang sebelumnya gagal
        name = str(target.get("kabupaten"))
        if result["status"] == "TIME_BUDGET":
            out_of_time.append(name)
            continue
        retried.append(name)
        if result["status"] != "OK":
            still.append(name)
            stopped = stopped or result["status"]
            continue
        query = first[_target_key(target)]
        hits.extend(_hit(item, "BRAVE", query["target"], query.get("topic") or "event") for item in result["items"])
        if not _names_location(_watchlist_location(watchlist, target), result):
            still.append(name)
    note = f"BRAVE: no result named {_plural(len(missing), 'location')} → name-only retry for {len(retried)}"
    if retried:
        note += f" ({', '.join(retried)}); still not named: {', '.join(still) or '-'}"
    if stopped:
        note += f"; retry stopped ({stopped})"
    if over_budget:
        note += f"; not retried (request budget): {', '.join(over_budget)}"
    if out_of_time:
        note += f"; not retried (time budget): {', '.join(out_of_time)}"
    return hits, note


# keep_decision=False: node paralel; ``decision`` hanya ke trace (satu key state tidak boleh ditulis dua node)
@traced_node(step="search_brave", tool="search_brave_news", keep_decision=False)
def search_brave(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    """N-17 (DEC-032): query kejadian per daerah (``plan["brave"]``) lalu query kebijakan/logistik per negara asal (``plan["brave_country"]``)
    dengan konkurensi klien, lalu query ulang nama-saja. Hit diurutkan menurut urutan rencana, bukan urutan selesai."""
    brave = _brave_client(config)
    plan = state["plan"]
    queries: list[Json] = [*plan["brave"], *plan["brave_country"]]
    watchlist = state.get("watchlist") or {}
    clock = _clock(config)
    before, started = brave.requests_made, clock()

    def admit(wait: float, retry: bool) -> bool:
        """Request baru hanya dimulai bila ``waktu_node + tunggu + timeout_baca <= BRAVE_TIME_BUDGET_S`` (DEC-017); ulangan 429 juga hanya
        bila jatah request ``MAX_BRAVE_REQUESTS`` belum habis (N-17 4.3)."""
        if clock() - started + wait + brave.read_timeout > cfg.BRAVE_TIME_BUDGET_S:
            return False
        return not (retry and brave.requests_made - before >= cfg.MAX_BRAVE_REQUESTS)

    def call(q: Json) -> Json:
        return brave.search(q["query"], country=q["country"], search_lang=q["search_lang"], start=q["start"], end=q["end"],
                            target=q["target"], admit=admit)

    results = (_run_ordered(queries, call, lambda: brave.concurrency, _brave_failed) if brave.enabled
               else [None] * len(queries))
    hits: list[Json] = []
    answered: list[tuple[Json, Json]] = []
    not_run: list[Json] = []
    oks, failure = 0, None
    for q, result in zip(queries, results):
        if result is None:
            continue  # tidak dimulai karena sumber gagal lebih dulu (jangan menembak sumber yang membatasi)
        if result["status"] == "TIME_BUDGET":
            not_run.append(q)
        elif result["status"] != "OK":
            failure = failure or result["status"]
        else:
            oks += 1
            answered.append((q, result))
            hits.extend(_hit(item, "BRAVE", q["target"], q.get("topic") or "event") for item in result["items"])
    followup_note = None
    if failure is None and answered:
        extra_hits, followup_note = _brave_name_only_retries(
            brave, queries, answered, watchlist, admit, max(len(queries), brave.requests_made - before))
        hits.extend(extra_hits)

    status = _status(oks, [failure] if failure else [], not brave.enabled, len(queries))
    update: dict[str, Any] = {
        "hits_brave": hits, "source_status": {**(state.get("source_status") or {}), "BRAVE": status},
        "extra_tool_calls": brave.requests_made - before,
        "output_summary": f"{oks} of {len(queries)} queries answered; {len(hits)} results",
    }
    notes = []
    if status == "DISABLED":
        notes.append("BRAVE_UNAVAILABLE (DISABLED: no API key)")
    elif failure:
        notes.append(f"BRAVE_UNAVAILABLE ({failure}) after {oks} of {len(queries)} queries → continue with other sources")
    if not_run:
        notes.append(f"BRAVE: {len(not_run)} of {len(queries)} queries not run (time budget)")
        update["skipped_brave"] = [{"source": "BRAVE", "target": _query_label(q), "reason": "TIME_BUDGET"} for q in not_run]
    if followup_note:
        notes.append(followup_note)
    if notes:
        update["decision"] = "; ".join(notes)
    return update


# keep_decision=False: node ini berjalan bersamaan dengan ``search_brave``; ``decision`` hanya ke trace (satu key state tidak boleh ditulis dua
# node). N-13 (DEC-026): sonar tidak bergantung pada hasil Brave (pertanyaannya dibuat ``plan_queries``), jadi ia ikut paralel; ia hanya
# menulis key miliknya (``hits_sonar``, ``sonar_claims``, ``skipped_sonar``, ``source_status["SONAR"]``) dan ``join_sources`` menggabungkannya.
@traced_node(step="search_sonar", tool="search_web_sonar", keep_decision=False)
def search_sonar(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    questions = state.get("sonar_questions") or []
    if not questions:
        return {"hits_sonar": [], "source_status": {"SONAR": "NOT_USED"}, "decision": "no policy questions planned"}

    sonar = (config.get("configurable") or {}).get(SONAR_KEY)
    if sonar is None:
        try:
            from model.load import get_sonar

            sonar = get_sonar(purpose="scan")  # timeout pendek, tanpa retry SDK (DEC-017)
        except Exception as exc:  # noqa: BLE001 - konfigurasi model hilang: sumber lain tetap jalan
            return {"hits_sonar": [], "source_status": {"SONAR": "UNAVAILABLE"},
                    "decision": f"SONAR_UNAVAILABLE ({type(exc).__name__})"}

    today, run, clock = _today(config), PolicyRun(), _clock(config)
    started = clock()

    def retry_ok() -> bool:  # ulang karena parameter ditolak hanya bila satu request lagi masih muat di jatah
        return clock() - started + cfg.SONAR_SCAN_TIMEOUT_S <= cfg.SONAR_TIME_BUDGET_S

    def ask(q: Json) -> Json | None:
        # N-17: ``SONAR_MAX_PARALLEL`` < jumlah pertanyaan -> pertanyaan yang baru sempat mulai setelah jatah habis dilewati (TIME_BUDGET)
        if not retry_ok():
            return None
        return ask_policy(q, sonar, today, run, retry_ok=retry_ok)

    with ThreadPoolExecutor(max_workers=max(1, min(len(questions), cfg.SONAR_MAX_PARALLEL))) as pool:
        asked = list(pool.map(ask, questions))  # urutan = urutan pertanyaan
    not_started = [q for q, r in zip(questions, asked) if r is None]
    results = [r for r in asked if r is not None]
    oks = [r for r in results if r["status"] == "OK"]
    claims: list[Json] = []
    hits: list[Json] = []
    for r in oks:
        own = [dict(claim) for claim in r["claims"]]  # hasil cache bersama antar run: jangan dimutasi
        for claim in own:
            claim["claim_id"] = f"C{len(claims) + 1}"
            claims.append(claim)
        for cand in candidates_from_sonar({**r, "claims": own}):
            hits.append({"url": _clean_url(cand["url"]), "title": cand["title"], "snippet": cand["snippet"],
                         "engine": "SONAR", "hint_dates": cand["hint_dates"], "target": cand["target"],
                         "claims": cand["claims"], "topic": "policy", "query_target": dict(cand["target"])})
    notes = []
    if len(oks) < len(results):
        notes.append(f"SONAR_UNAVAILABLE for {len(results) - len(oks)} of {len(results)} questions")
    if not_started:
        notes.append(f"SONAR: {len(not_started)} of {len(questions)} questions not run (time budget)")
    if run.rejected:
        notes.append("SONAR_PARAM_REJECTED:" + ",".join(sorted(run.rejected)))
    if oks and not claims:
        notes.append("no trade-measure claims returned")
    update: dict[str, Any] = {
        "hits_sonar": hits, "sonar_claims": claims,
        "source_status": {"SONAR": "OK" if oks else "UNAVAILABLE"},
        "extra_tool_calls": run.requests,
        "output_summary": f"{len(oks)} of {len(results)} questions answered; {len(claims)} claims",
        "decision": "; ".join(notes) or None,
    }
    if not_started:
        update["skipped_sonar"] = [{"source": "SONAR", "target": str(q["origin_country"]), "reason": "TIME_BUDGET"} for q in not_started]
    return update


def join_parallel_sources(state: Mapping[str, Any]) -> Json:
    """``raw_hits`` dan ``skipped_targets`` setelah Brave dan sonar selesai: urutan tetap Brave lalu sonar (urutan di dalam tiap sumber
    tidak berubah), apa pun yang selesai lebih dulu (N-10 butir 2.1.5, N-13 / DEC-026, N-17). Fungsi murni."""
    return {
        "raw_hits": [*(state.get("raw_hits") or []), *(state.get("hits_brave") or []), *(state.get("hits_sonar") or [])],
        "skipped_targets": [*(state.get("skipped_targets") or []), *(state.get("skipped_brave") or []),
                            *(state.get("skipped_sonar") or [])],
    }


@traced_node(step="join_sources")
def join_sources(state: ScanExternalState) -> dict[str, Any]:
    """N-13 (DEC-026): titik temu sumber paralel (tanpa tool, tanpa jaringan) sebelum ``screen_candidates``. N-17: Brave dan sonar."""
    joined = join_parallel_sources(state)
    counts = {name: len(state.get(key) or []) for name, key in (("Brave", "hits_brave"), ("sonar", "hits_sonar"))}
    return {**joined, "output_summary": f"{len(joined['raw_hits'])} results ({', '.join(f'{k} {v}' for k, v in counts.items())})"}


@traced_node(step="sources_unavailable")
def sources_unavailable(state: ScanExternalState) -> dict[str, Any]:
    status = state.get("source_status") or {}
    return {
        "error": SOURCES_UNAVAILABLE, "trace_status": "ERROR", "output_summary": SOURCES_UNAVAILABLE,
        "decision": "all sources failed (" + ", ".join(f"{k}={v}" for k, v in sorted(status.items())) + ") → status ERROR",
    }


# --------------------------------------------------------------------------- node: saring dan verifikasi


def pick_country(m: Mapping[str, Any], text: str, watchlist: Mapping[str, Any]) -> str | None:
    """Negara target kandidat kebijakan/logistik (N-09b, DEC-019). Urutan: (1) ``origin_country`` klaim sonar; (2) negara target
    query sumber (query negara Brave/sonar) bila teks menyebutnya; (3) negara watchlist pertama yang disebut teks. Untuk topik ``policy`` negara
    rumah tidak pernah dipilih (tarif dari luar terhadap barang negara rumah bukan sinyal, DEC-017): pertanyaannya "goods made in
    <negara asal>". Topik ``logistics`` boleh bertarget negara rumah."""
    home = str(watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY).upper()
    policy = "policy" in (m.get("topics") or [])

    def allowed(code: str) -> bool:
        return not (policy and code.upper() == home)

    claimed = next((str(c["origin_country"]).upper() for c in m["claims"] if c.get("origin_country")), None)
    if claimed and allowed(claimed):
        return claimed
    for code in m.get("query_countries") or []:
        if allowed(code) and mentions_country(code, text):
            return code
    return next((str(c["code"]).upper() for c in watchlist["countries"]
                 if allowed(str(c["code"])) and mentions_country(c["code"], text)), None)


def screen(raw_hits: list[Json], watchlist: Mapping[str, Any]) -> tuple[list[Json], Json]:
    """Gabung per URL, buang halaman indeks, buang yang tidak menyebut target (kode murni, 3.1).

    N-09c (DEC-020): satu URL yang menyebut beberapa lokasi watchlist menghasilkan SATU KANDIDAT PER (URL, LOKASI), maks
    ``MAX_LOCATIONS_PER_ARTICLE`` menurut urutan watchlist (prioritas). Salinan hanya berbeda di ``target``. Tanpa lokasi yang
    disebut: jalur ``COUNTRY`` (satu kandidat) tidak berubah.

    N-10 (DEC-022): kebijakan didahulukan atas lokasi. ``policy_hit`` = URL ditemukan lewat query kebijakan/logistik per negara atau sonar
    (``topic`` ``policy``/``logistics``, atau mesin sonar; N-17 memindahkan pembedaan ini dari nama mesin ke ``topic``) dan bertopik
    ``policy``; ``event_hit`` = URL ditemukan query kejadian per daerah (topik ``event``). Artikel ``policy_hit`` yang menyebut kota
    watchlist menjadi kandidat ``COUNTRY`` (``pick_country``), bukan ``LOCATION`` (yang akan ditanya soal bencana dan terbuang). Bila URL
    yang sama juga ``event_hit``: dua jenis kandidat, 1 ``COUNTRY`` + kandidat ``LOCATION`` per kota (aturan DEC-020). ``pick_country``
    kosong: dengan ``event_hit`` hanya ``LOCATION``, tanpa itu dibuang (``no_target``). Topik ``logistics`` dan ``event`` tidak berubah."""
    merged: dict[str, Json] = {}
    for hit in raw_hits:
        hit = {**hit, "url": _clean_url(hit["url"])}
        m = merged.setdefault(hit["url"], {"url": hit["url"], "engines": [], "topics": [], "title": "", "snippet": "",
                                           "hint_dates": [], "claims": [], "from_policy_source": False,
                                           "query_countries": []})
        if hit["engine"] not in m["engines"]:
            m["engines"].append(hit["engine"])
        if hit.get("topic") and hit["topic"] not in m["topics"]:
            m["topics"].append(hit["topic"])
        m["title"] = max(m["title"], hit["title"], key=len)
        m["snippet"] = max(m["snippet"], hit["snippet"], key=len)
        for h in hit["hint_dates"]:
            if h not in m["hint_dates"]:
                m["hint_dates"].append(h)
        m["claims"].extend(c for c in hit["claims"] if c not in m["claims"])
        policy_source = hit.get("topic") in ("policy", "logistics") or hit["engine"] == "SONAR"  # N-17: topik, bukan nama mesin
        m["from_policy_source"] |= policy_source
        query_target = hit.get("query_target") or {}
        if policy_source and query_target.get("scope") == "COUNTRY" and query_target.get("country"):
            code = str(query_target["country"]).upper()
            if code not in m["query_countries"]:
                m["query_countries"].append(code)

    counts = {"candidates": len(merged), "index_page": 0, "no_target": 0, "multi_location": 0, "location_cap": 0,
              "policy_kept_as_country": 0, "dual_scope": 0}
    kept: list[Json] = []
    for m in merged.values():
        if verify.looks_like_index_page(m["url"]):
            counts["index_page"] += 1
            continue
        text = f"{m['title']} {m['snippet']}"
        named = mentioned_locations(watchlist["locations"], text)
        if named:
            policy_hit = m["from_policy_source"] and "policy" in m["topics"]  # N-10: kebijakan didahulukan atas lokasi
            event_hit = "event" in m["topics"]
            country = pick_country(m, text, watchlist) if policy_hit else None
            if country is not None:
                kept.append({**copy.deepcopy(m), "target": {"scope": "COUNTRY", "kabupaten": None, "country": country},
                             "domain": registered_domain(m["url"])})
                counts["policy_kept_as_country"] += 1
                counts["dual_scope"] += 1 if event_hit else 0
            elif policy_hit and not event_hit:  # kebijakan tanpa negara asal: ditanya soal bencana pun hampir pasti "tidak" (Q-04)
                counts["no_target"] += 1
                continue
            if policy_hit and not event_hit:
                continue  # hanya kandidat COUNTRY; tidak ada kandidat LOCATION
            used = named[:cfg.MAX_LOCATIONS_PER_ARTICLE]
            counts["location_cap"] += len(named) - len(used)
            counts["multi_location"] += 1 if len(used) > 1 else 0
            for loc in used:
                target = {"scope": "LOCATION", "kabupaten": loc["kabupaten"], "country": loc["country"]}
                kept.append({**copy.deepcopy(m), "target": target, "domain": registered_domain(m["url"])})
            continue
        if m["from_policy_source"]:  # kebijakan/logistik per negara: query negara Brave dan sonar
            country = pick_country(m, text, watchlist)
            if country is None:
                counts["no_target"] += 1
                continue
            target = {"scope": "COUNTRY", "kabupaten": None, "country": country}
        else:
            counts["no_target"] += 1
            continue
        kept.append({**m, "target": target, "domain": registered_domain(m["url"])})
    counts["screened"] = len(kept)
    return kept, counts


@traced_node(step="screen_candidates")
def screen_candidates(state: ScanExternalState) -> dict[str, Any]:
    candidates, counts = screen(state.get("raw_hits") or [], state["watchlist"])
    return {
        "candidates": candidates, "screen_counts": counts,
        "output_summary": f"{counts['candidates']} candidates → {counts['screened']} screened",
        "decision": (f"{counts['candidates']} candidates; dropped: index pages {counts['index_page']}, "
                     f"target not mentioned {counts['no_target']}"
                     + (f"; multi-location URLs {counts['multi_location']} (one candidate per location)"
                        if counts["multi_location"] else "")
                     + (f"; location cap: {counts['location_cap']} locations not used (max "
                        f"{cfg.MAX_LOCATIONS_PER_ARTICLE} per URL)" if counts["location_cap"] else "")
                     + (f"; policy articles naming a watchlist city kept as country {counts['policy_kept_as_country']}"
                        if counts["policy_kept_as_country"] else "")
                     + (f"; dual-scope URLs {counts['dual_scope']}" if counts["dual_scope"] else "")),
    }


def apply_verification(claims: list[Json], candidates: list[Json], results: Mapping[str, Json]) -> list[Json]:
    """3.4 langkah 5: klaim dipakai bila >= 1 sumbernya IN_WINDOW; angka/tanggal yang tak tertulis di halaman di-null-kan."""
    out: list[Json] = []
    for claim in claims:
        urls = [u for u in claim["source_urls"] if results.get(u, {}).get("date_status") == "IN_WINDOW"]
        if not urls:
            continue
        rate_ok = date_ok = False
        for cand in candidates:
            for i, c in enumerate(cand["claims"]):
                if c.get("claim_id") != claim["claim_id"]:
                    continue
                grounding = (results.get(cand["url"]) or {}).get("claim_grounding") or []
                g = next((x for x in grounding if x["claim_index"] == i), {})
                rate_ok |= bool(g.get("rate"))
                date_ok |= bool(g.get("effective_date"))
        notes = []
        rate = claim.get("rate_change_pp")
        if rate is not None and not rate_ok:
            rate = None
            notes.append("UNGROUNDED_RATE")
        effective = claim.get("effective_date")
        if effective is not None and not date_ok:
            effective = None
            notes.append("UNGROUNDED_DATE")
        out.append({**claim, "rate_change_pp": rate, "effective_date": effective, "evidence_urls": urls,
                    "rate_grounded": rate is not None, "notes": notes})
    return out


def _target_key(target: Mapping[str, Any]) -> str:
    if target.get("scope") == "COUNTRY":
        return f"COUNTRY:{str(target.get('country') or '').upper()}"
    return f"LOCATION:{target.get('kabupaten')}"


def _target_label(target: Mapping[str, Any]) -> str:
    return str(target.get("country") or "").upper() if target.get("scope") == "COUNTRY" else str(target.get("kabupaten"))


def _order_within_target(group: list[Json], *, policy_first: bool = False) -> list[Json]:
    """Urutan stabil: kandidat yang dikutip klaim sonar, lalu domain terdaftar yang belum muncul (agar >= 2 domain berbeda cepat
    tercapai, syarat ``VERIFIED``), lalu sisanya menurut urutan asli.

    N-11 (DEC-023), hanya bila ``policy_first`` (kelompok negara): setelah yang dikutip sonar, kandidat yang JUDULNYA memuat kata
    kebijakan (``cfg.title_has_policy_word``) didahulukan, lalu yang lain; aturan domain baru dulu berjalan di dalam tiap bagian
    dengan himpunan ``seen`` yang sama. Kandidat tanpa kata kebijakan tidak dibuang, hanya di belakang."""
    cited = [c for c in group if c.get("claims")]
    seen = {c.get("domain") or registered_domain(c["url"]) for c in cited}
    rest = [c for c in group if not c.get("claims")]
    parts = ([c for c in rest if is_policy_title(c)], [c for c in rest if not is_policy_title(c)]) if policy_first else (rest,)
    ordered: list[Json] = list(cited)
    for part in parts:
        fresh: list[Json] = []
        repeated: list[Json] = []
        for c in part:
            domain = c.get("domain") or registered_domain(c["url"])
            if domain in seen:
                repeated.append(c)
            else:
                seen.add(domain)
                fresh.append(c)
        ordered += [*fresh, *repeated]
    return ordered


def is_policy_title(candidate: Mapping[str, Any]) -> bool:
    """N-11: judul kandidat memuat kata kebijakan (Indonesia + Inggris, DEC-023)."""
    return cfg.title_has_policy_word(str(candidate.get("title") or ""))


def policy_title_counts(candidates: list[Json], selected: list[Json], watchlist: Mapping[str, Any]) -> list[tuple[str, int, int]]:
    """N-11: per negara ``(label, dipilih, tersedia)`` = jumlah URL unik bertitel kebijakan yang dipilih / yang ada di kandidat
    negara itu. Urutan negara = watchlist."""
    def per_country(items: list[Json]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for c in items:
            if c["target"].get("scope") == "COUNTRY" and is_policy_title(c):
                out.setdefault(_target_key(c["target"]), set()).add(c["url"])
        return out

    available, checked = per_country(candidates), per_country(selected)
    keys = _ordered_target_keys([_target_key(c["target"]) for c in candidates if c["target"].get("scope") == "COUNTRY"], watchlist)
    return [(key.split(":", 1)[1], len(checked.get(key, ())), len(available.get(key, ()))) for key in dict.fromkeys(keys)]


def _ordered_target_keys(keys: Collection[str], watchlist: Mapping[str, Any]) -> list[str]:
    """Kunci target menurut prioritas watchlist: lokasi (urutan watchlist), lalu negara; target di luar watchlist di belakang."""
    priority = [f"LOCATION:{l['kabupaten']}" for l in watchlist.get("locations") or []]
    priority += [f"COUNTRY:{str(c['code']).upper()}" for c in watchlist.get("countries") or []]
    return [k for k in dict.fromkeys(priority) if k in keys] + [k for k in keys if k not in priority]


def select_for_verification(candidates: list[Json], watchlist: Mapping[str, Any], *, max_pages: int, per_target: int,
                            country_reserve: int) -> tuple[list[Json], list[Json]]:
    """(dipilih, dilewati): bagi jatah halaman verifikasi antar target secara bergiliran (DEC-017). Deterministik.

    Tanpa ini kandidat diperiksa menurut urutan masuk (Brave dulu) dan satu lokasi ramai menghabiskan seluruh jatah, sehingga
    kejadian di lokasi lain dan bukti kebijakan tidak pernah diperiksa.

    N-14 (DEC-027): lokasi didahulukan. Bukti SP-16 16 Okt: negara dan lokasi masing-masing 12 dari 24 halaman, tiap lokasi hanya 2
    halaman dan semua halaman negara dijawab "tidak". Urutan sekarang (semua bergiliran menurut prioritas watchlist):
    (1) cadangan negara: bergiliran antar target ``COUNTRY``, maks ``per_target`` per negara, sampai ``country_reserve`` halaman;
    (2) putaran pertama lokasi: bergiliran HANYA antar ``LOCATION``, maks ``per_target`` per lokasi; (3) sisa untuk lokasi: HANYA
    antar ``LOCATION``, tanpa batas per target; (4) sisa terakhir, hanya bila semua lokasi kehabisan kandidat: bergiliran antar semua
    target (lokasi lalu negara) tanpa batas per target, jadi jatah tidak kosong selama masih ada kandidat. Negara tanpa kandidat
    lokasi sama sekali memakai seluruh jatah. Hasil ``dipilih`` dan ``dilewati`` mengikuti urutan kandidat asli.

    N-09c (DEC-020): jatah ``max_pages`` dihitung per URL UNIK. Saat sebuah kandidat dipilih, semua salinan URL yang sama (target
    lokasi lain) ikut terpilih tanpa memakai jatah dan tanpa memakai kuota per target.
    """
    groups: dict[str, list[Json]] = {}
    by_url: dict[str, list[Json]] = {}
    for c in candidates:
        groups.setdefault(_target_key(c["target"]), []).append(c)
        by_url.setdefault(c["url"], []).append(c)
    ordered = _ordered_target_keys(list(groups), watchlist)
    location_keys = [k for k in ordered if k.startswith("LOCATION:")]
    country_keys = [k for k in ordered if k.startswith("COUNTRY:")]

    queues = {key: deque(_order_within_target(items, policy_first=key.startswith("COUNTRY:"))) for key, items in groups.items()}
    taken: Counter = Counter()
    chosen: set[int] = set()
    pages = 0  # URL unik terpilih

    def head(key: str) -> Json | None:
        queue = queues[key]
        while queue and id(queue[0]) in chosen:  # salinan yang sudah ikut terpilih lewat URL-nya
            queue.popleft()
        return queue[0] if queue else None

    def rotate(keys: list[str], limit: int | None, quota: int) -> None:
        nonlocal pages
        start = pages
        while pages - start < quota:
            progressed = False
            for key in keys:
                if pages - start >= quota:
                    break
                if limit is not None and taken[key] >= limit:
                    continue
                cand = head(key)
                if cand is None:
                    continue
                queues[key].popleft()
                chosen.update(id(c) for c in by_url[cand["url"]])
                taken[key] += 1
                pages += 1
                progressed = True
            if not progressed:
                return

    reserve = min(country_reserve, max_pages, sum(len(queues[k]) for k in country_keys))
    rotate(country_keys, per_target, reserve)  # 1. cadangan negara
    rotate(location_keys, per_target, max_pages - pages)  # 2. putaran pertama: hanya lokasi
    rotate(location_keys, None, max_pages - pages)  # 3. sisa untuk lokasi
    rotate(location_keys + country_keys, None, max_pages - pages)  # 4. lokasi habis: sisa dibagi ke semua target

    return [c for c in candidates if id(c) in chosen], [c for c in candidates if id(c) not in chosen]


def _candidate_domain(candidate: Mapping[str, Any]) -> str:
    return candidate.get("domain") or registered_domain(candidate["url"])


def passed_domains(items: list[Json], results: Mapping[str, Json]) -> set[str]:
    """N-14 (DEC-027): domain terdaftar dari ``items`` yang halamannya ``IN_WINDOW`` menurut ``results``."""
    return {_candidate_domain(c) for c in items if (results.get(c["url"]) or {}).get("date_status") == "IN_WINDOW"}


def select_refill(candidates: list[Json], selected: list[Json], results: Mapping[str, Json], watchlist: Mapping[str, Any], *,
                  per_target: int, max_pages: int) -> list[Json]:
    """N-14 (DEC-027): halaman pengganti untuk gelombang isi ulang. Hanya target ``LOCATION`` yang domain lolosnya (``IN_WINDOW``) < 2
    dan masih punya kandidat yang belum dibuka (``selected`` = yang sudah dibuka gelombang pertama, ``results`` = hasilnya).

    Urutan di dalam lokasi = ``_order_within_target``. Kandidat yang domain terdaftarnya sudah dicoba di run ini untuk target mana
    pun (lolos atau gagal, termasuk pengganti yang baru dipilih) dilewati, jadi situs yang menolak tidak dicoba lagi. Maks
    ``per_target`` per lokasi dan ``max_pages`` URL unik, bergiliran menurut prioritas watchlist. Target ``COUNTRY`` tidak diisi
    ulang. Salinan URL yang sama untuk target lain ikut dikembalikan tanpa memakai jatah (N-09c). Hasil mengikuti urutan kandidat
    asli; deterministik."""
    if per_target <= 0 or max_pages <= 0:
        return []
    opened = {c["url"] for c in selected}
    tried = {_candidate_domain(c) for c in selected}
    groups: dict[str, list[Json]] = {}
    by_url: dict[str, list[Json]] = {}
    for c in candidates:
        groups.setdefault(_target_key(c["target"]), []).append(c)
        by_url.setdefault(c["url"], []).append(c)

    queues: dict[str, deque[Json]] = {}
    for key in _ordered_target_keys(list(groups), watchlist):
        if not key.startswith("LOCATION:"):
            continue
        if len(passed_domains([c for c in groups[key] if c["url"] in opened], results)) >= 2:
            continue
        unopened = [c for c in _order_within_target(groups[key]) if c["url"] not in opened]
        if unopened:
            queues[key] = deque(unopened)

    picked: dict[int, Json] = {}
    taken: Counter = Counter()
    pages = 0
    while pages < max_pages:
        progressed = False
        for key, queue in queues.items():
            if pages >= max_pages:
                break
            if taken[key] >= per_target:
                continue
            while queue and (id(queue[0]) in picked or _candidate_domain(queue[0]) in tried):  # salinan yang sudah ikut / domain sudah dicoba
                queue.popleft()
            if not queue:
                continue
            cand = queue.popleft()
            picked.update((id(c), c) for c in by_url[cand["url"]])
            tried.add(_candidate_domain(cand))
            taken[key] += 1
            pages += 1
            progressed = True
        if not progressed:
            break
    return [c for c in candidates if id(c) in picked]


def _refill_text(refill: list[Json], short_after: list[str]) -> str:
    """Teks trace gelombang isi ulang: halaman per lokasi (URL unik) dan lokasi yang masih di bawah 2 domain. Nama dari watchlist."""
    per_location: dict[str, set[str]] = {}
    for c in refill:
        if c["target"].get("scope") != "COUNTRY":
            per_location.setdefault(_target_label(c["target"]), set()).add(c["url"])
    total = len({c["url"] for c in refill})
    detail = ", ".join(f"{name} +{len(found)}" for name, found in per_location.items())
    return (f"refill: +{_plural(total, 'page')} for {_plural(len(per_location), 'location')} ({detail}); "
            f"still under 2 domains: {', '.join(short_after) or '-'}")


@traced_node(step="verify_dates", tool="verify_sources")
def verify_dates(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    candidates = state.get("candidates") or []
    watchlist = state["watchlist"]
    session = (config.get("configurable") or {}).get(SESSION_KEY)
    clock = _clock(config)
    deadline = clock() + cfg.VERIFY_TIME_BUDGET_S  # dihitung sekali: gelombang isi ulang memakai tenggat yang sama (N-14)
    today = _today(config)
    selected, skipped = select_for_verification(
        candidates, watchlist, max_pages=cfg.MAX_VERIFY_PAGES, per_target=cfg.VERIFY_PER_TARGET_FIRST_PASS,
        country_reserve=cfg.VERIFY_COUNTRY_RESERVE)
    out = verify.verify_sources(selected, today, session=session, deadline=deadline, clock=clock)
    results = {r["url"]: r for r in out["results"]}
    stats = dict(out["stats"])

    refill = select_refill(candidates, selected, results, watchlist, per_target=cfg.VERIFY_REFILL_PER_TARGET,
                           max_pages=cfg.VERIFY_REFILL_MAX_PAGES)
    refill_text = ""
    if refill:
        remaining = deadline - clock()
        if remaining < cfg.VERIFY_REFILL_MIN_REMAINING_S:
            refill_text = (f"refill skipped: {_plural(len({c['url'] for c in refill}), 'page')} not checked, "
                           f"{max(0.0, remaining):.1f} s left of the {cfg.VERIFY_TIME_BUDGET_S} s verification budget")
            refill = []
        else:
            extra = verify.verify_sources(refill, today, max_pages=cfg.VERIFY_REFILL_MAX_PAGES, session=session, deadline=deadline,
                                          clock=clock)
            results.update({r["url"]: r for r in extra["results"]})
            stats = {k: v + extra["stats"].get(k, 0) for k, v in stats.items()}
            opened = [c for c in [*selected, *refill] if c["target"].get("scope") != "COUNTRY"]
            short = [key.split(":", 1)[1] for key in _ordered_target_keys(list(dict.fromkeys(_target_key(c["target"]) for c in opened)), watchlist)
                     if len(passed_domains([c for c in opened if _target_key(c["target"]) == key], results)) < 2]
            refill_text = _refill_text(refill, short)
    refilled = {c["url"] for c in refill}
    skipped = [c for c in skipped if c["url"] not in refilled]

    counts = state.get("screen_counts") or {}
    evidence_stats = {"candidates": counts.get("candidates", len(candidates)), "screened": counts.get("screened", len(candidates)),
                      "in_window": stats["in_window"], "stale": stats["stale"] + stats["future"],
                      "not_found": stats["not_found"], "fetch_failed": stats["fetch_failed"],
                      "skipped_budget": len({c["url"] for c in skipped}) + stats["skipped_budget"]}  # per URL unik (N-09c)
    claims = apply_verification(state.get("sonar_claims") or [], candidates, results)
    update: dict[str, Any] = {
        "verified": results, "evidence_stats": evidence_stats, "claims_for_llm": claims,
        "pages_checked": stats["pages_fetched"],
        "extra_tool_calls": 1 if stats["pages_fetched"] else 0,  # dua gelombang = satu tool verify_sources (G-05)
        "output_summary": (f"{stats['in_window']} in window, {stats['stale']} stale, {stats['not_found']} no date, "
                           f"{stats['fetch_failed']} fetch failed"),
    }
    parts: list[str] = []
    if evidence_stats["skipped_budget"]:
        per_target = Counter(_target_label(c["target"]) for c in skipped)
        parts.append(f"checked {stats['pages_fetched']} of {len({c['url'] for c in candidates})} candidates; "
                     f"{evidence_stats['skipped_budget']} not checked (page budget): "
                     + ", ".join(f"{t}: {n}" for t, n in per_target.items()))
    elif not stats["in_window"]:
        parts.append(f"{len(candidates)} candidates, 0 with a verified publication date in the window → no signal")
    if refill_text:
        parts.append(refill_text)
    counts_by_country = policy_title_counts(candidates, selected, watchlist)  # N-11 (DEC-023): hanya teks, bukan kontrak
    if counts_by_country:
        parts.append("policy-word titles checked/available: " + ", ".join(f"{c} {k}/{m}" for c, k, m in counts_by_country))
    if parts:
        update["decision"] = "; ".join(parts)
    return update


@traced_node(step="no_recent_evidence")
def no_recent_evidence(state: ScanExternalState) -> dict[str, Any]:
    n = len({c["url"] for c in state.get("candidates") or []}) or (state.get("screen_counts") or {}).get("screened", 0)  # URL unik
    skipped = (state.get("evidence_stats") or {}).get("skipped_budget", 0)
    if skipped:
        decision = (f"{n} candidates, {state.get('pages_checked', n - skipped)} checked, 0 with a verified publication date in "
                    f"the window; {skipped} not checked (page budget) → no signal")
    else:
        decision = f"{n} candidates, 0 with a verified publication date in the window → no signal"
    return {"signals_saved": 0, "signals_dropped": 0, "signals_matched": 0, "cases": [], "unverified": [],
            "decision": decision}


# --------------------------------------------------------------------------- node: ekstraksi LLM


def build_evidence(candidates: list[Json], results: Mapping[str, Json]) -> list[Json]:
    """Bukti IN_WINDOW bernomor E1..En (urutan = prioritas)."""
    evidence = []
    for cand in candidates:
        r = results.get(cand["url"])
        if r and r["date_status"] == "IN_WINDOW":
            evidence.append({"id": f"E{len(evidence) + 1}", "url": cand["url"], "published_at": r["published_at"],
                             "date_source": r["date_source"], "domain": r["domain"], "engines": cand["engines"],
                             "topics": list(cand.get("topics") or []),
                             "title": r["page_title"] or cand["title"], "snippet": cand["snippet"],
                             "description": r["page_description"], "target": cand["target"]})
    return evidence


def country_reserve_used(evidence: list[Json], limit: int, country_reserve: int = cfg.JUDGE_COUNTRY_RESERVE) -> int:
    """Slot cadangan negara yang dipakai ``cap_evidence``: ``min(country_reserve, limit, jumlah bukti COUNTRY)``; 0 bila semua bukti
    muat di ``limit`` (tidak ada pemilihan, jadi tidak ada cadangan)."""
    if len(evidence) <= limit:
        return 0
    return min(country_reserve, limit, sum(1 for e in evidence if e["target"].get("scope") == "COUNTRY"))


def cap_evidence(evidence: list[Json], watchlist: Mapping[str, Any], limit: int,
                 country_reserve: int = cfg.JUDGE_COUNTRY_RESERVE) -> tuple[list[Json], list[Json]]:
    """(dinilai, tidak dinilai): paling banyak ``limit`` bukti (satu gelombang panggilan Claude, N-09c). Bila lebih, pilih dengan
    pola ``select_for_verification`` (N-10, DEC-022): (1) cadangan negara: bergiliran antar target ``COUNTRY`` (urutan watchlist)
    sampai ``country_reserve_used`` bukti; (2) sisa slot bergiliran antar semua target (lokasi lalu negara). Cadangan yang tidak
    terpakai (bukti negara sedikit) kembali ke kolam. Lalu beri nomor ulang E1..En menurut urutan asli; bukti yang tidak dinilai
    tidak mendapat id. Deterministik."""
    if len(evidence) <= limit:
        return evidence, []
    groups: dict[str, deque[Json]] = {}
    for e in evidence:
        groups.setdefault(_target_key(e["target"]), deque()).append(e)
    keys = _ordered_target_keys(list(groups), watchlist)
    country_keys = [k for k in keys if k.startswith("COUNTRY:")]
    picked: set[int] = set()

    def rotate(order: list[str], goal: int) -> None:
        while len(picked) < goal:
            progressed = False
            for key in order:
                if len(picked) >= goal:
                    break
                if groups[key]:
                    picked.add(id(groups[key].popleft()))
                    progressed = True
            if not progressed:
                return

    rotate(country_keys, country_reserve_used(evidence, limit, country_reserve))
    rotate(keys, limit)
    kept = [e for e in evidence if id(e) in picked]
    dropped = [e for e in evidence if id(e) not in picked]
    return [{**e, "id": f"E{i + 1}"} for i, e in enumerate(kept)], dropped


def retry_severity(config: RunnableConfig, state: Mapping[str, Any], raw: list[judge.EvidenceJudgement],
                   evidence: list[Json]) -> tuple[list[judge.EvidenceJudgement], set[str], str]:
    """Tanya ulang severity saja (N-09c, DEC-020): (jawaban setelah diisi, id yang terisi, catatan trace). Jatah SENDIRI: satu
    panggilan, <= ``SEVERITY_RETRY_MAX_ITEMS`` bukti, baca ``SEVERITY_RETRY_READ_TIMEOUT_S`` tanpa retry SDK, mulai hanya bila
    waktu run (dihitung ulang sekarang) <= ``SEVERITY_RETRY_LATEST_START_S``. Tidak menambah ``extra_tool_calls``. Kegagalan apa
    pun tidak menjatuhkan run: bukti tetap ``INCOMPLETE``. Hanya ``severity`` yang berubah (``apply_severity``)."""
    missing = judge.severity_missing(raw, evidence)
    if not missing:
        return raw, set(), ""
    elapsed = graph_common._now() - float(state.get("started_at") or graph_common._now())
    if elapsed > cfg.SEVERITY_RETRY_LATEST_START_S:
        return raw, set(), f"severity retry skipped (time: {elapsed:.0f} s elapsed)"
    asked = missing[:cfg.SEVERITY_RETRY_MAX_ITEMS]
    try:
        llm = get_llm(config, read_timeout=cfg.SEVERITY_RETRY_READ_TIMEOUT_S, max_attempts=1)
        answers = judge.ask_severity(llm, load_prompt("severity_fill"), asked, evidence, raw, state.get("watchlist"))
    except Exception as exc:  # noqa: BLE001 - tanya ulang gagal: bukti tetap INCOMPLETE, run lanjut
        return raw, set(), f"severity retry failed ({type(exc).__name__})"
    patched = judge.apply_severity(raw, answers, asked)
    still_missing = set(judge.severity_missing(patched, evidence))
    filled = {i for i in asked if i not in still_missing}
    head = f"{len(missing)} missing, asked {len(asked)} (item budget)" if len(missing) > len(asked) else f"asked {len(asked)}"
    return patched, filled, f"severity retry: {head}, filled {len(filled)}"


def extract_attempts(elapsed_s: float) -> int:
    """Percobaan ekstraksi Claude: 2 bila run baru berjalan <= ``EXTRACT_RETRY_IF_ELAPSED_LE_S``, selain itu 1 (jatah 120 s, G-05):
    dua kali 30 s setelah detik ke-45 melewati batas dan seluruh bukti hilang."""
    return 2 if elapsed_s <= cfg.EXTRACT_RETRY_IF_ELAPSED_LE_S else 1


@traced_node(step="extract_signals")
def extract_signals(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    """Penilaian bukti per artikel (DEC-018, cara FAME): Claude hanya menjawab ya/tidak per bukti atas pertanyaan yang disusun
    kode; kode memeriksa jawaban (``validate_judgements``) dan menyusun sinyal (``signals_from_judgements``). Nama langkah trace,
    ``candidate_signals``, dan ``evidence`` tidak berubah."""
    built = build_evidence(state.get("candidates") or [], state.get("verified") or {})
    evidence, unjudged = cap_evidence(built, state["watchlist"], cfg.JUDGE_MAX_ITEMS_PER_RUN)
    reserve_used = country_reserve_used(built, cfg.JUDGE_MAX_ITEMS_PER_RUN)
    elapsed = graph_common._now() - float(state.get("started_at") or graph_common._now())
    llm = get_llm(config, read_timeout=EXTRACT_READ_TIMEOUT_S, max_attempts=extract_attempts(elapsed))  # sekali: dipakai bersama
    today = _today(config)
    run = judge.judge_evidence(llm, load_prompt("external_signal_extraction"), evidence, state["watchlist"], today)
    raw, filled, retry_note = retry_severity(config, state, run.raw, evidence)  # setelah penilaian utama, sebelum validasi
    judged = judge.validate_judgements(raw, evidence, today, severity_retry_ids=filled)
    signals = [ExtractedSignal(**s).model_dump() for s in
               judge.signals_from_judgements(judged, evidence, state.get("claims_for_llm") or [], state["watchlist"])]
    yes = sum(1 for j in judged if j["answer"])
    return {
        "evidence": evidence, "judgements": judged, "candidate_signals": signals,
        "input_summary": f"{len(evidence)} evidence items",
        "output_summary": f"{yes} yes of {len(judged)} items; {len(signals)} candidate signals",
        "decision": "; ".join(part for part in (
            judge.summarize_judgements(judged, run.calls, run.failed),
            (f"JUDGE_ITEM_BUDGET: {len(unjudged)} evidence items not judged ("
             + ", ".join(f"{t}: {n}" for t, n in Counter(_target_label(e["target"]) for e in unjudged).items()) + ")")
            if unjudged else "",
            f"country reserve used {reserve_used} of {cfg.JUDGE_COUNTRY_RESERVE}" if reserve_used else "",
            retry_note, *judge.downtime_notes(judged, evidence)) if part),
    }


# --------------------------------------------------------------------------- node: filter (3.8)


def _valid_date(value: str) -> date | None:
    if not _DATE.match(value or ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_MEDIA_SUFFIX = re.compile(r"\s+[-|–—]\s+[^-|–—]{1,60}$")  # " - <nama media>" / " | <nama media>" di ujung judul
_PUNCTUATION = re.compile(r"[^\w\s]")
SYNDICATION_MIN_WORDS = 6  # judul lebih pendek tidak pernah dianggap salinan


def _item_domain(item: Mapping[str, Any]) -> str:
    return item.get("domain") or registered_domain(item["url"])


def _in_domains(domain: str, registered: frozenset[str] | set[str]) -> bool:
    return any(domain == t or domain.endswith("." + t) for t in registered)


def syndication_key(title: Any) -> str | None:
    """Judul ternormalisasi untuk mendeteksi salinan/sindikasi: huruf kecil, tanpa tanda baca dan sufiks nama media, spasi
    dirapatkan. ``None`` bila judulnya < ``SYNDICATION_MIN_WORDS`` kata (terlalu pendek untuk disebut salinan)."""
    words = _PUNCTUATION.sub(" ", _MEDIA_SUFFIX.sub("", str(title or "").strip()).lower()).split()
    return " ".join(words) if len(words) >= SYNDICATION_MIN_WORDS else None


def independent_domains(items: list[Mapping[str, Any]]) -> set[str]:
    """Domain terdaftar unik dari ``items``, tetapi dua bukti berjudul sama (``syndication_key``) dihitung satu (salinan
    sindikasi; Europe Media Monitor: kejadian butuh >= 2 artikel non-duplikat dari berita berbeda). Hasil tidak bergantung pada
    urutan masukan: bukti diurutkan menurut (domain, URL) dan salinan = kemunculan berikutnya dari judul yang sama."""
    seen_titles: set[str] = set()
    domains: set[str] = set()
    for item in sorted(items, key=lambda i: (_item_domain(i), i.get("url") or "")):
        key = syndication_key(item.get("title"))
        if key is not None:
            if key in seen_titles:
                continue
            seen_titles.add(key)
        domains.add(_item_domain(item))
    return domains


def verification_of(items: list[Mapping[str, Any]], scope: str) -> tuple[str, str]:
    """Status bukti. ``LOCATION`` (DEC-025, mengganti DEC-018 K3): ``VERIFIED`` bila >= 2 domain independen (salinan berjudul sama
    dihitung satu), ATAU >= 1 domain ``OFFICIAL_HAZARD_DOMAINS`` (lembaga resmi kebencanaan/cuaca), ATAU >= 1 domain
    ``TRUSTED_PUBLIC_NEWS_DOMAINS`` (kantor berita nasional / lembaga penyiaran publik); selain itu ``UNVERIFIED``. ``COUNTRY``
    (kebijakan/logistik): >= 2 domain terdaftar berbeda atau >= 1 domain di ``TRUSTED_DOMAINS`` + ``OFFICIAL_HAZARD_DOMAINS`` +
    ``TRUSTED_PUBLIC_NEWS_DOMAINS``. Hanya syarat jumlah sumber yang berubah; pemeriksaan lain dilakukan ``judge_signal``.
    ``items`` = butir bukti lengkap (judul, domain)."""
    domains = {_item_domain(i) for i in items}
    if scope == "LOCATION":
        if len(independent_domains(items)) >= 2:
            return "VERIFIED", "two or more independent domains"
        if any(_in_domains(d, cfg.OFFICIAL_HAZARD_DOMAINS) for d in domains):
            return "VERIFIED", "official hazard agency"
        if any(_in_domains(d, cfg.TRUSTED_PUBLIC_NEWS_DOMAINS) for d in domains):
            return "VERIFIED", "national news agency or public broadcaster"
        return "UNVERIFIED", "single source (after syndication check)"
    if len(domains) >= 2:
        return "VERIFIED", "two or more independent domains"
    if any(_in_domains(d, cfg.TRUSTED_DOMAINS | cfg.OFFICIAL_HAZARD_DOMAINS | cfg.TRUSTED_PUBLIC_NEWS_DOMAINS) for d in domains):
        return "VERIFIED", "trusted domain"
    return "UNVERIFIED", "single untrusted domain"


def judge_signal(sig: Mapping[str, Any], evidence: Mapping[str, Json], claims: list[Json], watchlist: Mapping[str, Any],
                 today: date, categories: Sequence[str] = ()) -> tuple[Json | None, str | None]:
    """Satu sinyal -> (sinyal TL-05 + ``verification``/``alasan``, None) atau (None, alasan buang). ``categories`` = kategori data
    (``data_categories``): ``category`` sinyal negara dipetakan ke sana, tidak cocok -> ``None`` (A-18)."""
    cited = [evidence[i] for i in dict.fromkeys(sig.get("evidence_ids") or []) if i in evidence]  # id asing diabaikan (G-02)
    if not cited:
        return None, "NO_SOURCE_URL"
    known: list[Json] = []
    texts = [f"{e['title']} {e['snippet']} {e['description']}" for e in cited]

    if sig["scope"] == "COUNTRY" and sig["risk_type"] not in cfg.COUNTRY_SCOPE_RISK_TYPES:
        # bahaya fisik bersifat lokal: sinyal "banjir seluruh negara" dari berita di luar watchlist memetakan SEMUA site di
        # negara itu (R-01) -> alarm palsu massal. Hanya kebijakan/logistik/politik yang boleh berlingkup negara.
        return None, "SCOPE_NOT_ALLOWED"
    if sig["scope"] == "LOCATION":
        known = [l for l in watchlist["locations"] if core_name(l["kabupaten"]).lower() == core_name(sig.get("kabupaten") or "").lower()]
        if not known or not mentions_location(known[0], *texts):  # nama Latin atau salah satu local_names (DEC-025)
            return None, "LOCATION_NOT_IN_EVIDENCE"
        country = known[0]["country"]
    else:
        country = str(sig.get("country") or "").upper()
        if country not in {c["code"] for c in watchlist["countries"]} or not mentions_country(country, *texts):
            return None, "COUNTRY_NOT_IN_EVIDENCE"
        if not any(e["target"]["scope"] == "COUNTRY" and e["target"]["country"] == country for e in cited):
            # sinyal negara harus didukung bukti kebijakan/logistik per negara (query negara Brave/sonar), bukan berita lokasi yang kebetulan
            # menyebut nama negara (uji live SP-10: gangguan jalan lokal menjadi "LOGISTICS ID" dan memetakan semua site di ID)
            return None, "SCOPE_NOT_SUPPORTED"
        if sig["risk_type"] == "TARIFF" and country == str(watchlist.get("home_country") or "").upper():
            # tarif dari luar terhadap barang negara rumah tidak mengubah biaya pembelian dari supplier di negara rumah (R-14);
            # tanpa aturan ini artikel "negara X menaikkan tarif ekspor <negara rumah>" memetakan SEMUA site di negara rumah.
            return None, "HOME_COUNTRY_TARIFF"

    if sig["scope"] == "LOCATION" and sig.get("severity") is None:  # TL-05 menolak severity null untuk LOCATION (DEC-019)
        return None, "INCOMPLETE"

    effective = _valid_date(sig.get("effective_date", ""))
    earliest = min(date.fromisoformat(e["published_at"]) for e in cited)
    if effective is None or effective < earliest - timedelta(days=DATE_SLACK_DAYS) or effective > today + timedelta(days=MAX_FUTURE_DAYS):
        return None, "DATE_NOT_SUPPORTED"

    tariff_pp = None
    if sig["risk_type"] == "TARIFF" and sig.get("tariff_pp") is not None:
        cited_urls = {e["url"] for e in cited}
        grounded = [c["rate_change_pp"] for c in claims
                    if c.get("rate_grounded") and cited_urls & set(c["evidence_urls"])]
        tariff_pp = sig["tariff_pp"] if any(abs(float(g) - float(sig["tariff_pp"])) < 1e-9 for g in grounded) else None
    tariff = sig["risk_type"] == "TARIFF"

    verification, reason = verification_of(cited, sig["scope"])
    out: Json = {
        "risk_type": sig["risk_type"], "scope": sig["scope"], "country": country,
        "kabupaten": known[0]["kabupaten"] if sig["scope"] == "LOCATION" else None,
        "kecamatan": list(sig.get("kecamatan") or []) if sig["scope"] == "LOCATION" else [],
        "severity": sig["severity"] if sig["scope"] == "LOCATION" else None,  # DEC-019: negara tanpa severity
        "effective_date": effective.isoformat(),
        "tariff_pp": tariff_pp if tariff else None,
        "category": map_category(sig.get("category"), categories) if sig["scope"] == "COUNTRY" else None,
        "downtime_days": judge.normalize_downtime_days(sig.get("downtime_days"))[0],
        "summary": (sig.get("summary") or "").strip(), "source_urls": [e["url"] for e in cited],
        "engine": cited[0]["engines"][0], "verification": verification,
        "evidence": [{"url": e["url"], "published_at": e["published_at"], "date_source": e["date_source"],
                      "engine": e["engines"][0]} for e in cited],
        "_reason": reason, "_items": cited,  # privat: dipakai merge_signals, dibuang di filter_signals sebelum TL-05
    }
    return (out, None) if out["summary"] else (None, "INCOMPLETE")


def merge_signals(signals: list[Json]) -> list[Json]:
    """Sinyal yang sama (jenis, lingkup, lokasi/negara, tanggal efektif) digabung: bukti disatukan."""
    merged: dict[tuple, Json] = {}
    for s in signals:
        key = (s["risk_type"], s["scope"], s["kabupaten"] or s["country"], s["effective_date"])
        if key not in merged:
            merged[key] = s
            continue
        kept = merged[key]
        kept["source_urls"] = list(dict.fromkeys([*kept["source_urls"], *s["source_urls"]]))
        known = {e["url"] for e in kept["evidence"]}
        kept["evidence"].extend(e for e in s["evidence"] if e["url"] not in known)
        kept["_items"] = [*kept["_items"], *(i for i in s["_items"] if i["url"] not in {k["url"] for k in kept["_items"]})]
        kept["severity"] = max((v for v in (kept["severity"], s["severity"]) if v is not None), default=None)
        if kept["tariff_pp"] is None:
            kept["tariff_pp"] = s["tariff_pp"]
        for name in ("category", "downtime_days"):  # A-18: nilai tidak-null pertama menang
            if kept.get(name) is None and s.get(name) is not None:
                kept[name] = s[name]
        kept["verification"], kept["_reason"] = verification_of(kept["_items"], kept["scope"])
    return list(merged.values())


@traced_node(step="filter_signals")
def filter_signals(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    evidence = {e["id"]: e for e in state.get("evidence") or []}
    today = _today(config)
    judged: list[Json] = []
    dropped: list[Json] = []
    for i, sig in enumerate(state.get("candidate_signals") or []):
        out, reason = judge_signal(sig, evidence, state.get("claims_for_llm") or [], state["watchlist"], today,
                                   state.get("data_categories") or [])
        if out is None:
            dropped.append({"index": i, "reason": reason})
        else:
            judged.append(out)
    merged = merge_signals(judged)

    to_save, unverified = [], []
    for s in merged:
        reason = s.pop("_reason")
        s.pop("_items", None)
        if s["verification"] == "VERIFIED":
            to_save.append(s)
        else:
            unverified.append({"risk_type": s["risk_type"], "location": s["kabupaten"] or s["country"],
                               "urls": s["source_urls"], "reason": reason})
    reasons = Counter(d["reason"] for d in dropped)
    return {
        "signals_to_save": to_save, "unverified": unverified, "local_dropped": dropped,
        "output_summary": f"{len(to_save)} verified, {len(unverified)} unverified, {len(dropped)} dropped",
        "decision": (f"filter: {len(to_save)} VERIFIED, {len(unverified)} UNVERIFIED (not saved); dropped {len(dropped)} "
                     f"({_fmt_dates(reasons)})"),
    }


@traced_node(step="save_signals", tool="save_signals")
def save_signals(state: ScanExternalState, config: RunnableConfig) -> dict[str, Any]:
    signals = list(state.get("signals_to_save") or [])
    local = len(state.get("local_dropped") or [])
    if not signals:
        return {"signals_saved": 0, "signals_dropped": local, "signals_matched": 0, "cases": [],
                "trace_status": "SKIPPED", "decision": "no verified signals to save"}

    result = get_tools(config).save_signals(signals)
    saved = result.get("saved") or []
    server_dropped = result.get("dropped") or []
    cases = list(dict.fromkeys(s["case_id"] for s in saved if s.get("case_id")))
    matched = sum(1 for s in saved if s.get("match") == "MATCHED")
    return {
        "signals_saved": len(saved), "signals_dropped": local + len(server_dropped), "signals_matched": matched,
        "cases": cases, "input_summary": f"{len(signals)} signals",
        "output_summary": f"{len(saved)} saved ({matched} matched), {len(server_dropped)} dropped by server; "
                          f"cases: {', '.join(cases) or '-'}",
    }


# --------------------------------------------------------------------------- cabang (fungsi Python, G-06)


def route_after_watchlist(state: ScanExternalState) -> str:
    return "empty_partition" if is_empty_watchlist(state["watchlist"]) else "plan_queries"


def route_after_search(state: ScanExternalState) -> str:
    return "screen_candidates" if "OK" in (state.get("source_status") or {}).values() else "sources_unavailable"


def route_after_verify(state: ScanExternalState) -> str:
    return "extract_signals" if (state.get("evidence_stats") or {}).get("in_window") else "no_recent_evidence"


def build_graph() -> Any:
    g = StateGraph(ScanExternalState)
    for node in (build_watchlist, empty_partition, plan_queries, search_brave, search_sonar, join_sources,
                 sources_unavailable, screen_candidates, verify_dates, no_recent_evidence, extract_signals,
                 filter_signals, save_signals):
        g.add_node(node.step, node)
    g.add_edge(START, "build_watchlist")
    for a, b in (("plan_queries", "search_brave"), ("plan_queries", "search_sonar"), ("screen_candidates", "verify_dates"),
                 ("extract_signals", "filter_signals"), ("filter_signals", "save_signals")):
        g.add_edge(a, b)
    g.add_edge(["search_brave", "search_sonar"], "join_sources")  # DEC-026, DEC-032: gabung setelah KEDUANYA selesai
    g.add_conditional_edges("build_watchlist", route_after_watchlist,
                            {"plan_queries": "plan_queries", "empty_partition": "empty_partition"})
    g.add_conditional_edges("join_sources", route_after_search,
                            {"screen_candidates": "screen_candidates", "sources_unavailable": "sources_unavailable"})
    g.add_conditional_edges("verify_dates", route_after_verify,
                            {"extract_signals": "extract_signals", "no_recent_evidence": "no_recent_evidence"})
    for end in ("save_signals", "sources_unavailable", "no_recent_evidence", "empty_partition"):
        g.add_edge(end, END)
    return g.compile()


# --------------------------------------------------------------------------- entrypoint


def partition_data(state: Mapping[str, Any]) -> Json:
    """``data.partition`` (kontrak D.3 v1.9): bagian yang dikerjakan dan isinya (daerah dan kode negara watchlist bagian itu)."""
    index, count = _partition_of(state)
    watchlist = state.get("watchlist") or {}
    return {"index": index, "count": count,
            "locations": [str(l["kabupaten"]) for l in watchlist.get("locations") or []],
            "countries": [str(c["code"]).upper() for c in watchlist.get("countries") or []]}


def build_response(outcome: RunOutcome) -> Json:
    """Hasil run -> ``status`` / ``summary`` / ``data`` kontrak D.3 (v1.2; v1.9: ``data.partition``)."""
    s = outcome.state
    sources = [e for e in ENGINES if (s.get("source_status") or {}).get(e) == "OK"]
    data: Json = {
        "signals_saved": s.get("signals_saved", 0), "signals_dropped": s.get("signals_dropped", 0),
        "unverified": s.get("unverified", []), "cases": s.get("cases", []), "sources_used": sources,
        "evidence_stats": s.get("evidence_stats") or {"candidates": (s.get("screen_counts") or {}).get("candidates", 0),
                                                       "screened": 0, "in_window": 0, "stale": 0, "not_found": 0,
                                                       "fetch_failed": 0, "skipped_budget": 0},
        "skipped_targets": s.get("skipped_targets", []),
        "partition": partition_data(s),
    }
    if outcome.timed_out:
        return {"status": "AGENT_TIMEOUT", "summary": f"Agent stopped: {outcome.message}", "data": data}
    if s.get("error"):
        return {"status": "ERROR", "summary": s["error"], "data": data}
    if s.get("watchlist") is not None and is_empty_watchlist(s["watchlist"]):
        index, count = _partition_of(s)
        return {"status": "OK", "data": data, "summary": (f"No news search: partition {index + 1}/{count} is empty "
                                                          "(no locations or origin countries assigned to it).")}

    summary = (f"{data['signals_saved']} verified signals saved, {len(data['unverified'])} unverified (not saved); "
               f"sources: {', '.join(sources) or '-'}")
    return {"status": "OK", "summary": summary + ".", "data": data}


def run(payload: Mapping[str, Any], *, tools: ToolClient | None = None, llm: Any = None, brave: Any = None,
        sonar: Any = None, session: Any = None, today: date | None = None,
        clock: Callable[[], float] | None = None) -> Json:
    """Jalankan ``scan_external`` untuk payload D.2. Argumen selain ``payload`` hanya diisi tes.

    ``scan_partition`` (v1.9, DEC-029) divalidasi SEBELUM apa pun dipanggil: tidak sah -> ``ERROR`` dengan ``VALIDATION_FAILED``."""
    try:
        index, count = parse_scan_partition(payload.get("scan_partition"))
    except ValueError as exc:
        return {"status": "ERROR", "summary": f"VALIDATION_FAILED: {exc}", "data": {}}
    tools = tools or ToolClient.from_payload(payload)
    configurable = {key: value for key, value in ((LLM_KEY, llm), (BRAVE_KEY, brave), (SONAR_KEY, sonar), (SESSION_KEY, session),
                                                  (TODAY_KEY, today), (CLOCK_KEY, clock)) if value is not None}
    initial = {**new_run_state(payload), "scan_partition": {"index": index, "count": count}}
    outcome = run_guarded(build_graph(), initial, tools, configurable=configurable)
    return build_response(outcome)
