"""R-21 country risk heatmap (EP-16) as a pure function. No DB, HTTP, clock, or environment access."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sentra_api.erp import ErpData
from sentra_api.impact_engine import HORIZON_DAYS
from sentra_api.impact_engine.engine import is_open
from sentra_api.priority import round1


LEVEL_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
EXPOSURE_TIER_LOWER_BOUNDS = (5, 15, 40)  # share (%) at which tier 2, 3, 4 start
COUNTRY_NAMES = {  # display names for ISO 3166-1 alpha-2 codes; an unlisted code is shown as is
    "AU": "Australia", "BD": "Bangladesh", "BR": "Brazil", "CA": "Canada", "CN": "China", "DE": "Germany",
    "FR": "France", "GB": "United Kingdom", "HK": "Hong Kong", "ID": "Indonesia", "IN": "India", "IT": "Italy",
    "JP": "Japan", "KH": "Cambodia", "KR": "South Korea", "MM": "Myanmar", "MX": "Mexico", "MY": "Malaysia",
    "NL": "Netherlands", "PH": "Philippines", "PL": "Poland", "SG": "Singapore", "TH": "Thailand", "TR": "Turkey",
    "TW": "Taiwan", "US": "United States", "VN": "Vietnam",
}


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)


@dataclass(frozen=True)
class HeatmapSite:
    """One `supplier_site` row (tier `1`, `2` or `ALT`)."""

    site_id: str
    supplier_name: str
    tier: str
    kabupaten: str | None
    country: str


@dataclass(frozen=True)
class OpenCase:
    """A case that is not CLOSED/DISMISSED. site_ids = its candidate sites (`case_site`)."""

    case_id: str
    title: str
    level: str
    score: float
    site_ids: tuple[str, ...]
    simulated: bool


@dataclass(frozen=True)
class CountryCase:
    case_id: str
    title: str
    level: str
    score: float


@dataclass(frozen=True)
class CountrySite:
    site_id: str
    supplier_name: str
    tier: str
    kabupaten: str | None


@dataclass(frozen=True)
class CountryRow:
    country: str
    name: str
    sites_t1: int
    sites_t2: int
    open_po_lines: int
    po_exposure: int
    share: float
    exposure_tier: int
    highest_level: str | None
    open_cases: tuple[CountryCase, ...]
    sites: tuple[CountrySite, ...]


@dataclass(frozen=True)
class Heatmap:
    """API_CONTRACT EP-16."""

    total_po_exposure: int
    simulated_cases_present: bool
    countries: tuple[CountryRow, ...]

    def to_dict(self) -> dict[str, Any]:
        """EP-16 JSON shape (tuples as lists)."""
        return _listify(asdict(self))


def _listify(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _listify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_listify(item) for item in value]
    return value


def exposure_tier(share: float) -> int:
    """Colour tier 1-4 of the `PO exposure` mode: < 5%, 5-< 15%, 15-< 40%, >= 40% (pass the unrounded share)."""
    return 1 + sum(share >= bound for bound in EXPOSURE_TIER_LOWER_BOUNDS)


def build_heatmap(
    erp: ErpData,
    sites: Iterable[HeatmapSite],
    open_cases: Iterable[OpenCase],
    d0: date,
    *,
    horizon_days: int = HORIZON_DAYS,
) -> Heatmap:
    """R-21.

    A country is listed when it has at least one tier 1/2 site. Its exposure is the value (current qty x net
    price) of the open PO lines to its tier 1 or ALT sites with a current confirmed date up to D0 + horizon
    (late lines included); `share` is relative to the total of the listed countries. A case counts for every
    country that holds one of its candidate sites.
    """
    by_id = {site.site_id: site for site in sites}
    colored = [site for site in by_id.values() if site.tier in ("1", "2")]
    rows = {code: {"t1": 0, "t2": 0, "lines": 0, "exposure": 0, "cases": [], "sites": []}
            for code in sorted({site.country for site in colored})}
    for site in sorted(colored, key=lambda s: s.site_id):
        row = rows[site.country]
        row["t1" if site.tier == "1" else "t2"] += 1
        row["sites"].append(CountrySite(site.site_id, site.supplier_name, site.tier, site.kabupaten))

    for line in erp.po_lines:
        supplier = by_id.get(line.supplier_id)
        if supplier is None or supplier.tier not in ("1", "ALT") or supplier.country not in rows:
            continue
        if is_open(line) and (line.confirmed_date - d0).days <= horizon_days:
            rows[supplier.country]["lines"] += 1
            rows[supplier.country]["exposure"] += line.qty * line.net_price

    cases = list(open_cases)
    for case in cases:
        if case.level not in LEVEL_RANK:
            raise ValueError(f"Case {case.case_id} has unknown level {case.level!r}.")
        for code in sorted({by_id[site_id].country for site_id in case.site_ids}):
            if code in rows:
                rows[code]["cases"].append(case)

    total = sum(row["exposure"] for row in rows.values())
    countries = []
    for code, row in rows.items():
        share = row["exposure"] / total * 100 if total else 0.0
        ranked = sorted(row["cases"], key=lambda c: (-LEVEL_RANK[c.level], -c.score, c.case_id))
        countries.append(CountryRow(
            country=code, name=country_name(code), sites_t1=row["t1"], sites_t2=row["t2"],
            open_po_lines=row["lines"], po_exposure=row["exposure"], share=round1(share),
            exposure_tier=exposure_tier(share), highest_level=ranked[0].level if ranked else None,
            open_cases=tuple(CountryCase(c.case_id, c.title, c.level, c.score) for c in ranked),
            sites=tuple(row["sites"]),
        ))
    countries.sort(key=lambda row: (-row.po_exposure, row.country))
    return Heatmap(total, any(case.simulated for case in cases), tuple(countries))
