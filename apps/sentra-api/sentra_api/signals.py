"""TL-05 signal validation (enums, G-02) and R-01 site matching as pure functions. No DB, HTTP, clock, or environment."""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

RISK_TYPES = frozenset({"FLOOD", "LANDSLIDE", "FIRE", "EARTHQUAKE", "LOGISTICS", "TARIFF", "POLITICAL"})
SCOPES = frozenset({"LOCATION", "COUNTRY"})
ENGINES = frozenset({"GDELT", "SONAR", "BRAVE", "CURRENTS", "DEMO"})
VERIFICATIONS = frozenset({"VERIFIED", "UNVERIFIED"})
DEMO_ENGINE = "DEMO"
DEMO_SOURCE_LABEL = "SENTRA demo scenario (SIMULATED)"
CASE_MERGE_WINDOW_H = 72  # RULES_AND_GOLDEN section 3
DUPLICATE_WINDOW_DAYS = 30  # TL-05 DUPLICATE_SOURCE (DEC-015)
MATCHABLE_TIERS = frozenset({"1", "2"})  # ALT sites never take part in R-01
# R-01 v2 / R-18 v2: the country events that can carry a stated downtime and are then calculated like a disruption
DISRUPTION_COUNTRY_RISKS = frozenset({"POLITICAL", "LOGISTICS"})

_PLACE_PREFIX = re.compile(r"^(?:kabupaten\s+|kab\.\s*|kab\s+|kota\s+)", re.IGNORECASE)


class SignalRejected(Exception):
    """The signal is dropped; `reason` is one of the TL-05 `dropped` reasons."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SiteRef:
    site_id: str
    tier: str
    kabupaten: str | None
    country: str


@dataclass(frozen=True)
class ParsedSignal:
    risk_type: str
    scope: str
    country: str
    kabupaten: str | None
    kecamatan: list[str]
    severity: int | None
    effective_date: date
    tariff_pp: float | None
    category: str | None
    summary: str
    source_urls: list[str]
    engine: str
    verification: str | None
    downtime_days: int | None = None  # v1.11 (DEC-037): length of the disruption as the source states it
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_label: str | None = None

    @property
    def simulated(self) -> bool:
        return self.engine == DEMO_ENGINE

    @property
    def mode(self) -> str:
        """Origin of the signal, never the application mode (DEC-024)."""
        return "DEMO" if self.simulated else "LIVE"


def normalize_place(value: str | None) -> str:
    """Case-insensitive place key; a leading `Kab.`/`Kabupaten`/`Kota` word is ignored (R-01)."""
    if value is None:
        return ""
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return _PLACE_PREFIX.sub("", text, count=1)


def display_place(value: str) -> str:
    """The name as shown in titles: whitespace collapsed and the `Kab.`/`Kabupaten`/`Kota` word removed."""
    return _PLACE_PREFIX.sub("", " ".join(value.split()), count=1)


def _category_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def candidate_sites(
    scope: str,
    kabupaten: str | None,
    country: str | None,
    sites: Iterable[SiteRef],
    *,
    risk_type: str | None = None,
    category: str | None = None,
    downtime_days: int | None = None,
    supplied_categories: Mapping[str, Iterable[str]] | None = None,
) -> list[SiteRef]:
    """R-01 / R-01 v2: tier 1-2 sites ordered by site_id.

    LOCATION: same kabupaten. COUNTRY: same country; a `TARIFF` takes tier-1 sites only (the duty is on what we import
    directly); a `POLITICAL` or `LOGISTICS` event that states `downtime_days` and a `category` takes only the sites that
    supply that category. `supplied_categories` maps a site to the categories it supplies (parts it is the main supplier of, and the
    `component_category` of its tier-2 links); categories compare with `strip().casefold()`.
    """
    if scope == "LOCATION":
        wanted = normalize_place(kabupaten)
        if not wanted:
            return []
        matches = [s for s in sites if s.tier in MATCHABLE_TIERS and normalize_place(s.kabupaten) == wanted]
    elif scope == "COUNTRY":
        code = (country or "").strip().upper()
        matches = [s for s in sites if s.tier in MATCHABLE_TIERS and code and s.country.upper() == code]
        if risk_type == "TARIFF":
            matches = [s for s in matches if s.tier == "1"]
        elif risk_type in DISRUPTION_COUNTRY_RISKS and downtime_days is not None and _category_key(category):
            key = _category_key(category)
            supplied = supplied_categories or {}
            matches = [s for s in matches if any(_category_key(c) == key for c in supplied.get(s.site_id, ()))]
    else:
        return []
    return sorted(matches, key=lambda s: s.site_id)


def _bad(detail: str) -> SignalRejected:
    return SignalRejected("INVALID_ENUM", detail)


def _text(raw: Mapping[str, Any], key: str, *, required: bool) -> str | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise _bad(f"{key} is required")
        return None
    if not isinstance(value, str):
        raise _bad(f"{key} must be a string")
    value = value.strip()
    if not value and required:
        raise _bad(f"{key} is required")
    return value or None


def _string_list(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _bad(f"{key} must be a list of strings")
    return value


def parse_signal(raw: Mapping[str, Any]) -> ParsedSignal:
    """Validate one TL-05 signal: enums first (INVALID_ENUM), then G-02 (NO_SOURCE_URL unless engine = DEMO).

    The request-level `mode` is ignored: a signal is DEMO exactly when its engine is DEMO.
    """
    risk_type, scope, engine = raw.get("risk_type"), raw.get("scope"), raw.get("engine")
    if risk_type not in RISK_TYPES:
        raise _bad("risk_type")
    if scope not in SCOPES:
        raise _bad("scope")
    if engine not in ENGINES:
        raise _bad("engine")

    country = _text(raw, "country", required=True)
    if len(country) != 2 or not country.isalpha():
        raise _bad("country must be an ISO 3166-1 alpha-2 code")
    kabupaten = _text(raw, "kabupaten", required=scope == "LOCATION")

    severity = raw.get("severity")
    if scope == "LOCATION":
        if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 5:
            raise _bad("severity must be an integer 1-5 for scope LOCATION")
    else:
        severity = None  # DEC-019: a country signal has no severity

    raw_date = raw.get("effective_date")
    try:
        effective_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
        effective_date = None
    if effective_date is None:
        raise _bad("effective_date must be YYYY-MM-DD")

    downtime_days = raw.get("downtime_days")
    if downtime_days is not None and (isinstance(downtime_days, bool) or not isinstance(downtime_days, int) or downtime_days < 1):
        raise _bad("downtime_days must be an integer >= 1 or null")

    tariff_pp = raw.get("tariff_pp")
    if tariff_pp is not None and (isinstance(tariff_pp, bool) or not isinstance(tariff_pp, (int, float))):
        raise _bad("tariff_pp must be a number")
    summary = _text(raw, "summary", required=True)
    category = _text(raw, "category", required=False)
    kecamatan = _string_list(raw, "kecamatan")

    verification = raw.get("verification")
    if verification is not None and verification not in VERIFICATIONS:
        raise _bad("verification")
    evidence = raw.get("evidence")
    if evidence is not None and not (isinstance(evidence, list) and all(isinstance(e, dict) for e in evidence)):
        raise _bad("evidence must be a list of objects")

    urls_raw = raw.get("source_urls")
    if urls_raw is not None and not isinstance(urls_raw, list):
        raise _bad("source_urls must be a list")
    source_urls: list[str] = []
    for url in urls_raw or []:
        if isinstance(url, str) and url.strip() and url.strip() not in source_urls:
            source_urls.append(url.strip())
    simulated = engine == DEMO_ENGINE
    if not source_urls and not simulated:
        raise SignalRejected("NO_SOURCE_URL")  # G-02

    return ParsedSignal(
        risk_type=risk_type, scope=scope, country=country.upper(),
        kabupaten=display_place(kabupaten) if kabupaten else None, kecamatan=list(kecamatan), severity=severity,
        effective_date=effective_date, tariff_pp=tariff_pp, category=category, summary=summary,
        source_urls=source_urls, engine=engine, downtime_days=downtime_days,
        verification=None if simulated else (verification or "VERIFIED"),  # LIVE without verification = VERIFIED
        evidence=[] if simulated else list(evidence or []),
        source_label=_text(raw, "source_label", required=False) or (DEMO_SOURCE_LABEL if simulated else None),
    )
