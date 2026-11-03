"""Ringkasan jadwal PO vs produksi (Schedule Agent, FR-02, prompt PRD 7.5).

Dipakai node ``schedule_check`` di ``analyze_case`` dan, nanti, ``daily_schedule_check`` (A-30). Aturannya:

- Fakta (``schedule_facts``) dihitung dari TL-01 / TL-02; hanya fakta itu yang dikirim ke LLM dan menjadi
  ``allowed`` untuk validator angka (G-04).
- LLM menulis ringkasan ≤ 60 kata; teks lolos ``with_number_validation`` (ulang sekali) atau diganti template.
- Kalau model tidak tersedia / gagal, template dipakai: ringkasan jadwal tidak boleh menjatuhkan analisis kasus.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from graphs.common import with_number_validation
from prompts import load_prompt
from tools.client import Json, ToolClient, ToolError

log = logging.getLogger(__name__)

MAX_WORDS = 60
PROMPT = "schedule_summary"


@dataclass(frozen=True)
class ScheduleSummary:
    text: str
    source: str  # "llm" | "template"
    note: str | None = None  # alasan jatuh ke template (untuk decision di trace)


def schedule_facts(po_schedule: Json, production_schedule: Json) -> Json:
    """Fakta jadwal yang boleh disebut dalam ringkasan (R-19)."""
    lines = po_schedule.get("po_lines") or []
    slips = [int(line["slip_days"]) for line in lines if (line.get("slip_days") or 0) > 0]
    orders = production_schedule.get("production_orders") or []
    return {
        "po_lines": dict(po_schedule.get("totals") or {}),
        "slip_days": {"min": min(slips), "max": max(slips)} if slips else None,
        "production_orders": {
            "total": len(orders),
            "short": [{"order_id": o["order_id"], "short_parts": list(o.get("short_parts") or [])}
                      for o in orders if o.get("coverage") == "Short"],
        },
    }


def template_summary(facts: Json) -> str:
    """Ringkasan tanpa LLM; semua angka dari ``facts``."""
    totals = facts["po_lines"]
    text = f"{totals['slipped']} of {totals['po_lines']} PO lines have already slipped"
    slips = facts.get("slip_days")
    if slips:
        text += (f" ({slips['max']} days at most)" if slips["min"] == slips["max"] else
                 f" ({slips['min']}-{slips['max']} days)")
    short = facts["production_orders"]["short"]
    if short:
        text += f"; {len(short)} production orders are short of parts."
    else:
        text += f"; all {facts['production_orders']['total']} production orders are covered."
    return text


_MARKDOWN_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_MARKDOWN_LINE_START = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)


def plain_text(text: str) -> str:
    """Ringkasan sebagai teks polos: tanda markdown (tebal/miring/kode, judul, butir) dibuang dan spasi dirapikan.

    UI menampilkan ringkasan apa adanya, jadi `**5 have slipped**` akan terlihat dengan bintangnya. Membuang tanda tidak
    mengubah angka, jadi hasil validasi G-04 tetap berlaku."""
    text = _MARKDOWN_LINE_START.sub("", text)
    text = _MARKDOWN_EMPHASIS.sub("", text)
    return " ".join(text.split())


def fit_words(text: str, limit: int = MAX_WORDS) -> str | None:
    """Teks utuh bila ≤ ``limit`` kata; kalau tidak, kalimat-kalimat awal yang muat. ``None`` bila kalimat pertama pun
    terlalu panjang. Memotong kalimat tidak bisa menambah angka, jadi hasil validasi G-04 tetap berlaku."""
    if len(text.split()) <= limit:
        return text
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(" ".join([*kept, sentence]).split()) > limit:
            break
        kept.append(sentence)
    return " ".join(kept) or None


def _content(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(b if isinstance(b, str) else b.get("text", "") for b in content if b)
    return str(content).strip()


def summarize_schedule(tools: ToolClient, get_llm: Callable[[], Any]) -> ScheduleSummary:
    facts = schedule_facts(tools.get_po_schedule(), tools.get_production_schedule())
    fallback = template_summary(facts)

    messages: list[tuple[str, str]] = [("system", load_prompt(PROMPT)),
                                       ("human", json.dumps(facts, ensure_ascii=False))]
    try:
        llm = get_llm()
        draft = _content(llm.invoke(messages))

        def regenerate(correction: str) -> str:
            return _content(llm.invoke([*messages, ("ai", draft), ("human", correction)]))

        if not draft:
            return ScheduleSummary(fallback, "template", "LLM returned an empty summary → template")
        validated = with_number_validation(draft, facts, tools=tools, regenerate=regenerate)
    except Exception as exc:  # model / konfigurasi / jaringan model; ToolError dari validator ikut (lihat bawah)
        if isinstance(exc, ToolError):
            raise  # sentra-api bermasalah bukan alasan memakai template
        log.warning("Schedule summary LLM failed: %s", type(exc).__name__)
        return ScheduleSummary(fallback, "template", f"LLM unavailable ({type(exc).__name__}) → template")

    if validated is None:
        return ScheduleSummary(fallback, "template", "summary failed number validation twice → template")
    fitted = fit_words(plain_text(validated))
    if fitted is None:
        return ScheduleSummary(fallback, "template", f"summary longer than {MAX_WORDS} words → template")
    return ScheduleSummary(fitted, "llm")
