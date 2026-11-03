"""Konstanta umum pipeline berita (DEC-015, ``docs/NEWS_PIPELINE.md`` 3.3, 3.4, 3.9).

Hanya hal UMUM: kata kunci per bahasa, daftar domain, jendela waktu, anggaran, TTL cache, dan hasil spike SP-09. TIDAK ADA
nama kabupaten, supplier, part, atau isi skenario demo di sini (anti-hardcode 3.10, UT-21): semua target datang dari
watchlist (TL-06 / TL-22).

Nilai bertanda "SP-09" berasal dari uji 13 Okt 2026 (``docs/spike-results.md``) dan sudah diputuskan DS (DEC-017).
"""

from __future__ import annotations

import html
import os
import re
from functools import lru_cache

# --------------------------------------------------------------------------- jendela waktu dan negara rumah

EVENT_WINDOW_DAYS = 7  # kejadian lokal (target LOCATION)
ONGOING_EVENT_MAX_AGE_DAYS = 30  # DEC-021: kejadian lokal yang masih berlangsung boleh dimulai sebelum jendela, maks 30 hari
POLICY_WINDOW_DAYS = 14  # kebijakan dagang (target COUNTRY): pengumuman bisa beberapa hari sebelum berlaku
NEWS_HOME_COUNTRY = (os.environ.get("NEWS_HOME_COUNTRY") or "ID").strip().upper() or "ID"  # negara pabrik (konfigurasi produk)

# --------------------------------------------------------------------------- kata kunci umum (bukan per skenario)

# DEC-025 (N-12 2.1): bahasa kejadian mengikuti negara LOKASI yang dicari; negara di luar peta = Inggris. Hanya 4 bahasa.
EVENT_LANGUAGE_BY_COUNTRY = {"ID": "id", "CN": "zh", "JP": "ja"}
EVENT_LANGUAGE_DEFAULT = "en"
EVENT_KEYWORDS: dict[str, list[str]] = {
    "id": ["banjir", "longsor", "kebakaran", "gempa", "jalan putus", "mogok"],
    "en": ["flood", "landslide", "fire", "earthquake", "typhoon", "port congestion", "strike"],
    "zh": ["洪水", "山体滑坡", "火灾", "地震", "台风", "道路中断"],  # Hanzi sederhana
    # 浸水 (bukan 洪水): media Jepang menulis banjir lokal sebagai 浸水/冠水
    "ja": ["浸水", "土砂崩れ", "火災", "地震", "台風", "通行止め"],
}
# N-17 (DEC-032): Brave News Search mengambil alih query kebijakan dan logistik/politik per NEGARA ASAL IMPOR dari Currents. Satu query
# ``"<Negara>" (kw1 OR "kw dua" OR ...)`` per jenis (bentuk ``quoted_or`` sama dengan kejadian lokal). Bahasa lokal hanya untuk CN (zh) dan
# JP (ja); negara lain memakai Inggris. Frasa dikutip supaya tidak ada lagi kata tunggal seperti ``port`` (sumber derau Currents).
COUNTRY_POLICY_TERMS: dict[str, tuple[str, ...]] = {
    "en": ("tariff", "import duty", "anti-dumping", "export ban", "export control", "sanctions"),
    "zh": ("关税", "反倾销", "出口禁令", "出口管制"),
    "ja": ("関税", "反ダンピング", "輸出禁止", "輸出規制"),
}
COUNTRY_LOGISTICS_TERMS_EN = ("port strike", "port congestion", "port closure", "dock strike", "border closure", "protest", "unrest")
COUNTRY_LOCAL_LANGUAGE_BY_COUNTRY = {"CN": "zh", "JP": "ja"}
# DEC-023: kata kebijakan di JUDUL kandidat negara untuk urutan pemilihan halaman verifikasi (Indonesia + Inggris, daftar inti)
POLICY_TITLE_KEYWORDS = {
    "id": ("tarif", "bea masuk", "antidumping", "larangan ekspor"),
    "en": ("tariff", "import duty", "antidumping", "export ban"),
}

# --------------------------------------------------------------------------- domain

# Satu domain di daftar ini cukup untuk status VERIFIED (3.8). Boleh diubah DS (N-00 butir 5).
TRUSTED_DOMAINS = frozenset({
    "bnpb.go.id", "bmkg.go.id", "kemenkeu.go.id", "beacukai.go.id", "kemendag.go.id", "ustr.gov", "federalregister.gov",
    "reuters.com", "apnews.com", "antaranews.com", "kompas.com", "detik.com", "cnnindonesia.com", "tempo.co",
    "cnbcindonesia.com", "bisnis.com", "kontan.co.id", "thejakartapost.com",
})  # hanya untuk sinyal NEGARA (kebijakan/logistik); kejadian lokal memakai dua daftar di bawah (DEC-025)
# Allowlist ``search_domain_filter`` sonar: maks 20 domain (batas Perplexity), dicek tes.
SONAR_ALLOWLIST_TRADE = (
    "reuters.com", "apnews.com", "bloomberg.com", "cnbc.com", "ft.com", "scmp.com", "caixinglobal.com", "ustr.gov",
    "federalregister.gov", "cbp.gov", "kemendag.go.id", "kemenkeu.go.id", "beacukai.go.id", "bisnis.com",
    "kontan.co.id", "cnbcindonesia.com", "thejakartapost.com", "nikkei.com", "bangkokpost.com",
)
SONAR_ALLOWLIST_MAX = 20

# Jenis risiko yang boleh berlingkup NEGARA (kebijakan/logistik/politik). Bahaya fisik (banjir, longsor, kebakaran, gempa) harus
# berlingkup LOKASI di watchlist (uji live SP-10 13 Okt: berita bencana di luar watchlist menghasilkan sinyal COUNTRY palsu).
COUNTRY_SCOPE_RISK_TYPES = frozenset({"TARIFF", "POLITICAL", "LOGISTICS"})

# DEC-025 (N-12 2.5, K6; mengganti DEC-018 K3): satu domain di salah satu dari dua daftar ini cukup untuk VERIFIED kejadian LOKAL;
# kejadian lokal selain itu wajib >= 2 domain independen. Setiap entri harus memenuhi ``registered_domain(d) == d`` (dicek tes).
# Lembaga resmi kebencanaan, cuaca, gempa, sumber daya air. Disusun 16 Okt 2026; SP-15 ``--check-domains`` memeriksa keterjangkauan.
OFFICIAL_HAZARD_DOMAINS = frozenset({
    "bnpb.go.id", "bmkg.go.id",  # ID
    "cma.gov.cn", "nmc.cn", "mem.gov.cn", "mwr.gov.cn", "cenc.ac.cn",  # CN
    "jma.go.jp", "bousai.go.jp", "fdma.go.jp",  # JP
    "ndma.gov.in", "imd.gov.in", "cwc.gov.in", "ndrf.gov.in", "incois.gov.in",  # IN
    "nea.gov.sg", "weather.gov.sg", "pub.gov.sg", "scdf.gov.sg",  # SG
    "usgs.gov", "weather.gov", "noaa.gov", "fema.gov",  # US
    "metoffice.gov.uk", "bgs.ac.uk",  # GB
    "bom.gov.au", "ga.gov.au", "nema.gov.au",  # AU
    "weather.gc.ca", "nrcan.gc.ca",  # CA
    "geonet.org.nz", "metservice.com", "civildefence.govt.nz",  # NZ
    "met.ie",  # IE
    "ndrrmc.gov.ph", "dost.gov.ph",  # PH (dost.gov.ph = induk PAGASA/PHIVOLCS, kompromi)
    "ahacentre.org", "gdacs.org", "reliefweb.int",  # regional/global
})
# Kantor berita nasional dan lembaga penyiaran publik (termasuk media pemerintah Tiongkok (CN), disetujui DS).
TRUSTED_PUBLIC_NEWS_DOMAINS = frozenset({
    "antaranews.com", "tvri.go.id", "rri.co.id",  # ID
    "ptinews.com", "ddnews.gov.in", "newsonair.gov.in", "prasarbharati.gov.in",  # IN
    "nhk.or.jp", "kyodonews.net", "jiji.com",  # JP
    "xinhuanet.com", "news.cn", "cctv.com", "cgtn.com",  # CN
    "channelnewsasia.com",  # SG
    "apnews.com", "npr.org", "pbs.org",  # US
    "bbc.com", "bbc.co.uk", "reuters.com",  # GB / global
    "abc.net.au", "cbc.ca", "rnz.co.nz", "rte.ie", "pna.gov.ph",  # AU, CA, NZ, IE, PH
})

# --------------------------------------------------------------------------- penilaian bukti per artikel (DEC-018, cara FAME)

# Jenis kejadian/tindakan di pertanyaan ya/tidak per bukti (Inggris, umum, tanpa nama tempat).
JUDGE_EVENT_CLASSES_EN = ("a flood, landslide, fire, earthquake, or a road, port or transport disruption "
                          "(closure, congestion or strike)")
JUDGE_POLICY_CLASSES_EN = "a tariff, import duty, anti-dumping duty, export ban, export restriction or trade sanction"
JUDGE_LOGISTICS_CLASSES_EN = "a port closure, port congestion, transport strike, border closure or political unrest"
JUDGE_MAX_ITEMS_PER_CALL = 9  # bukti per panggilan Claude (urutan E1..En); DEC-028 (N-15): 8 -> 9
JUDGE_MAX_PARALLEL_CALLS = 6  # N-17 (DEC-032): 4 -> 6 (DEC-028: 3 -> 4). Turunkan ke 4 bila AI Core menolak 6 panggilan bersamaan (sisa bukti NOT_JUDGED)
# Bukti yang dinilai Claude per run = satu gelombang (6 paralel x 9 = 54). Lebih dari ini dipilih bergiliran per target (N-09c, DEC-020).
JUDGE_MAX_ITEMS_PER_RUN = JUDGE_MAX_ITEMS_PER_CALL * JUDGE_MAX_PARALLEL_CALLS
# DEC-022: dari slot penilaian (24, DEC-028 36, N-17 54), 14 (7 negara x 2) dicadangkan untuk bukti COUNTRY (kebijakan/logistik); yang tidak terpakai kembali ke kolam.
JUDGE_COUNTRY_RESERVE = 14
JUDGE_MAX_TEXT_CHARS = 700  # judul + lead + snippet per bukti; isi halaman tidak dikirim (DEC-015)
# Jenis risiko yang sah menurut jenis pertanyaan; jawaban "ya" di luar daftar ini dibatalkan kode (RISK_TYPE_MISMATCH).
RISK_TYPES_BY_QUESTION = {
    "event": frozenset({"FLOOD", "LANDSLIDE", "FIRE", "EARTHQUAKE", "LOGISTICS"}),
    "policy": frozenset({"TARIFF", "POLITICAL"}),
    "logistics": frozenset({"LOGISTICS", "POLITICAL"}),
}
MAX_FUTURE_DAYS = 60  # effective_date / tanggal berlaku kebijakan maksimal ke depan (3.8 butir 3)
SEVERITY_RANGE = (1, 5)  # severity sah untuk sinyal LOCATION; sinyal COUNTRY selalu null (DEC-019)

# --------------------------------------------------------------------------- hasil SP-09 (diputuskan DS, DEC-017)

# Brave: bentuk query "<lokasi>" (kw1 OR kw2 ...) menemukan kejadian tanpa alarm palsu (SP-09).
BRAVE_QUERY_STYLE = "quoted_or"
# DEC-026 (N-13, bukti SP-16 16 Okt: Brave mengabaikan nama lokasi bila beritanya sedikit, 0 dari 16 hasil menyebut lokasi): bila tidak
# ada hasil yang menyebut lokasi, satu query ulang ``"<nama>"`` tanpa kata kunci, maks ``BRAVE_MAX_FALLBACKS`` per scan, di dalam
# ``MAX_BRAVE_REQUESTS``. N-17 (DEC-032): 3 -> 5; query negara tidak pernah diulang.
BRAVE_FALLBACK_STYLE = "name_only"
BRAVE_MAX_FALLBACKS = 5
# Daftar ``country`` yang diterima API Brave (dibaca dari galat 422 Brave, 13 Okt 2026); selain itu pakai ``ALL``.
BRAVE_COUNTRY_SUPPORTED = frozenset({
    "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "GR", "HK", "IN", "ID", "IT", "JP", "KR", "MY",
    "MX", "NL", "NZ", "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE", "CH", "TW", "TR", "GB", "US",
})
# Brave menolak ``search_lang=id`` (422). Bahasa Indonesia cukup lewat kata kunci; ``None`` = parameter tidak dikirim.
# DEC-025: Brave memakai ``zh-hans`` (Mandarin Hanzi sederhana) dan ``jp`` (Jepang; BUKAN ``ja``) untuk CN/JP. Kedua kode diuji SP-15
# (B1, B2); bila Brave menolaknya (422) ubah nilainya menjadi ``None`` (parameter tidak dikirim), tanpa cabang kode.
BRAVE_SEARCH_LANG = {"id": None, "en": "en", "zh": "zh-hans", "ja": "jp"}
BRAVE_MAX_KEYWORDS = 6  # kata kunci dalam satu query Brave (batas query Brave: 400 karakter / 50 kata)
SONAR_PARAMS_ENABLED = frozenset({"search_after_date_filter", "search_domain_filter", "web_search_options"})
SONAR_RESPONSE_FORMAT = True

# --------------------------------------------------------------------------- anggaran per run (3.9, G-05)

# N-17 (DEC-032): Brave = kejadian per daerah + query ulang nama + kebijakan/logistik per negara asal; angka turunan dihitung dari konstanta.
MAX_BRAVE_EVENT_REQUESTS = 15  # satu query kejadian per daerah (15 daerah muat dalam satu scan); di atas batas -> BUDGET
MAX_BRAVE_COUNTRY_REQUESTS = 16  # 7 kebijakan Inggris + 2 kebijakan lokal (CN, JP) + 7 logistik; di atas batas -> BUDGET
MAX_BRAVE_REQUESTS = MAX_BRAVE_EVENT_REQUESTS + BRAVE_MAX_FALLBACKS + MAX_BRAVE_COUNTRY_REQUESTS  # 15 + 5 + 16 = 36
MAX_SONAR_QUESTIONS = 7  # satu pertanyaan per negara asal (N-17: 3 -> 7)
# Saklar darurat bila AI Core menolak pertanyaan sonar bersamaan (429): turunkan; pertanyaan yang tidak sempat mulai dalam jatah -> TIME_BUDGET.
SONAR_MAX_PARALLEL = MAX_SONAR_QUESTIONS
# DEC-029 (N-16A): scan terbagi. N-17 menaikkan ukuran aman sehingga 15 daerah + 7 negara asal muat dalam SATU scan (``count`` = 1).
MAX_SCAN_PARTITIONS = 3  # nilai ``scan_partition.count`` tertinggi yang diterima
PARTITION_SAFE_LOCATIONS = MAX_BRAVE_EVENT_REQUESTS  # ukuran TARGET daerah per bagian (pembagian dan saran SCAN_PARTITIONS). Peringatan trace memakai batas keras: daerah > MAX_BRAVE_EVENT_REQUESTS
PARTITION_SAFE_ORIGINS = MAX_SONAR_QUESTIONS  # negara asal impor per bagian: target sekaligus batas keras peringatan (satu pertanyaan sonar per negara)
MAX_VERIFY_PAGES = 48  # N-17 (DEC-032; sebelumnya 36, DEC-027): 15 daerah x 2 domain + cadangan negara ~ 40-45
VERIFY_PER_TARGET_FIRST_PASS = 3  # putaran pertama: maks halaman per target (DEC-017)
MAX_LOCATIONS_PER_ARTICLE = 3  # satu berita boleh dihitung untuk maks 3 lokasi watchlist yang disebutnya (N-09c, DEC-020)
VERIFY_COUNTRY_RESERVE = 14  # halaman yang dicadangkan untuk kandidat COUNTRY (7 negara x 2); kembali ke kolam bila tidak terpakai (DEC-022; N-17: 9 -> 14)
# DEC-027 (N-14): gelombang isi ulang di ``verify_dates``. Lokasi dengan < 2 domain lolos (IN_WINDOW) yang masih punya kandidat belum dibuka
# mendapat halaman pengganti dari domain yang belum dicoba, dalam tenggat verifikasi yang sama; negara tidak diisi ulang.
VERIFY_REFILL_PER_TARGET = 2  # pengganti per lokasi
VERIFY_REFILL_MAX_PAGES = 8  # pengganti per scan, bergiliran antar lokasi yang kekurangan
VERIFY_REFILL_MIN_REMAINING_S = 3  # gelombang isi ulang tidak dijalankan bila sisa waktu verifikasi kurang dari ini
# N-17 (8.1): TL-06 + TL-22 + TL-05 (3 panggilan tetap) + Brave 36 + sonar 7 (+1 ulang) + verifikasi 1 batch = 3 + 36 + 8 + 1 = 48.
# Batas G-05 untuk scan_external = 50 (graphs/common.py ``MAX_TOOL_CALLS_BY_TASK``, sisa 2); graph lain tetap 25.
SCAN_FIXED_TOOL_CALLS = 3
MAX_TOOL_CALLS_PER_SCAN = SCAN_FIXED_TOOL_CALLS + MAX_BRAVE_REQUESTS + (MAX_SONAR_QUESTIONS + 1) + 1
# Laju Brave (N-17 bagian 4). ``BRAVE_RATE_MODE``: "auto" = baca ``X-RateLimit-*`` dari setiap respons; "safe" = saklar darurat, selalu serial
# dengan jeda ``BRAVE_SAFE_INTERVAL_S``. Sebelum respons pertama (dan bila header hilang/rusak) klien memakai mode aman.
BRAVE_RATE_MODE = "auto"
BRAVE_MAX_CONCURRENCY = 4  # request Brave bersamaan (mode auto, batas per detik >= 2)
BRAVE_MIN_INTERVAL_S = 0.05  # jarak antar-MULAI request di mode auto (batas per detik >= 2)
BRAVE_SAFE_INTERVAL_S = 1.1  # mode aman: serial, jeda 1,1 s (paket lama 1 request/detik)
BRAVE_429_RETRY_MAX_WAIT_S = 2  # tunggu ``X-RateLimit-Reset`` (maks ini) sebelum satu ulangan 429; juga batas tunggu saat sisa per detik = 0
NEWS_CACHE_TTL_S = 30 * 60  # Brave
SONAR_CACHE_TTL_S = 60 * 60

# --------------------------------------------------------------------------- bulan (Inggris + Indonesia) untuk pembaca tanggal

# DEC-025 (N-12 2.4): pola tanggal teks tambahan menurut NEGARA TARGET kandidat. ``cjk`` = ``2026年10月16日``; ``reiwa`` =
# ``令和8年10月16日`` (tahun = REIWA_BASE_YEAR + N, ``元`` = 1). Pola Indonesia/Inggris selalu aktif untuk semua negara (IN
# ``16 October 2026`` sudah didukung). DEC-026 (N-13 4): ``iso_datetime`` = ``2026-10-14 14:34`` (tanggal angka YANG DIIKUTI JAM; jam
# wajib supaya nomor artikel/versi/ID tidak terbaca sebagai tanggal), hanya CN/JP (bukti SP-16: berita CN tanpa meta tanggal).
TEXT_DATE_STYLES_BY_COUNTRY = {"CN": ("cjk", "iso_datetime"), "JP": ("cjk", "reiwa", "iso_datetime")}
REIWA_BASE_YEAR = 2018

MONTHS_EN = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
             "september": 9, "october": 10, "november": 11, "december": 12}
MONTHS_ID = {"januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
             "september": 9, "oktober": 10, "november": 11, "desember": 12}


# --------------------------------------------------------------------------- nama negara (pycountry; bukan tabel manual)


@lru_cache(maxsize=None)
def country_name(code: str) -> str:
    """Nama Inggris dari kode ISO2 (``pycountry``); kode tak dikenal dikembalikan apa adanya."""
    import pycountry

    country = pycountry.countries.get(alpha_2=(code or "").strip().upper())
    return getattr(country, "common_name", None) or country.name if country else (code or "")


@lru_cache(maxsize=None)
def local_country_name(code: str, lang: str) -> str | None:
    """Nama negara dalam bahasa ``lang`` (``zh`` -> ``zh_CN``, ``ja``) dari terjemahan bawaan ``pycountry`` (katalog ``iso3166-1``),
    tanpa tabel manual (DEC-025). ``None`` bila negara/terjemahan tidak ada (terjemahan sama dengan nama Inggris dianggap tidak ada)."""
    import gettext

    import pycountry

    country = pycountry.countries.get(alpha_2=(code or "").strip().upper())
    locale = {"zh": "zh_CN", "ja": "ja"}.get(lang, lang)
    if not country:
        return None
    try:
        catalog = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=[locale])
    except OSError:
        return None
    translated = catalog.gettext(country.name).strip()
    return translated if translated and translated != country.name else None


@lru_cache(maxsize=None)
def country_code(value: str) -> str | None:
    """Kode ISO2 dari kode atau nama negara (tanpa peka huruf besar); ``None`` bila tak dikenali."""
    import pycountry

    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 2 and pycountry.countries.get(alpha_2=text.upper()):
        return text.upper()
    try:
        return pycountry.countries.lookup(text).alpha_2
    except LookupError:
        return None


# --------------------------------------------------------------------------- kata kebijakan di judul (DEC-023, N-11)


def _policy_title_pattern() -> re.Pattern[str]:
    """Satu regex dari ``POLICY_TITLE_KEYWORDS``: kata/frasa utuh (``\\b``); kata terakhir boleh berakhiran ``s``/``ies`` (Inggris,
    ``duty`` -> ``duties``) atau ``nya`` (Indonesia). Tanpa stemming lain."""
    alternatives = []
    for lang, words in POLICY_TITLE_KEYWORDS.items():
        for phrase in words:
            *head, last = phrase.split(" ")
            if lang == "en":
                tail = f"(?:{re.escape(last)}|{re.escape(last[:-1])}ies)" if last.endswith("y") else f"{re.escape(last)}s?"
            else:
                tail = f"{re.escape(last)}(?:nya)?"
            alternatives.append(r"\s+".join([*(re.escape(w) for w in head), tail]))
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


_POLICY_TITLE_RE = _policy_title_pattern()
_HYPHEN_BETWEEN_LETTERS = re.compile(r"(?<=[^\W\d_])[-‐‑](?=[^\W\d_])")


def title_has_policy_word(title: str) -> bool:
    """DEC-023: ``True`` bila judul memuat kata kebijakan (``POLICY_TITLE_KEYWORDS``, Indonesia + Inggris). Fungsi murni (G-06).

    Normalisasi: entitas HTML dibuka, huruf kecil, spasi dirapatkan, tanda hubung di antara huruf menjadi spasi, lalu
    ``anti dumping`` menjadi ``antidumping``."""
    text = re.sub(r"\s+", " ", html.unescape(title or "").lower())
    text = _HYPHEN_BETWEEN_LETTERS.sub(" ", text).replace("anti dumping", "antidumping")
    return _POLICY_TITLE_RE.search(text) is not None


# --------------------------------------------------------------------------- jatah waktu per sumber (DEC-017, G-05: <= 120 s)

# Request baru hanya dimulai bila ``waktu_node + timeout_baca <= jatah``; target yang tidak sempat dicari dicatat ``TIME_BUDGET``.
BRAVE_TIMEOUT_S = (3, 5)  # (connect, read)
# N-17 (DEC-032): 22 -> 30 s. Paket Search (~50 rps) dengan 4 paralel: 36 x ~0,7 s / 4 ~ 7 s; paket lama (1 rps): ~25 request muat, sisanya
# ``TIME_BUDGET`` menurut urutan prioritas rencana. Brave dan sonar berjalan bersamaan, jadi jatah ini tidak menambah jatah sumber lain
# (lihat ``worst_case_scan_seconds``).
BRAVE_TIME_BUDGET_S = 30
SONAR_SCAN_TIMEOUT_S = 20  # satu pertanyaan; pertanyaan berjalan paralel, tanpa retry SDK
SONAR_TIME_BUDGET_S = 22
FETCH_TIMEOUT_S = (3, 6)  # pengambilan halaman verifikasi
VERIFY_TIME_BUDGET_S = 10
EXTRACT_READ_TIMEOUT_S = 30
EXTRACT_RETRY_IF_ELAPSED_LE_S = 45  # percobaan kedua ekstraksi hanya bila run baru berjalan <= 45 s
RUN_OVERHEAD_S = 8  # watchlist, rencana, saring, filter, simpan, trace
MAX_RUN_SECONDS = 120  # G-05
WORST_CASE_LIMIT_S = 112  # sisa 8 s di bawah G-05

# Tanya ulang severity (N-09c, DEC-020): jatah SENDIRI, terpisah dari penilaian utama.
SEVERITY_RETRY_MAX_CALLS = 1  # satu panggilan per run
SEVERITY_RETRY_MAX_ITEMS = 8  # bukti per panggilan; sisanya tetap INCOMPLETE
SEVERITY_RETRY_READ_TIMEOUT_S = 20  # tanpa retry SDK (max_attempts = 1)
SEVERITY_RETRY_LATEST_START_S = MAX_RUN_SECONDS - RUN_OVERHEAD_S - SEVERITY_RETRY_READ_TIMEOUT_S  # 92: selesai <= 112 s


def worst_case_scan_seconds() -> int:
    """Waktu terburuk satu scan menurut jatah di atas (tanpa jatah node, run bisa melewati 120 s dan kehilangan semua bukti).

    N-17 (DEC-032): GDELT dan Currents dihapus. Brave dan sonar berjalan bersamaan (``join_sources`` sebelum saring), jadi yang dihitung
    yang TERBESAR dari keduanya, bukan jumlahnya: 8 + max(30, 22) + 10 = 48 sebelum ekstraksi."""
    before_extract = RUN_OVERHEAD_S + max(BRAVE_TIME_BUDGET_S, SONAR_TIME_BUDGET_S) + VERIFY_TIME_BUDGET_S
    return max(before_extract + EXTRACT_READ_TIMEOUT_S,  # satu percobaan ekstraksi
               EXTRACT_RETRY_IF_ELAPSED_LE_S + 2 * EXTRACT_READ_TIMEOUT_S,  # dua percobaan, mulai paling lambat di detik ke-45
               SEVERITY_RETRY_LATEST_START_S + SEVERITY_RETRY_READ_TIMEOUT_S)  # tanya ulang severity, mulai paling lambat di detik ke-92
