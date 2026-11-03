"""Penilaian bukti per artikel, cara FAME (DEC-018, ``docs/N-09_PENILAIAN_BUKTI_FAME.md``).

LLM tidak lagi menyusun sinyal. Untuk setiap bukti ``IN_WINDOW`` KODE menyusun satu pertanyaan sempit (jenis kejadian, tempat,
rentang tanggal; atau tindakan dagang untuk negara asal) dan Claude hanya menjawab ya/tidak dengan satu alasan dari daftar tetap,
dari judul + lead + snippet bukti itu (isi halaman tidak dikirim, DEC-015). Kemudian KODE:

- memeriksa ulang jawaban (``validate_judgements``): alasan tidak konsisten, jenis risiko di luar jenis pertanyaan, tanggal kejadian
  yang frasanya tidak tertulis di bukti atau di luar periode -> "tidak" (G-06, G-08);
- menyusun sinyal dari jawaban "ya" per (lingkup, tempat, jenis risiko) (``signals_from_judgements``); lokasi/negara selalu dari
  target kandidat (bukan dari LLM), URL dari bukti (G-02).

Modul ini murni: tidak mengimpor SDK model dan tidak mengimpor ``graphs/``. Konten luar selalu di dalam ``<external_content>`` dan
tag penutup palsu di dalamnya dinetralkan (G-03). Pertanyaan berada DI LUAR ``<external_content>`` (teks tepercaya buatan kode).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Collection, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from tools import news_config as cfg
from tools.sonar_policy import long_date

Json = dict[str, Any]

RiskType = Literal["FLOOD", "LANDSLIDE", "FIRE", "EARTHQUAKE", "LOGISTICS", "TARIFF", "POLITICAL"]
Reason = Literal[
    "NEW_OR_ONGOING_EVENT", "MEASURE_ADOPTED_OR_ANNOUNCED",  # alasan "ya"
    "RECAP_OR_STATISTICS", "FOLLOW_UP_OR_RECOVERY", "FORECAST_OR_WARNING", "HISTORY_OR_ANNIVERSARY",
    "PROPOSAL_OR_INVESTIGATION", "ROUTINE_OR_ENFORCEMENT", "OPINION_OR_EXPLAINER", "OTHER_PLACE_OR_PERIOD"]
YES_REASONS = frozenset({"NEW_OR_ONGOING_EVENT", "MEASURE_ADOPTED_OR_ANNOUNCED"})

NOT_ANSWERED = "NOT_ANSWERED"
_EXTERNAL_TAG = re.compile(r"</?\s*external_content[^>]*>", re.IGNORECASE)
_PLACE_PREFIX = re.compile(r"^(kabupaten|kab\.?|kota)\s+", re.IGNORECASE)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


# --------------------------------------------------------------------------- skema jawaban Claude (5.3)


SEVERITY_DESCRIPTION = ("Required for every yes answer to a question about a place: integer 1-5 from the severity rubric. "
                        "Null for questions about a country, and for no answers.")


DOWNTIME_DESCRIPTION = ("Whole days the disruption is expected to last, only if the item states it. Null otherwise; never guess.")


def normalize_downtime_days(value: Any) -> tuple[int | None, bool]:
    """``downtime_days`` jawaban Claude -> ``(bilangan bulat >= 1 atau None, dibuang)``. ``None`` = tidak disebut (tidak dibuang).
    Selain bilangan bulat >= 1 (``bool``, pecahan, nol/negatif, teks bukan angka, NaN) -> ``(None, True)``; angka bulat dalam bentuk
    ``"14"`` atau ``14.0`` diterima (kontrak TL-05 v1.11: int >= 1 atau null)."""
    if value is None:
        return None, False
    number: int | None = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float):
        number = int(value) if math.isfinite(value) and value == int(value) else None
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        number = int(value.strip())
    return (number, False) if number is not None and number >= 1 else (None, True)


def _require_severity(schema: dict[str, Any]) -> None:
    """JSON schema yang dikirim ke model: ``severity`` masuk ``required`` (boleh ``null``). Parser Python tetap toleran
    (default ``None``): satu jawaban tanpa field tidak boleh menggagalkan 8 bukti (N-09c, DEC-020)."""
    required = schema.setdefault("required", [])
    if "severity" not in required:
        required.append("severity")


class EvidenceJudgement(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_severity)

    id: str
    answer: bool
    reason: Reason
    risk_type: RiskType | None = None
    # rentang 1-5 diperiksa KODE per bukti (validate_judgements), bukan skema: satu nilai aneh tidak menggagalkan 8 bukti
    severity: int | None = Field(default=None, description=SEVERITY_DESCRIPTION)
    event_date: str | None = None
    event_date_text: str | None = None
    kecamatan: list[str] = Field(default_factory=list)
    category: str | None = None
    # A-18 (DEC-037): lama gangguan hanya bila bukti menyebutnya. Bentuk longgar (angka/teks) supaya satu nilai aneh seperti "14.5"
    # tidak menggagalkan 8 bukti; ``normalize_downtime_days`` memutuskan, bukan skema.
    downtime_days: int | float | str | None = Field(default=None, description=DOWNTIME_DESCRIPTION)
    summary: str | None = None


class EvidenceJudgements(BaseModel):
    judgements: list[EvidenceJudgement]


class SeverityAnswer(BaseModel):
    id: str
    severity: int | None = None


class SeverityAnswers(BaseModel):
    """Jawaban tanya ulang severity (N-09c): hanya ``id`` dan ``severity``; field lain tidak ada di skema."""

    answers: list[SeverityAnswer]


# --------------------------------------------------------------------------- pembantu teks (G-03) dan nama tempat


def sanitize_external(text: Any) -> str:
    """Satu baris tanpa tag ``<external_content>``: konten luar tidak boleh menutup/membuka blok sendiri (G-03)."""
    return " ".join(_EXTERNAL_TAG.sub("[removed tag]", str(text)).split())


def core_name(place: str) -> str:
    return _PLACE_PREFIX.sub("", (place or "").strip())


_CJK = re.compile(r"[\u3005\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")  # Han, Hiragana, Katakana


def has_cjk(name: str) -> bool:
    """``True`` bila ``name`` memuat aksara Han, Hiragana, atau Katakana."""
    return _CJK.search(name or "") is not None


def _nfkc(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _name_pattern(needle: str) -> re.Pattern[str]:
    """Nama Latin: kata utuh (batas karakter kata). Nama beraksara CJK: substring (teks CJK tidak dipisah spasi, jadi ``在甲乙市发生`` harus
    cocok); ``needle`` sudah dinormalkan NFKC."""
    if has_cjk(needle):
        return re.compile(re.escape(needle))
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)", re.IGNORECASE)


def mentions_place(name: str, *texts: str) -> bool:
    """Nama tempat (tanpa awalan Kab./Kabupaten/Kota) disebut tanpa peka huruf besar: kata utuh untuk nama Latin, substring untuk
    nama beraksara CJK (DEC-025). Teks dan nama dinormalkan NFKC (angka/huruf lebar penuh)."""
    needle = _nfkc(core_name(name))
    if not needle:
        return False
    pattern = _name_pattern(needle)
    return any(pattern.search(_nfkc(t)) for t in texts)


def location_names(location: Mapping[str, Any]) -> list[str]:
    """Nama Latin (``kabupaten``) lalu ``local_names`` lokasi watchlist, tanpa kosong dan tanpa duplikat (DEC-025)."""
    names = [str(location.get("kabupaten") or ""), *(str(n) for n in location.get("local_names") or [])]
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


def mentions_location(location: Mapping[str, Any], *texts: str) -> bool:
    """Lokasi watchlist disebut bila nama Latin ATAU salah satu ``local_names`` muncul (DEC-025)."""
    return any(mentions_place(n, *texts) for n in location_names(location))


def mentioned_locations(locations: Sequence[Mapping[str, Any]], text: str) -> list[Mapping[str, Any]]:
    """Lokasi watchlist yang disebut ``text``, urut menurut urutan ``locations`` (prioritas). Aturan umum tanpa daftar nama
    (N-09c, DEC-020): nama terpanjang dicocokkan lebih dulu dan rentang teks yang sudah dipakai diabaikan, sehingga nama pendek
    yang hanya muncul sebagai bagian nama panjang watchlist lain tidak dihitung ("Alfa Barat" tidak menyebut "Alfa"; "Alfa dan
    Alfa Barat" menyebut keduanya). Lokasi dengan nama inti sama dihitung bersama. DEC-025: setiap lokasi dicocokkan lewat nama
    Latin dan ``local_names`` (CJK = substring setelah NFKC); aturan "terpanjang dulu" berlaku untuk semua nama bersama."""
    by_name: dict[str, list[int]] = {}
    for i, loc in enumerate(locations):
        for name in location_names(loc):
            key = _nfkc(core_name(name)).lower()
            if key and i not in by_name.setdefault(key, []):
                by_name[key].append(i)
    haystack = _nfkc(text)
    taken: list[tuple[int, int]] = []
    named: set[int] = set()
    for key in sorted(by_name, key=len, reverse=True):  # stabil: panjang sama menurut urutan watchlist
        spans = [m.span() for m in _name_pattern(key).finditer(haystack)]
        free = [sp for sp in spans if not any(sp[0] < end and start < sp[1] for start, end in taken)]
        if free:
            named.update(by_name[key])
            taken.extend(free)
    return [loc for i, loc in enumerate(locations) if i in named]


def country_names(code: str) -> list[str]:
    """Nama negara untuk pencocokan teks (pycountry: nama, nama umum, nama resmi)."""
    import pycountry

    country = pycountry.countries.get(alpha_2=(code or "").upper())
    if not country:
        return []
    names = [getattr(country, a, None) for a in ("name", "common_name", "official_name")]
    return list(dict.fromkeys(n for n in names if n))


def mentions_country(code: str, *texts: str) -> bool:
    """Nama negara (kata utuh, tanpa peka huruf besar) atau kodenya (huruf besar, kata utuh) disebut."""
    for text in texts:
        for name in country_names(code):
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text or "", flags=re.IGNORECASE):
                return True
        if re.search(rf"(?<![A-Za-z]){re.escape(code.upper())}(?![A-Za-z])", text or ""):
            return True
    return False


# --------------------------------------------------------------------------- pertanyaan dan teks per bukti (6.4 / 4.3)


def question_kind(evidence: Mapping[str, Any]) -> Literal["event", "policy", "logistics"]:
    """``LOCATION`` -> ``event``; ``COUNTRY`` dengan topik ``policy`` -> ``policy``; ``COUNTRY`` lainnya -> ``logistics``."""
    if (evidence.get("target") or {}).get("scope") != "COUNTRY":
        return "event"
    return "policy" if "policy" in (evidence.get("topics") or []) else "logistics"


def local_name_of(target: Mapping[str, Any], watchlist: Mapping[str, Any]) -> str | None:
    """Nama lokal pertama (``local_names[0]``) lokasi watchlist yang cocok dengan ``target`` (kabupaten + negara); ``None`` bila tidak ada."""
    for loc in watchlist.get("locations") or []:
        if (loc.get("kabupaten") == target.get("kabupaten")
                and str(loc.get("country") or "").upper() == str(target.get("country") or "").upper()):
            return next((str(n).strip() for n in loc.get("local_names") or [] if str(n).strip()), None)
    return None


def place_label(target: Mapping[str, Any], watchlist: Mapping[str, Any]) -> str:
    """``"<kabupaten>"`` atau ``"<kabupaten> / <local_names[0]>"`` (DEC-025): judul berbahasa Mandarin/Jepang bisa dicocokkan Claude."""
    name = str(target.get("kabupaten"))
    local = local_name_of(target, watchlist)
    return f"{name} / {local}" if local else name


def build_question(evidence: Mapping[str, Any], watchlist: Mapping[str, Any], today: date) -> str:
    """Pertanyaan ya/tidak untuk satu bukti; semua nama dari target kandidat dan watchlist, tanggal dalam huruf (tanpa URL)."""
    target = evidence["target"]
    country = cfg.country_name(str(target.get("country") or ""))
    kind = question_kind(evidence)
    if kind == "event":
        start = today - timedelta(days=cfg.EVENT_WINDOW_DAYS)
        return (f"Does this item report {cfg.JUDGE_EVENT_CLASSES_EN} that happened or is still happening in "
                f"{place_label(target, watchlist)} ({country}) between {long_date(start)} and {long_date(today)}?")
    start = today - timedelta(days=cfg.POLICY_WINDOW_DAYS)
    if kind == "policy":
        home = cfg.country_name(str(watchlist.get("home_country") or cfg.NEWS_HOME_COUNTRY))
        return (f"Does this item report {cfg.JUDGE_POLICY_CLASSES_EN}, adopted or announced between {long_date(start)} and "
                f"{long_date(today)}, that affects goods made in {country} and shipped to {home}?")
    return (f"Does this item report {cfg.JUDGE_LOGISTICS_CLASSES_EN} happening in {country} between {long_date(start)} and "
            f"{long_date(today)}?")


def item_text(evidence: Mapping[str, Any]) -> str:
    """``Title:`` + ``Lead:`` (``description``, selain itu snippet) + ``Snippet:`` (bila berbeda), disanitasi dan dipotong ke
    ``JUDGE_MAX_TEXT_CHARS`` (K1). Isi halaman tidak pernah ikut."""
    title = sanitize_external(evidence.get("title") or "")
    description = sanitize_external(evidence.get("description") or "")
    snippet = sanitize_external(evidence.get("snippet") or "")
    lead = description or snippet
    lines = [f"Title: {title}"]
    if lead:
        lines.append(f"Lead: {lead}")
    if snippet and snippet != lead:
        lines.append(f"Snippet: {snippet}")
    text = "\n".join(lines)
    limit = cfg.JUDGE_MAX_TEXT_CHARS
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def user_message(items: Sequence[Mapping[str, Any]], watchlist: Mapping[str, Any], today: date) -> str:
    """Pesan user satu panggilan (5.2): pertanyaan di luar ``<external_content>``, artikel di dalamnya."""
    blocks = ["Answer the question for each item."]
    for e in items:
        blocks.append(
            f"[{e['id']}]\nQuestion: {build_question(e, watchlist, today)}\n<external_content>\n"
            f"Published: {e['published_at']} (verified) | Domain: {sanitize_external(e.get('domain') or '')}\n"
            f"{item_text(e)}\n</external_content>")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- memanggil Claude (5.4)


@dataclass
class JudgeRun:
    raw: list[EvidenceJudgement] = field(default_factory=list)  # semua jawaban, urutan potongan lalu urutan di dalam potongan
    calls: int = 0
    failed: int = 0


def chunk_evidence(evidence: Sequence[Mapping[str, Any]], size: int = cfg.JUDGE_MAX_ITEMS_PER_CALL) -> list[list[Mapping[str, Any]]]:
    """Potongan <= ``size`` bukti menurut urutan E1..En."""
    return [list(evidence[i:i + size]) for i in range(0, len(evidence), size)]


def judge_chunk(llm: Any, system_prompt: str, chunk: Sequence[Mapping[str, Any]], watchlist: Mapping[str, Any],
                today: date) -> list[EvidenceJudgement]:
    result = llm.with_structured_output(EvidenceJudgements).invoke(
        [("system", system_prompt), ("human", user_message(chunk, watchlist, today))])
    if not isinstance(result, EvidenceJudgements):
        raise ValueError("model did not return evidence judgements")
    return list(result.judgements)


def judge_evidence(llm: Any, system_prompt: str, evidence: Sequence[Mapping[str, Any]], watchlist: Mapping[str, Any],
                   today: date, *, max_parallel: int = cfg.JUDGE_MAX_PARALLEL_CALLS) -> JudgeRun:
    """Nilai semua bukti: potongan <= JUDGE_MAX_ITEMS_PER_CALL (9) bukti, <= JUDGE_MAX_PARALLEL_CALLS (4) panggilan paralel (DEC-028); hasil digabung menurut urutan bukti (deterministik).

    Potongan yang melempar exception -> bukti di potongan itu tidak terjawab (``NOT_ANSWERED`` di ``validate_judgements``) dan
    ``failed`` bertambah. SEMUA potongan gagal -> exception pertama dilempar ulang (node ``ERROR``, perilaku ekstraksi lama)."""
    chunks = chunk_evidence(evidence)
    if not chunks:
        return JudgeRun()

    def one(chunk: list[Mapping[str, Any]]) -> tuple[list[EvidenceJudgement] | None, Exception | None]:
        try:
            return judge_chunk(llm, system_prompt, chunk, watchlist, today), None
        except Exception as exc:  # noqa: BLE001 - satu potongan gagal tidak menjatuhkan run
            return None, exc

    with ThreadPoolExecutor(max_workers=max(1, min(len(chunks), max_parallel))) as pool:
        outcomes = list(pool.map(one, chunks))  # urutan = urutan potongan, bukan urutan selesai
    errors = [exc for _, exc in outcomes if exc is not None]
    if len(errors) == len(chunks):
        raise errors[0]
    return JudgeRun(raw=[j for answers, _ in outcomes if answers for j in answers], calls=len(chunks), failed=len(errors))


# --------------------------------------------------------------------------- validasi oleh kode (6.1)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def _valid_iso(value: Any) -> date | None:
    if not isinstance(value, str) or not _DATE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def evidence_texts(evidence: Mapping[str, Any]) -> list[str]:
    """Teks bukti yang boleh dipakai sebagai pembenaran: judul, lead, snippet."""
    return [str(evidence.get(k) or "") for k in ("title", "description", "snippet")]


def _written_in(phrase: Any, evidence: Mapping[str, Any]) -> bool:
    needle = _norm(phrase)
    return bool(needle) and any(needle in _norm(t) for t in evidence_texts(evidence))


def _outcome(evidence: Mapping[str, Any], answer: bool, reason: str, code_override: str | None, **fields: Any) -> Json:
    return {"id": evidence["id"], "answer": answer, "reason": reason, "code_override": code_override,
            "risk_type": None, "severity": None, "severity_source": None, "event_date": None, "event_date_text": None, "kecamatan": [],
            "category": None, "downtime_days": None, "downtime_dropped": False, "summary": None, **fields}


def first_answers(raw: Sequence[EvidenceJudgement | Mapping[str, Any]], known: Collection[str]) -> dict[str, EvidenceJudgement]:
    """Jawaban PERTAMA per id bukti yang dikenal (id asing dan jawaban ganda diabaikan, id dinormalkan)."""
    answers: dict[str, EvidenceJudgement] = {}
    for item in raw:
        judgement = item if isinstance(item, EvidenceJudgement) else EvidenceJudgement.model_validate(item)
        jid = judgement.id.strip().upper()
        if jid in known:
            answers.setdefault(jid, judgement)
    return answers


def validate_judgements(raw: Sequence[EvidenceJudgement | Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]],
                        today: date, *, severity_retry_ids: Collection[str] = ()) -> list[Json]:
    """Tepat satu hasil per bukti, menurut urutan bukti. HANYA kode yang menentukan "ya" final (G-06).

    Urutan: tidak dijawab -> ``NOT_ANSWERED``; id asing/duplikat diabaikan (jawaban pertama dipakai); alasan tidak konsisten ->
    ``INCONSISTENT``; jenis risiko di luar jenis pertanyaan -> ``RISK_TYPE_MISMATCH``; ``event`` tanpa severity 1-5 atau tanpa ringkasan -> ``INCOMPLETE`` (severity pertanyaan negara diabaikan, DEC-019);
    tanggal kejadian yang frasanya tidak tertulis -> dikosongkan (``EVENT_DATE_NOT_IN_TEXT``) dan jawaban tetap "ya"; tanggal
    kejadian di luar periode -> ``EVENT_DATE_OUTSIDE_PERIOD``, kecuali ``event`` yang dimulai sebelum jendela tetapi tidak lebih dari
    ``ONGOING_EVENT_MAX_AGE_DAYS`` hari lalu: tetap "ya", tanggal dikosongkan (``EVENT_STARTED_BEFORE_PERIOD``, DEC-021); kecamatan
    yang tidak disebut di teks dibuang.

    ``severity_retry_ids``: id yang severity-nya diisi tanya ulang (N-09c); hasil per bukti membawa ``severity_source``
    (``"judge"`` | ``"retry"`` | ``None``)."""
    answers = first_answers(raw, {e["id"] for e in evidence})

    out: list[Json] = []
    for e in evidence:
        j = answers.get(e["id"])
        if j is None:
            out.append(_outcome(e, False, NOT_ANSWERED, None))
            continue
        if j.answer != (j.reason in YES_REASONS):
            out.append(_outcome(e, False, j.reason, "INCONSISTENT"))
            continue
        if not j.answer:
            out.append(_outcome(e, False, j.reason, None))
            continue
        kind = question_kind(e)
        if j.risk_type is None or j.risk_type not in cfg.RISK_TYPES_BY_QUESTION[kind]:
            out.append(_outcome(e, False, j.reason, "RISK_TYPE_MISMATCH"))
            continue
        summary = (j.summary or "").strip()
        # Severity hanya untuk kejadian lokal (DEC-019): dipakai K_A (R-10) dan RPT-1.6, keduanya khusus LOCATION. Untuk pertanyaan
        # policy/logistics nilai dari Claude diabaikan dan sinyal membawa None (angka pengisi akan tampil seolah penilaian).
        severity: int | None = None
        if kind == "event":
            lowest, highest = cfg.SEVERITY_RANGE
            severity = j.severity if j.severity is not None and lowest <= j.severity <= highest else None
        code_override: str | None = None
        if (kind == "event" and severity is None) or not summary:
            out.append(_outcome(e, False, j.reason, "INCOMPLETE"))
            continue

        event_date = _valid_iso(j.event_date)
        if j.event_date:  # diisi: harus berbentuk tanggal sah dan frasanya tertulis di judul/lead/snippet bukti itu (K4)
            if event_date is None:
                code_override = "EVENT_DATE_INVALID"
            elif not _written_in(j.event_date_text, e):
                code_override, event_date = "EVENT_DATE_NOT_IN_TEXT", None
        if event_date is not None:
            if kind == "event":
                low, high = today - timedelta(days=cfg.EVENT_WINDOW_DAYS), today + timedelta(days=1)
            else:
                low, high = today - timedelta(days=cfg.POLICY_WINDOW_DAYS), today + timedelta(days=cfg.MAX_FUTURE_DAYS)
            # Kejadian lokal yang mulai sebelum jendela tetapi masih berlangsung tetap "ya" (N-09d, DEC-021): tanggal mulai tidak
            # dipakai (``effective_date`` sinyal = tanggal terbit terawal), selama tidak lebih tua dari ONGOING_EVENT_MAX_AGE_DAYS.
            oldest = today - timedelta(days=cfg.ONGOING_EVENT_MAX_AGE_DAYS) if kind == "event" else low
            if event_date > high or event_date < oldest:
                out.append(_outcome(e, False, j.reason, "EVENT_DATE_OUTSIDE_PERIOD"))
                continue
            if event_date < low:
                code_override, event_date = "EVENT_STARTED_BEFORE_PERIOD", None
        downtime_days, downtime_dropped = normalize_downtime_days(j.downtime_days)
        texts = evidence_texts(e)
        kecamatan = [k.strip() for k in dict.fromkeys(j.kecamatan) if k.strip() and mentions_place(k, *texts)]
        out.append(_outcome(
            e, True, j.reason, code_override, risk_type=j.risk_type, severity=severity,
            severity_source=None if severity is None else ("retry" if e["id"] in severity_retry_ids else "judge"),
            event_date=event_date.isoformat() if event_date else None,
            event_date_text=j.event_date_text if event_date else None, kecamatan=kecamatan,
            category=(j.category or "").strip() or None, downtime_days=downtime_days, downtime_dropped=downtime_dropped,
            summary=summary))
    return out


# --------------------------------------------------------------------------- tanya ulang severity (N-09c, DEC-020)


def severity_missing(raw: Sequence[EvidenceJudgement | Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    """Id bukti (urutan bukti) yang jawaban pertamanya "ya" kejadian lokal yang sah tetapi ``severity`` kosong atau di luar
    ``SEVERITY_RANGE``. Jawaban yang akan dibatalkan karena alasan lain (``INCONSISTENT``, ``RISK_TYPE_MISMATCH``, ringkasan kosong)
    dan pertanyaan negara tidak ikut (DEC-019)."""
    answers = first_answers(raw, {e["id"] for e in evidence})
    lowest, highest = cfg.SEVERITY_RANGE
    missing: list[str] = []
    for e in evidence:
        j = answers.get(e["id"])
        if j is None or not j.answer or j.reason not in YES_REASONS or question_kind(e) != "event":
            continue
        if j.risk_type is None or j.risk_type not in cfg.RISK_TYPES_BY_QUESTION["event"] or not (j.summary or "").strip():
            continue
        if j.severity is None or not lowest <= j.severity <= highest:
            missing.append(e["id"])
    return missing


def severity_message(asked_ids: Sequence[str], evidence: Sequence[Mapping[str, Any]],
                     raw: Sequence[EvidenceJudgement | Mapping[str, Any]], watchlist: Mapping[str, Any] | None = None) -> str:
    """Pesan user tanya ulang: jenis kejadian dan tempat (teks tepercaya buatan kode) di luar ``<external_content>``, artikel di dalamnya."""
    by_id = {e["id"]: e for e in evidence}
    answers = first_answers(raw, set(by_id))
    blocks = ["Rate the severity for each item."]
    for eid in asked_ids:
        e, target = by_id[eid], by_id[eid]["target"]
        country = cfg.country_name(str(target.get("country") or ""))
        blocks.append(
            f"[{eid}]\nEvent: {answers[eid].risk_type} in {place_label(target, watchlist or {})} ({country})\n<external_content>\n"
            f"Published: {e['published_at']} (verified) | Domain: {sanitize_external(e.get('domain') or '')}\n"
            f"{item_text(e)}\n</external_content>")
    return "\n\n".join(blocks)


def ask_severity(llm: Any, system_prompt: str, asked_ids: Sequence[str], evidence: Sequence[Mapping[str, Any]],
                 raw: Sequence[EvidenceJudgement | Mapping[str, Any]],
                 watchlist: Mapping[str, Any] | None = None) -> SeverityAnswers:
    result = llm.with_structured_output(SeverityAnswers).invoke(
        [("system", system_prompt), ("human", severity_message(asked_ids, evidence, raw, watchlist))])
    if not isinstance(result, SeverityAnswers):
        raise ValueError("model did not return severity answers")
    return result


def apply_severity(raw: Sequence[EvidenceJudgement | Mapping[str, Any]], answers: SeverityAnswers,
                   asked_ids: Collection[str]) -> list[EvidenceJudgement]:
    """Salinan ``raw`` yang ``severity``-nya diisi dari ``answers``. HANYA id yang ditanya, HANYA nilai 1-5 (``null``/di luar
    rentang diabaikan), HANYA field ``severity`` jawaban pertama id itu. Jawaban, alasan, jenis risiko, dan field lain tidak
    pernah diubah tanya ulang (G-06)."""
    lowest, highest = cfg.SEVERITY_RANGE
    asked = {str(i).strip().upper() for i in asked_ids}
    fill: dict[str, int] = {}
    for a in answers.answers:
        aid = str(a.id).strip().upper()
        if aid in asked and aid not in fill and a.severity is not None and lowest <= a.severity <= highest:
            fill[aid] = int(a.severity)
    out: list[EvidenceJudgement] = []
    seen: set[str] = set()
    for item in raw:
        j = item if isinstance(item, EvidenceJudgement) else EvidenceJudgement.model_validate(item)
        jid = j.id.strip().upper()
        if jid in asked and jid not in seen and jid in fill:
            j = j.model_copy(update={"severity": fill[jid]})
        seen.add(jid)
        out.append(j)
    return out


def summarize_judgements(judged: Sequence[Mapping[str, Any]], calls: int, failed: int = 0) -> str:
    """Alasan untuk trace ``extract_signals`` (G-06): ya/tidak per alasan dan keputusan yang dibalik kode."""
    def fmt(counter: Counter) -> str:
        return ", ".join(f"{k} {n}" for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

    yes = Counter(j["reason"] for j in judged if j["answer"])
    no = Counter((j["code_override"] or j["reason"]) for j in judged if not j["answer"])
    overrides = Counter(j["code_override"] for j in judged if j["code_override"])
    text = (f"judged {len(judged)} items in {calls} calls: yes {sum(yes.values())}" + (f" ({fmt(yes)})" if yes else "")
            + f"; no {sum(no.values())}" + (f" ({fmt(no)})" if no else ""))
    if overrides:
        text += f"; code overrides {sum(overrides.values())} ({fmt(overrides)})"
    if failed:
        text += f"; JUDGE_CALL_FAILED: {failed} of {calls} calls"
    return text


# --------------------------------------------------------------------------- penyusunan sinyal oleh kode (6.2)


def _number_forms(token: str) -> set[str]:
    forms = {token}
    forms.add(re.sub(r"[.,]", "", token) if _THOUSANDS.fullmatch(token) else token.replace(",", "."))
    return forms


def numbers_grounded(summary: str, texts: Sequence[str]) -> bool:
    """G-04: setiap angka di ``summary`` harus tertulis di teks bukti yang dikutip (pemisah ribuan/desimal dinormalkan)."""
    seen: set[str] = set()
    for text in texts:
        for token in _NUMBER.findall(text or ""):
            seen |= _number_forms(token)
    return all(_number_forms(token) & seen for token in _NUMBER.findall(summary))


def _claims_citing(claims: Sequence[Mapping[str, Any]], urls: set[str]) -> list[Mapping[str, Any]]:
    return [c for c in claims if urls & set(c.get("evidence_urls") or [])]


Group = list[tuple[Mapping[str, Any], Mapping[str, Any]]]


def answer_groups(judged: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], Group]:
    """Jawaban "ya" final dikelompokkan per (lingkup, tempat, jenis risiko), urutan kemunculan; nilai = [(jawaban, bukti)]."""
    by_id = {e["id"]: e for e in evidence}
    groups: dict[tuple[str, str, str], Group] = {}
    for j in judged:
        e = by_id.get(j["id"])
        if not j["answer"] or e is None:
            continue
        target = e["target"]
        place = str(target.get("country") or "").upper() if target.get("scope") == "COUNTRY" else str(target.get("kabupaten"))
        groups.setdefault((target["scope"], place, j["risk_type"]), []).append((j, e))
    return groups


def group_downtime_days(items: Group) -> tuple[int | None, bool]:
    """Lama gangguan satu kelompok: ``(hari, bertentangan)``. Satu nilai (atau nilai yang sama di beberapa bukti) -> nilai itu; tidak
    disebut -> ``(None, False)``; nilai berbeda antar bukti -> ``(None, True)`` (kode tidak memilih salah satu, A-18)."""
    values = sorted({j["downtime_days"] for j, _ in items if j.get("downtime_days") is not None})
    if len(values) > 1:
        return None, True
    return (values[0] if values else None), False


def downtime_notes(judged: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    """Catatan trace A-18 untuk ``downtime_days`` yang dibuang: bukan bilangan bulat >= 1, atau nilai bertentangan dalam satu kelompok."""
    notes = []
    bad = [j["id"] for j in judged if j.get("downtime_dropped")]
    if bad:
        notes.append(f"downtime_days dropped (not a whole number of days): {', '.join(bad)}")
    conflicts = [f"{risk} {place}" for (_, place, risk), items in answer_groups(judged, evidence).items()
                 if group_downtime_days(items)[1]]
    if conflicts:
        notes.append(f"downtime_days dropped (conflicting values): {', '.join(conflicts)}")
    return notes


def signals_from_judgements(judged: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]],
                            claims: Sequence[Mapping[str, Any]], watchlist: Mapping[str, Any]) -> list[Json]:
    """Sinyal kandidat (bentuk ``ExtractedSignal``) dari jawaban "ya" final, satu per (lingkup, tempat, jenis risiko).

    Lokasi/negara dari ``evidence["target"]`` (kode), bukan dari LLM. ``effective_date`` = ``event_date`` sah terawal; bila tidak
    ada: untuk ``TARIFF`` tanggal berlaku klaim sonar yang tergrounding di URL kelompok, selain itu ``published_at`` terawal.
    ``tariff_pp`` hanya dari klaim tergrounding yang mengutip URL kelompok dan hanya bila tepat satu nilai (``judge_signal``
    tetap memeriksa). Angka di ringkasan yang tidak tertulis di bukti -> templat (G-04)."""
    names = {str(c.get("code") or "").upper(): c.get("name") for c in watchlist.get("countries") or []}
    groups = answer_groups(judged, evidence)

    signals: list[Json] = []
    for (scope, place, risk), items in groups.items():
        evs = [e for _, e in items]
        urls = {e["url"] for e in evs}
        related = _claims_citing(claims, urls) if risk == "TARIFF" else []

        event_dates = sorted(j["event_date"] for j, _ in items if j.get("event_date"))
        claim_dates = sorted(c["effective_date"] for c in related if _valid_iso(c.get("effective_date")))
        effective = (event_dates or claim_dates or [min(e["published_at"] for e in evs)])[0]

        rates = {float(c["rate_change_pp"]) for c in related if c.get("rate_grounded") and c.get("rate_change_pp") is not None}
        # A-18: kategori juga untuk kebijakan/logistik negara (filter komponen R-01 v2); sinyal lokasi tidak membawanya
        category = next((j["category"] for j, _ in items if j.get("category")), None) if scope == "COUNTRY" else None
        downtime_days, _ = group_downtime_days(items)

        first_j, first_e = items[0]
        label = (names.get(place) or cfg.country_name(place)) if scope == "COUNTRY" else place
        summary = first_j["summary"]
        if not numbers_grounded(summary, [t for e in evs for t in evidence_texts(e)]):
            summary = f"{risk.replace('_', ' ').capitalize()} reported in {label} by {first_e['domain']} on {first_e['published_at']}."
        signals.append({
            "risk_type": risk, "scope": scope, "country": str(first_e["target"]["country"]).upper(),
            "kabupaten": place if scope == "LOCATION" else None,
            "kecamatan": list(dict.fromkeys(k for j, _ in items for k in j["kecamatan"])),
            "severity": max((int(j["severity"]) for j, _ in items if j["severity"] is not None), default=None),
            "effective_date": effective,
            "tariff_pp": next(iter(rates)) if risk == "TARIFF" and len(rates) == 1 else None, "category": category,
            "downtime_days": downtime_days,
            "summary": summary, "evidence_ids": [e["id"] for e in evs],
        })
    return signals
