"""G-04 number validator (TL-24, and the check behind TL-16 / TL-20). Pure: no DB, HTTP, clock, or environment.

Every number in an LLM text must exist in the JSON given to the LLM, after normalising the format:
`1.25B`, `1,250,000,000`, `1.250.000.000`, `42.5` and `42,5` are the same number as the value in the data, as
shown with the precision the text uses (R-20: half-up). A text shows a number with at least one decimal when it
rounds, so `43` does not stand for `42.5`.
"""

import re
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, NamedTuple

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LIST_MARKER = re.compile(r"(?m)^[ \t]*\d+[.)][ \t]+")
# A number is not preceded by a word character, a separator, `/`, `+`, `:` (identifiers such as S01, D+14, 10:41) or
# `<letter>-` (PRD-03, LX-2, BAT-48). "5-10" is a range of two numbers.
_NUMBER = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?<![\w.,/+:])(?<![A-Za-z]-)(?P<num>\d+(?:[.,]\d+)*)"
    r"(?:(?P<word>\s(?i:billion|million|thousand)\b)|(?P<unit>(?:bn|B|M|K)\b)|(?P<pct>%))?"
)
_SCALES = {"B": Decimal(10) ** 9, "BN": Decimal(10) ** 9, "BILLION": Decimal(10) ** 9, "M": Decimal(10) ** 6,
           "MILLION": Decimal(10) ** 6, "K": Decimal(10) ** 3, "THOUSAND": Decimal(10) ** 3}


class NumberToken(NamedTuple):
    token: str  # as written in the text
    shown: Decimal  # the number as typed (1.25 for `1.25B`)
    decimals: int  # decimals as typed
    scale: Decimal  # 1, or 10^3 / 10^6 / 10^9 for K / M / B
    percent: bool


def _parse(body: str) -> tuple[Decimal, int] | None:
    """`1,250,000,000` / `1.250.000.000` are thousands separators; `42,5` / `42.5` are decimals."""
    if re.fullmatch(r"\d{1,3}(,\d{3})+", body):
        return Decimal(body.replace(",", "")), 0
    if re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", body):
        return Decimal(body.replace(".", "")), 0
    if body.count(",") + body.count(".") > 1:
        return None
    normalized = body.replace(",", ".")
    try:
        return Decimal(normalized), len(normalized.split(".")[1]) if "." in normalized else 0
    except InvalidOperation:
        return None


def _scan(text: str) -> Iterator[str | NumberToken]:
    """ISO dates (as `str`) and numbers in order of appearance; list markers (`1.`, `2)`) are not numbers."""
    text = _LIST_MARKER.sub(lambda m: " " * len(m.group(0)), text)
    for match in _NUMBER.finditer(text):
        if match.group("date"):
            yield match.group("date")
            continue
        parsed = _parse(match.group("num"))
        if parsed is None:
            continue
        shown, decimals = parsed
        suffix = (match.group("word") or match.group("unit") or "").strip().upper()
        yield NumberToken(match.group(0), shown, decimals, _SCALES.get(suffix, Decimal(1)), match.group("pct") is not None)


def extract_numbers(text: str) -> list[NumberToken]:
    """The numbers of a text (ISO dates are checked as a whole by `check_numbers`)."""
    return [item for item in _scan(text) if isinstance(item, NumberToken)]


class _Allowed:
    def __init__(self) -> None:
        self.numbers: set[Decimal] = set()
        self.dates: set[str] = set()

    def add_number(self, value: Decimal) -> None:
        self.numbers.add(abs(value))

    def add_text(self, text: str) -> None:
        for item in _scan(text):
            if isinstance(item, str):
                self.add_date(item)
            else:
                value = item.shown * item.scale
                self.add_number(value)
                if item.percent:
                    self.add_number(value / 100)

    def add_date(self, value: str) -> None:
        self.dates.add(value)
        self.numbers.update(Decimal(int(part)) for part in value.split("-"))  # "14 Nov 2026" in a text

    def walk(self, node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            self.add_number(Decimal(str(node)))
        elif isinstance(node, str):
            self.add_text(node)
            self.dates.update(_ISO_DATE.findall(node))
        elif isinstance(node, dict):
            for value in node.values():
                self.walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                self.walk(value)


def _shown_as(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_HALF_UP) * step


def _is_allowed(item: NumberToken, allowed: _Allowed) -> bool:
    typed = item.shown * item.scale
    step = Decimal(10) ** -max(item.decimals, 1) * item.scale  # R-20: at least one decimal when rounding
    for value in allowed.numbers:
        if _shown_as(value, step) == typed:
            return True
        if item.percent and _shown_as(value * 100, step) == typed:  # `20%` for a ratio of 0.20
            return True
    return False


def check_numbers(text: str, allowed: Any) -> list[str]:
    """The numbers and dates of `text` that are not in `allowed` (any JSON), each once, in order of appearance."""
    known = _Allowed()
    known.walk(allowed)
    offending: list[str] = []
    for item in _scan(text):
        token = item if isinstance(item, str) else item.token
        ok = item in known.dates if isinstance(item, str) else _is_allowed(item, known)
        if not ok and token not in offending:
            offending.append(token)
    return offending
