"""Rencana query berita dari watchlist (fungsi murni, DEC-015, ``docs/NEWS_PIPELINE.md`` 3.2, 3.3, 3.9).

Tidak ada LLM dan tidak ada nama tempat di sini: lokasi, negara, dan prioritas SELALU datang dari watchlist (TL-06 / TL-22).
Kata kunci umum per bahasa ada di ``tools/news_config.py``. Target yang tidak muat anggaran dikembalikan di ``skipped``.
N-17 (DEC-032): sumber berita hanya Brave (kejadian per daerah, kebijakan dan logistik per negara asal) dan sonar.

Bentuk watchlist (3.2)::

    {"home_country": "<ISO2>",
     "locations": [{"kabupaten": "...", "country": "<ISO2>", "site_ids": [...], "tiers": [...], "priority": 0.0}],
     "countries": [{"code": "<ISO2>", "name": "<nama Inggris>", "goods": [...], "po_exposure_share": 0.0}]}
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from tools import news_config as cfg

Json = dict[str, Any]


def _term(word: str) -> str:
    """Kata kunci majemuk diberi tanda kutip supaya dicari sebagai frasa."""
    return f'"{word}"' if " " in word else word


def event_language(country: str, home_country: str | None = None) -> str:
    """Bahasa kata kunci kejadian menurut negara LOKASI (DEC-025, K1): ID Indonesia, CN Mandarin (Hanzi), JP Jepang (Kanji), selain itu
    Inggris. ``home_country`` hanya diterima demi kompatibilitas; tidak lagi menentukan bahasa (negara rumah ID lewat peta)."""
    return cfg.EVENT_LANGUAGE_BY_COUNTRY.get((country or "").strip().upper(), cfg.EVENT_LANGUAGE_DEFAULT)


def brave_search_target(location: Mapping[str, Any], country: str) -> tuple[str, str]:
    """(nama untuk query, bahasa) satu lokasi (N-12 2.1). Lokasi zh/ja dengan nama lokal memakai ``local_names[0]`` (aksara lokal);
    tanpa nama lokal memakai nama Latin dengan kata kunci Inggris (nama Latin + kata kunci CJK hampir tidak pernah cocok)."""
    lang = event_language(country)
    if lang in ("zh", "ja"):
        local = next((n for n in location.get("local_names") or [] if str(n).strip()), None)
        if local:
            return str(local).strip(), lang
        return str(location["kabupaten"]), cfg.EVENT_LANGUAGE_DEFAULT
    return str(location["kabupaten"]), lang


def brave_query(location: str, lang: str, style: str = cfg.BRAVE_QUERY_STYLE) -> str:
    """Query Brave per lokasi. Bentuk hasil SP-09: ``"<lokasi>" (kw1 OR kw2 ...)`` (``quoted_or``)."""
    keywords = [_term(k) for k in cfg.EVENT_KEYWORDS[lang][: cfg.BRAVE_MAX_KEYWORDS]]
    if style == "quoted_or":
        return f'"{location}" ({" OR ".join(keywords)})'
    if style == "plain_keywords":
        return f"{location} {' '.join(keywords)}"
    if style == "single_keyword":
        return f"{location} {keywords[0]}"
    raise ValueError(f"unknown BRAVE_QUERY_STYLE: {style!r}")


def brave_fallback_query(location: Mapping[str, Any], country: str, style: str = cfg.BRAVE_FALLBACK_STYLE) -> str:
    """Query ulang Brave (DEC-026): HANYA nama lokasi dalam tanda kutip, tanpa kata kunci. Nama = ``brave_search_target`` (nama lokal
    Hanzi/Kanji untuk CN/JP bila ada, selain itu nama Latin); tanda kutip di dalam nama dibuang."""
    if style != "name_only":
        raise ValueError(f"unknown BRAVE_FALLBACK_STYLE: {style!r}")
    name, _ = brave_search_target(location, country)
    clean = " ".join(name.replace('"', "").split())
    return f'"{clean}"'


def country_query(name: str, terms: Sequence[str]) -> str:
    """Query Brave per negara asal (N-17, DEC-032): ``"<Nama>" (kw1 OR "kw dua" OR "anti-dumping" ...)``, bentuk ``quoted_or`` sama dengan
    ``brave_query``. Nama selalu diberi kutip; istilah yang memuat spasi atau tanda baca (mis. tanda hubung) diberi kutip sebagai frasa;
    tanda kutip di dalam nama/istilah dibuang."""
    def term(value: str) -> str:
        clean = " ".join(value.replace('"', "").split())
        return f'"{clean}"' if re.search(r"[^\w]", clean) else clean

    clean_name = " ".join(name.replace('"', "").split())
    return f'"{clean_name}" ({" OR ".join(t for t in map(term, terms) if t)})'


# --------------------------------------------------------------------------- watchlist terbagi (N-16A, DEC-029)


def _code(value: Any) -> str:
    return str(value or "").upper()


def origin_countries(watchlist: Mapping[str, Any]) -> list[str]:
    """Kode ISO negara asal impor (≠ negara rumah) di ``watchlist``: dari ``countries`` dan dari negara ``locations``, urutan kemunculan."""
    home = _code(watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY)
    codes = [_code(c.get("code")) for c in watchlist.get("countries") or []]
    codes += [_code(l.get("country")) for l in watchlist.get("locations") or []]
    return [c for c in dict.fromkeys(codes) if c and c != home]


def partition_watchlist(watchlist: Mapping[str, Any], index: int, count: int) -> Json:
    """Bagian ke-``index`` (mulai 0) dari ``count`` bagian watchlist (DEC-029, ``docs/N-16_SCAN_TERBAGI.md`` bagian 3). Fungsi murni,
    deterministik: hasil tidak bergantung pada urutan input maupun pada ``index`` (setiap run menghitung pembagian yang sama).

    1. Negara asal (≠ negara rumah) dibagi UTUH beserta semua daerahnya. Urut prioritas (``po_exposure_share``; tanpa itu, prioritas
       daerah tertinggi negara itu, lalu kode ISO); tiap negara masuk ke bagian dengan negara asal paling sedikit, seri → daerah
       paling sedikit, seri → indeks terkecil.
    2. Daerah negara rumah, urut prioritas, masuk satu per satu ke bagian dengan daerah paling sedikit (semua daerah, termasuk milik
       negara asal yang sudah masuk), seri → indeks terkecil.
    3. Hasil = watchlist dengan ``locations`` dan ``countries`` milik bagian itu (urutan prioritas: daerah menurut ``priority``, negara
       menurut prioritas negara; seri menurut nama/kode), ``home_country`` dan kunci lain sama. Entri negara rumah ikut setiap bagian yang
       memegang daerah negara rumah (supaya tiap daerah punya negaranya); selain itu gabungan bagian = watchlist penuh dan tidak beririsan.
    4. ``count = 1`` mengembalikan ``watchlist`` apa adanya.

    ``ValueError`` bila ``count`` di luar 1..``MAX_SCAN_PARTITIONS`` atau ``index`` di luar 0..``count - 1``."""
    if not 1 <= count <= cfg.MAX_SCAN_PARTITIONS:
        raise ValueError(f"count must be 1..{cfg.MAX_SCAN_PARTITIONS}, got {count}")
    if not 0 <= index < count:
        raise ValueError(f"index must be 0..{count - 1}, got {index}")
    if count == 1:
        return watchlist  # type: ignore[return-value]

    home = _code(watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY)
    locations = sorted(watchlist.get("locations") or [],
                       key=lambda l: (-(l.get("priority") or 0.0), str(l.get("kabupaten") or ""), _code(l.get("country"))))
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in watchlist.get("countries") or []:
        entries.setdefault(_code(entry.get("code")), entry)
    by_country: dict[str, list[Mapping[str, Any]]] = {}
    for loc in locations:
        by_country.setdefault(_code(loc.get("country")), []).append(loc)

    def priority(code: str) -> float:
        share = (entries.get(code) or {}).get("po_exposure_share")
        if share is not None:
            return float(share)
        return max((l.get("priority") or 0.0 for l in by_country.get(code, [])), default=0.0)  # TL-22 tidak ada: prioritas daerah

    origin_order = sorted(origin_countries(watchlist), key=lambda c: (-priority(c), c))
    countries_in = [0] * count
    locations_in = [0] * count
    part_of_origin: dict[str, int] = {}
    for code in origin_order:
        target = min(range(count), key=lambda i: (countries_in[i], locations_in[i], i))
        part_of_origin[code] = target
        countries_in[target] += 1
        locations_in[target] += len(by_country.get(code, []))

    part_of_home: dict[int, int] = {}  # id(daerah negara rumah) -> bagian
    for loc in locations:
        if _code(loc.get("country")) == home:
            target = min(range(count), key=lambda i: (locations_in[i], i))
            part_of_home[id(loc)] = target
            locations_in[target] += 1

    def location_part(loc: Mapping[str, Any]) -> int:
        return part_of_home[id(loc)] if id(loc) in part_of_home else part_of_origin.get(_code(loc.get("country")), 0)

    mine = [loc for loc in locations if location_part(loc) == index]
    holds_home = any(_code(loc.get("country")) == home for loc in mine) or (not part_of_home and index == 0)
    country_rows = sorted(entries.values(), key=lambda c: (-priority(_code(c.get("code"))), _code(c.get("code"))))
    kept = [c for c in country_rows
            if (part_of_origin.get(_code(c.get("code"))) == index) or (_code(c.get("code")) == home and holds_home)]
    return {**watchlist, "locations": mine, "countries": kept}


def partition_fits_target(watchlist: Mapping[str, Any]) -> bool:
    """Bagian muat di ukuran TARGET pembagian (``PARTITION_SAFE_LOCATIONS`` daerah, ``PARTITION_SAFE_ORIGINS`` negara asal)."""
    return (len(watchlist.get("locations") or []) <= cfg.PARTITION_SAFE_LOCATIONS
            and len(origin_countries(watchlist)) <= cfg.PARTITION_SAFE_ORIGINS)


def smallest_fitting_partition_count(watchlist: Mapping[str, Any], above: int) -> int | None:
    """Jumlah bagian terkecil di atas ``above`` (sampai ``MAX_SCAN_PARTITIONS``) yang membuat SETIAP bagian muat di ukuran target
    (``partition_fits_target``); ``None`` bila tidak ada."""
    for count in range(above + 1, cfg.MAX_SCAN_PARTITIONS + 1):
        if all(partition_fits_target(partition_watchlist(watchlist, i, count)) for i in range(count)):
            return count
    return None


def partition_overflow(watchlist: Mapping[str, Any]) -> list[str]:
    """Pelanggaran BATAS KERAS satu bagian (dasar peringatan trace): daerah > ``MAX_BRAVE_EVENT_REQUESTS`` (satu query Brave per daerah)
    atau negara asal > ``PARTITION_SAFE_ORIGINS`` (pertanyaan sonar). Contoh ``["16 locations > 15", "8 origin countries > 7"]``; kosong
    bila tidak ada (N-17: 15 daerah + 7 negara asal muat dalam satu scan). Ukuran target pembagian (``PARTITION_SAFE_LOCATIONS``) tidak
    memicu peringatan."""
    found = []
    if (n := len(watchlist.get("locations") or [])) > cfg.MAX_BRAVE_EVENT_REQUESTS:
        found.append(f"{n} locations > {cfg.MAX_BRAVE_EVENT_REQUESTS}")
    if (n := len(origin_countries(watchlist))) > cfg.PARTITION_SAFE_ORIGINS:
        found.append(f"{n} origin countries > {cfg.PARTITION_SAFE_ORIGINS}")
    return found


def _by_priority(items: Sequence[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    return sorted(items, key=lambda i: -(i.get(key) or 0.0))  # sort stabil: seri mempertahankan urutan watchlist


def build_news_plan(
    watchlist: Mapping[str, Any], today: date, *, home_country: str | None = None, style: str | None = None,
    max_brave: int = cfg.MAX_BRAVE_EVENT_REQUESTS, max_country: int = cfg.MAX_BRAVE_COUNTRY_REQUESTS,
) -> Json:
    """Rencana query Brave, urut menurut prioritas, dalam anggaran 3.9 (N-17, DEC-032): ``{"brave", "brave_country", "skipped"}``.

    - ``brave``: satu query kejadian per lokasi (maks ``max_brave``), jendela ``EVENT_WINDOW_DAYS``; ``country`` = negara lokasi bila
      didukung Brave, selain itu ``ALL``; bahasa, nama (Latin atau ``local_names[0]``), kata kunci, dan ``search_lang`` mengikuti
      negara lokasi (DEC-025, ``brave_search_target``; Indonesia: ``search_lang`` tidak dikirim, SP-09).
    - ``brave_country`` (menggantikan Currents): (1) satu query kebijakan Inggris per negara ASAL IMPOR menurut prioritas; (2) satu query
      kebijakan berbahasa lokal bagi negara asal CN (``zh``) dan JP (``ja``) bila nama lokal tersedia; (3) satu query logistik/politik
      Inggris per negara asal. NEGARA RUMAH TIDAK DICARI sama sekali (DEC-025). Kebijakan memakai jendela ``POLICY_WINDOW_DAYS``,
      logistik ``EVENT_WINDOW_DAYS``. ``max_country`` hanya batas atas; yang tidak muat dicatat di ``skipped`` dengan ``target``
      ``"<ISO2>:<jenis>"`` (``policy``, ``policy-zh``, ``policy-ja``, ``logistics``).
    """
    home = (home_country or watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY).upper()
    style = style or cfg.BRAVE_QUERY_STYLE
    locations = [loc for loc in _by_priority(watchlist.get("locations") or [], "priority")
                 if loc.get("kabupaten") and loc.get("country")]
    countries = [c for c in _by_priority(watchlist.get("countries") or [], "po_exposure_share") if c.get("code")]
    skipped: list[Json] = []

    event_start = today - timedelta(days=cfg.EVENT_WINDOW_DAYS)
    brave: list[Json] = []
    for loc in locations:
        if len(brave) >= max_brave:
            skipped.append({"source": "BRAVE", "target": loc["kabupaten"], "reason": "BUDGET"})
            continue
        country = str(loc["country"]).upper()
        name, lang = brave_search_target(loc, country)
        brave.append({
            "query": brave_query(name, lang, style),
            "country": country if country in cfg.BRAVE_COUNTRY_SUPPORTED else "ALL",
            "search_lang": cfg.BRAVE_SEARCH_LANG[lang], "start": event_start, "end": today,
            "target": {"scope": "LOCATION", "kabupaten": loc["kabupaten"], "country": country},
        })

    policy_start = today - timedelta(days=cfg.POLICY_WINDOW_DAYS)
    brave_country: list[Json] = []

    def add(code: str, kind: str, topic: str, language: str, name: str, terms: Sequence[str], start: date) -> None:
        if len(brave_country) >= max_country:
            skipped.append({"source": "BRAVE", "target": f"{code}:{kind}", "reason": "BUDGET"})
            return
        # Kebijakan Inggris dan logistik: berita tentang suatu negara sering terbit di media negara lain -> ``ALL``. Kebijakan bahasa
        # lokal memakai negara itu sendiri bila didukung Brave.
        local = language != "en"
        brave_country.append({
            "query": country_query(name, terms), "kind": kind, "topic": topic,
            "country": code if local and code in cfg.BRAVE_COUNTRY_SUPPORTED else "ALL",
            "search_lang": cfg.BRAVE_SEARCH_LANG[language], "start": start, "end": today,
            "target": {"scope": "COUNTRY", "kabupaten": None, "country": code}})

    # Kebijakan (tarif dst.) hanya untuk negara asal impor: tarif luar terhadap barang negara rumah tidak mengubah biaya pembelian dari
    # supplier di negara rumah (R-14, DEC-017). DEC-025: negara rumah tidak dicari sama sekali (juga logistik).
    # Urutan: (1) kebijakan Inggris per negara asal, (2) kebijakan lokal CN/JP, (3) logistik per negara.
    origins = [c for c in countries if str(c["code"]).upper() != home]
    for country in origins:
        code = str(country["code"]).upper()
        add(code, "policy", "policy", "en", country.get("name") or cfg.country_name(code), cfg.COUNTRY_POLICY_TERMS["en"], policy_start)
    for country in origins:
        code = str(country["code"]).upper()
        language = cfg.COUNTRY_LOCAL_LANGUAGE_BY_COUNTRY.get(code)
        local_name = cfg.local_country_name(code, language) if language else None
        if language and local_name:
            add(code, f"policy-{language}", "policy", language, local_name, cfg.COUNTRY_POLICY_TERMS[language], policy_start)
    for country in origins:
        code = str(country["code"]).upper()
        add(code, "logistics", "logistics", "en", country.get("name") or cfg.country_name(code), cfg.COUNTRY_LOGISTICS_TERMS_EN,
            event_start)
    return {"brave": brave, "brave_country": brave_country, "skipped": skipped}
