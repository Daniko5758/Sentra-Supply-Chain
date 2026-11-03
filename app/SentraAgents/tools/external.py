"""Pencarian eksternal agen: sonar lama (TL-04, kompatibilitas). Bukan endpoint ``sentra-api``.

N-17 (DEC-032): GDELT (TL-03) dihapus dari agen. Pertanyaan kebijakan dagang untuk ``scan_external`` dibangun ``tools/sonar_policy.py``;
berkas ini hanya menyisakan ``sonar_search`` generik.

- ``sonar_search``: pertanyaan lewat ``model/load.py::get_sonar().ask`` (klien OpenAI native). ``citations`` hanya
  dari field respons (G-02); tanpa sitasi, jawabannya tidak boleh menjadi sumber sinyal.

Tidak ada nilai rahasia di sini; sonar memakai kredensial AI Core lewat ``model/load.py``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

Json = dict[str, Any]

SONAR_SYSTEM_PROMPT = (
    "You are a news research assistant for supply-chain risk monitoring. Answer in at most four sentences. "
    "Report only events you can cite, and give the publication date of each event. "
    "If you find nothing recent, say so plainly."
)


def sonar_search(question: str, sonar: Any = None, today: date | None = None) -> Json:
    """TL-04: ``{"question", "answer", "citations", "status": OK|ERROR}``.

    ``citations`` kosong → jawaban tidak boleh menjadi sumber sinyal (G-02). Kegagalan model / konfigurasi menjadi
    ``status = "ERROR"`` (tanpa membocorkan pesan mentah).
    """
    today = today or date.today()
    try:
        if sonar is None:
            from model.load import get_sonar

            sonar = get_sonar()
        answer = sonar.ask([
            {"role": "system", "content": SONAR_SYSTEM_PROMPT},
            {"role": "user", "content": f"Today is {today.isoformat()}. {question}"},
        ])
    except Exception as exc:  # noqa: BLE001 - konfigurasi, jaringan, atau penolakan gateway
        log.warning("sonar request failed: %s", type(exc).__name__)
        return {"question": question, "answer": "", "citations": [], "status": "ERROR",
                "error": type(exc).__name__}
    return {"question": question, "answer": answer.text, "citations": list(answer.citations), "status": "OK"}
