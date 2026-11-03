"""R-07a v2, R-07b, R-08 and the R-20 rounding as pure functions. No DB, HTTP, clock, or environment access."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sentra_api.impact_engine import Impact, ImpactData, compute_impact


RAR_CAP = 1_000_000_000  # RULES_AND_GOLDEN section 3, parameter RAR_CAP
TTR_CAP_DAYS = 30
W_RAR, W_TTR, W_BCP, W_TIER = 0.40, 0.25, 0.20, 0.15
TIER_POINTS = {"1": 100, "2": 60}
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


def round1(value: float | int | Decimal | str) -> float:
    """R-20: one decimal, half-up (not Python's banker's `round`)."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _scaled(value: int, divisor: int, places: str) -> Decimal:
    return (Decimal(value) / divisor).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def format_idr(value: int, *, compact: bool = True) -> str:
    """R-20 money text: `IDR 1,250,000,000` (full) or `IDR 1.25B` / `IDR 41.0M` (compact; under one million in full)."""
    sign, amount = ("-", -value) if value < 0 else ("", value)
    if not compact or amount < 1_000_000:
        return f"{sign}IDR {amount:,}"
    millions = _scaled(amount, 1_000_000, "0.1")
    if millions < 1000:
        return f"{sign}IDR {millions}M"
    return f"{sign}IDR {_scaled(amount, 1_000_000_000, '0.01')}B"


# R-07a v2: eight questions (1-5), two per category; weights sum to 1.
BCP_CATEGORIES = {"governance": "G", "resilience": "R", "supply_chain": "S", "testing": "T"}
BCP_WEIGHTS = {"governance": Decimal("0.15"), "resilience": Decimal("0.30"), "supply_chain": Decimal("0.25"),
               "testing": Decimal("0.30")}
BCP_LOW_RISK_FROM = 85
BCP_MEDIUM_RISK_FROM = 60


def bcp_categories(answers: Mapping[str, int]) -> dict[str, Decimal]:
    """R-07a v2: per category `(mean of its two answers - 1) / 4 * 100`, not rounded. Answers outside 1-5 or missing -> ValueError."""
    categories = {}
    for name, letter in BCP_CATEGORIES.items():
        values = []
        for key in (f"{letter}1", f"{letter}2"):
            value = answers.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(f"BCP answer {key} must be an integer from 1 to 5.")
            values.append(value)
        categories[name] = (Decimal(sum(values)) / 2 - 1) / 4 * 100
    return categories


def bcp_v2(answers: Mapping[str, int]) -> int:
    """R-07a v2: `0.15 G + 0.30 R + 0.25 S + 0.30 T`, half-up to a whole number."""
    categories = bcp_categories(answers)
    total = sum(BCP_WEIGHTS[name] * categories[name] for name in BCP_WEIGHTS)
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bcp_band(score: float | int) -> str:
    """R-07a v2 band: `LOW_RISK` from 85, `MEDIUM_RISK` 60-84, `HIGH_RISK` below 60."""
    if score >= BCP_LOW_RISK_FROM:
        return "LOW_RISK"
    return "MEDIUM_RISK" if score >= BCP_MEDIUM_RISK_FROM else "HIGH_RISK"


def level(score: float) -> str:
    """R-08."""
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    return "MEDIUM" if score >= MEDIUM_THRESHOLD else "LOW"


@dataclass(frozen=True)
class SiteProfile:
    """What the score needs to know about a candidate site besides the impact."""

    site_id: str
    supplier_name: str
    tier: str  # "1" | "2"
    bcp_score: int


@dataclass(frozen=True)
class PriorityComponents:
    n_rar: float
    n_ttr: float
    n_bcp: int
    n_tier: int


@dataclass(frozen=True)
class PriorityRow:
    """API_CONTRACT G.5."""

    site_id: str
    supplier_name: str
    tier: str
    recovery_days: int
    rar: int
    output_value: int
    po_value: int
    bcp_score: int
    bcp_band: str
    components: PriorityComponents
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def site_score(rar: int, recovery_days: int, bcp: int, tier: str) -> tuple[PriorityComponents, float]:
    """R-07b. N_RAR and N_TTR stay unrounded in the sum; only the final score is rounded."""
    n_rar = min(100, rar / RAR_CAP * 100)
    n_ttr = min(100, recovery_days / TTR_CAP_DAYS * 100)
    n_bcp = 100 - bcp
    n_tier = TIER_POINTS[tier]
    raw = W_RAR * n_rar + W_TTR * n_ttr + W_BCP * n_bcp + W_TIER * n_tier
    return PriorityComponents(round1(n_rar), round1(n_ttr), n_bcp, n_tier), round1(raw)


def priority_rows(
    data: ImpactData,
    profiles: Mapping[str, SiteProfile],
    candidates: Mapping[str, int],
    d0: date,
    *,
    confirmed_awal: Mapping[tuple[str, int], date] | None = None,
    logistics: bool = False,
) -> tuple[PriorityRow, ...]:
    """Score every candidate site, highest first (ties: site_id).

    candidates: site_id -> recovery days d(s) as resolved by the caller. The RAR of a site is
    standalone (R-06): R-02..R-06 recomputed with only that site as candidate, net of baseline (R-04a v2: original
    confirmation dates, `confirmed_awal`). logistics: the event is a `LOGISTICS` one (R-03 v2).
    """
    rows = []
    for site_id, days in candidates.items():
        profile = profiles[site_id]
        totals = compute_impact(data, {site_id: days}, d0, confirmed_awal=confirmed_awal, logistics=logistics).totals
        components, score = site_score(totals.rar, days, profile.bcp_score, profile.tier)
        rows.append(PriorityRow(
            site_id=site_id, supplier_name=profile.supplier_name, tier=profile.tier, recovery_days=days,
            rar=totals.rar, output_value=totals.output_value, po_value=totals.po_value,
            bcp_score=profile.bcp_score, bcp_band=bcp_band(profile.bcp_score), components=components, score=score,
        ))
    return tuple(sorted(rows, key=lambda row: (-row.score, row.site_id)))


def country_priority_rows(
    impact: Impact,
    profiles: Mapping[str, SiteProfile],
    candidates: Iterable[str],
) -> tuple[PriorityRow, ...]:
    """Score the candidate sites of a country event (R-14, R-18): d(s) = 0 and no blocked output.

    The RAR of a site is the value of its own PO lines in `impact` (standalone, R-06).
    """
    value_by_site: dict[str, int] = {}
    for row in impact.po_lines:
        value_by_site[row.supplier_id] = value_by_site.get(row.supplier_id, 0) + row.value
    rows = []
    for site_id in candidates:
        profile = profiles[site_id]
        po_value = value_by_site.get(site_id, 0)
        components, score = site_score(po_value, 0, profile.bcp_score, profile.tier)
        rows.append(PriorityRow(
            site_id=site_id, supplier_name=profile.supplier_name, tier=profile.tier, recovery_days=0,
            rar=po_value, output_value=0, po_value=po_value, bcp_score=profile.bcp_score,
            bcp_band=bcp_band(profile.bcp_score), components=components, score=score,
        ))
    return tuple(sorted(rows, key=lambda row: (-row.score, row.site_id)))


def case_score(rows: Iterable[PriorityRow]) -> float:
    """R-07b: the case score is the highest site score (0.0 when there is no candidate)."""
    return max((row.score for row in rows), default=0.0)


def case_level(rows: Iterable[PriorityRow]) -> str:
    return level(case_score(rows))
