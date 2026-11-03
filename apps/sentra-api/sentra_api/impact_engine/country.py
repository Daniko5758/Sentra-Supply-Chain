"""R-14 (tariff) and R-18 (country exposure) as pure functions. No DB, HTTP, clock, or environment access.

A country event has no recovery time (d(s) = 0): nothing is delayed, so there are no part delays, no
blocked production orders, and RAR is the value of the PO lines it touches.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sentra_api.erp import ErpData, PoLine
from sentra_api.impact_engine.engine import HORIZON_DAYS, is_open
from sentra_api.impact_engine.models import ExposureInfo, Impact, ImpactPoLine, ImpactTotals, TariffInfo


EXPOSURE_NOTE = "Delay not estimated — no country-level recovery data"


def _country_lines(erp: ErpData, site_countries: Mapping[str, str], country: str) -> list[PoLine]:
    """Open PO lines whose supplier site is in `country`, in PO order."""
    lines = (line for line in erp.po_lines if is_open(line) and site_countries.get(line.supplier_id) == country)
    return sorted(lines, key=lambda line: (line.po_id, line.item_no))


def _extra_duty(affected_value: int, tariff_pp: float | int | Decimal) -> int:
    """Whole rupiah, half-up: value x tariff_pp / 100."""
    return int((Decimal(affected_value) * Decimal(str(tariff_pp)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _country_impact(lines: Iterable[PoLine], *, tariff: TariffInfo | None, exposure: ExposureInfo | None) -> Impact:
    po_lines = tuple(
        ImpactPoLine(
            po_id=line.po_id, item_no=line.item_no, material_id=line.material_id, supplier_id=line.supplier_id,
            qty=line.qty, confirmed_date=line.confirmed_date, new_eta=line.confirmed_date, slip_days=0,
            value=line.qty * line.net_price, cause_site=line.supplier_id, via_site=None,
        )
        for line in lines
    )
    po_value = sum(row.value for row in po_lines)
    totals = ImpactTotals(
        units_blocked=0, output_value=0, po_value=po_value, rar=po_value,
        parts_affected=len({row.material_id for row in po_lines}), pos_affected=len({row.po_id for row in po_lines}),
        po_lines_affected=len(po_lines), products_affected=(), orders_blocked=0, max_delay_days=0, max_po_slip_days=0,
    )
    return Impact(
        part_delays=(), po_lines=po_lines, production_orders=(), sales_orders=(), totals=totals,
        baseline_short=(), scope="COUNTRY", tariff=tariff, exposure=exposure,
    )


def tariff_impact(
    erp: ErpData,
    site_countries: Mapping[str, str],
    *,
    country: str,
    category: str | None,
    tariff_pp: float | int | None,
    effective_date: date | None,
) -> Impact:
    """R-14: open PO lines of the country (and part category, if the signal names one) whose current
    confirmed date is on or after `effective_date` bear the tariff.

    site_countries: site_id -> ISO country. category: a `material.category`, None = every category.
    Without `effective_date` there is no date to compare against, so every open line of the country
    counts; without `tariff_pp` or `effective_date` no extra duty is computed (`not_stated`).
    """
    wanted = None if category is None else category.strip().casefold()
    materials = {m.material_id: m.category.strip().casefold() for m in erp.materials}
    lines = [
        line for line in _country_lines(erp, site_countries, country)
        if (wanted is None or materials.get(line.material_id) == wanted)
        and (effective_date is None or line.confirmed_date >= effective_date)
    ]
    affected_value = sum(line.qty * line.net_price for line in lines)
    not_stated = tuple(name for name, value in (("tariff_pp", tariff_pp), ("effective_date", effective_date))
                       if value is None)
    extra_duty = None if not_stated else _extra_duty(affected_value, tariff_pp)
    info = TariffInfo(country, category, tariff_pp, effective_date, affected_value, extra_duty, not_stated)
    return _country_impact(lines, tariff=info, exposure=None)


def exposure_impact(
    erp: ErpData,
    site_countries: Mapping[str, str],
    *,
    country: str,
    d0: date,
    horizon_days: int = HORIZON_DAYS,
) -> Impact:
    """R-18: exposure = value of the country's open PO lines with ETA within the horizon (late lines included)."""
    lines = [line for line in _country_lines(erp, site_countries, country)
             if (line.confirmed_date - d0).days <= horizon_days]
    po_value = sum(line.qty * line.net_price for line in lines)
    return _country_impact(lines, tariff=None, exposure=ExposureInfo(country, po_value, EXPOSURE_NOTE))
