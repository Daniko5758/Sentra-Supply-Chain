"""Prompt sistem (English) dimuat saat runtime dari berkas .md di folder ini (PRD 7.5)."""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Isi ``prompts/<name>.md`` tanpa spasi di ujung."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
