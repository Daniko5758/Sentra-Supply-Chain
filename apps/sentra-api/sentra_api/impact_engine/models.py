"""Input and output types of the impact engine. Output follows API_CONTRACT G.1."""

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from sentra_api.erp import ErpData


@dataclass(frozen=True)
class TierLinkInfo:
    """Tier-2 site -> tier-1 site link for the parts it feeds (`tier_link`)."""

    tier2_site: str
    tier1_site: str
    parts: tuple[str, ...]
    buffer_days: int


@dataclass(frozen=True)
class ImpactData:
    erp: ErpData
    tier_links: tuple[TierLinkInfo, ...]


@dataclass(frozen=True)
class PartDelay:
    part_id: str
    days: int
    cause_site: str
    via_site: str | None


@dataclass(frozen=True)
class ImpactPoLine:
    po_id: str
    item_no: int
    material_id: str
    supplier_id: str
    qty: int
    confirmed_date: date
    new_eta: date
    slip_days: int
    value: int
    cause_site: str
    via_site: str | None


@dataclass(frozen=True)
class ImpactProductionOrder:
    order_id: str
    product_id: str
    qty: int
    start_date: date
    units_blocked: int
    blocking_parts: tuple[str, ...]
    ready_date: date | None  # None = "No date within horizon"
    output_value: int
    sales_order_id: str | None


@dataclass(frozen=True)
class ImpactSalesOrder:
    so_id: str
    customer: str
    old_date: date
    new_date: date | None
    units_at_risk: int


@dataclass(frozen=True)
class ImpactTotals:
    units_blocked: int
    output_value: int
    po_value: int
    rar: int
    parts_affected: int
    pos_affected: int
    po_lines_affected: int
    products_affected: tuple[str, ...]
    orders_blocked: int
    max_delay_days: int
    max_po_slip_days: int


@dataclass(frozen=True)
class BaselineShort:
    """Shortage that exists without the event (R-04a); shown in Schedule, not counted as impact."""

    order_id: str
    units_short: int
    short_parts: tuple[str, ...]
    ready_date: date | None


@dataclass(frozen=True)
class TariffInfo:
    """G.1 `tariff` (R-14). `extra_duty` is None when the rate or the date is not stated in the source."""

    country: str
    category: str | None
    tariff_pp: float | None
    effective_date: date | None
    affected_value: int
    extra_duty: int | None
    not_stated: tuple[str, ...]


@dataclass(frozen=True)
class ExposureInfo:
    """G.1 `exposure` (R-18)."""

    country: str
    po_value: int
    note: str


@dataclass(frozen=True)
class DisruptionInfo:
    """G.1 `disruption` (R-18 v2): a country event with a stated length, calculated like a location event."""

    country: str
    category: str | None
    downtime_days: int
    sites: tuple[str, ...]


@dataclass(frozen=True)
class BlockedPart:
    """Units of one production order that one part leaves blocked (R-04). Inside the engine result only: the
    recommendation rules need the shortage per part (R-15), the contract shape of the impact (G.1) does not carry it."""

    order_id: str
    material_id: str
    units: int


@dataclass(frozen=True)
class Impact:
    part_delays: tuple[PartDelay, ...]
    po_lines: tuple[ImpactPoLine, ...]
    production_orders: tuple[ImpactProductionOrder, ...]
    sales_orders: tuple[ImpactSalesOrder, ...]
    totals: ImpactTotals
    baseline_short: tuple[BaselineShort, ...]
    version: int = 1
    scope: str = "LOCATION"
    tariff: TariffInfo | None = None  # R-14, country events only
    exposure: ExposureInfo | None = None  # R-18, country events only
    disruption: DisruptionInfo | None = None  # R-18 v2, country events with downtime_days only
    # Per blocked order of the impact and per blocking part (with the event, R-04); not part of G.1 and not compared.
    blocked_parts: tuple[BlockedPart, ...] = field(default=(), compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """G.1 JSON shape (dates as ISO strings, tuples as lists)."""
        shape = _jsonable(asdict(self))
        del shape["blocked_parts"]
        return shape


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class PoLineSchedule:
    po_id: str
    item_no: int
    slip_days: int
    status: str  # "Slipped" | "On time"


@dataclass(frozen=True)
class OrderCoverage:
    order_id: str
    coverage: str  # "Covered" | "Short"
    short_parts: tuple[str, ...]
    units_short: int
    ready_date: date | None


@dataclass(frozen=True)
class ScheduleStatus:
    """R-19 without an event."""

    po_lines: tuple[PoLineSchedule, ...]
    production_orders: tuple[OrderCoverage, ...]
