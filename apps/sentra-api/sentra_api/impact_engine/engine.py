"""R-02..R-06 and R-04a as pure functions. No DB, HTTP, clock, or environment access.

Dates are handled as day offsets from D0 internally and converted back to `date` in the result.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from sentra_api.erp import PoLine
from sentra_api.impact_engine.models import (
    BaselineShort, BlockedPart, Impact, ImpactData, ImpactPoLine, ImpactProductionOrder, ImpactSalesOrder,
    ImpactTotals, PartDelay,
)


HORIZON_DAYS = 30  # RULES_AND_GOLDEN section 3, parameter HORIZON_DAYS


@dataclass(frozen=True)
class _Delay:
    days: int
    cause_site: str
    via_site: str | None
    # Best propagated tier-2 component alone (R-03 v2): what an air line still suffers in a logistics event.
    prop_days: int = 0
    prop_cause: str | None = None
    prop_via: str | None = None

    def propagated_only(self) -> "_Delay":
        return _Delay(self.prop_days, self.prop_cause or self.cause_site, self.prop_via)


@dataclass(frozen=True)
class _AffectedLine:
    line: PoLine
    confirmed_awal: int
    new_eta: int
    delay: _Delay


@dataclass(frozen=True)
class _BlockedOrder:
    order_id: str
    units: int
    parts: tuple[str, ...]
    ready: int | None
    per_part: tuple[tuple[str, int], ...]  # (part, units blocked by that part)


@dataclass(frozen=True)
class _Run:
    affected: tuple[_AffectedLine, ...]
    blocked: dict[str, _BlockedOrder]


def is_open(line: PoLine) -> bool:
    return line.qty > 0 and line.status != "CANCELLED"


def part_delays(data: ImpactData, candidates: Mapping[str, int]) -> dict[str, _Delay]:
    """R-02: D(p) = max(direct delay of the main supplier, propagated tier-2 delay minus buffer).

    The best propagated component is kept apart (R-03 v2): on a tie the tier-1 site stays the cause of D(p).
    """
    links = sorted(data.tier_links, key=lambda link: (link.tier2_site, link.tier1_site))
    delays: dict[str, _Delay] = {}
    for material in sorted(data.erp.materials, key=lambda m: m.material_id):
        tier1 = material.main_supplier_id
        direct = max(0, candidates.get(tier1, 0))
        prop, prop_cause, prop_via = 0, None, None
        for link in links:
            if link.tier1_site != tier1 or material.material_id not in link.parts:
                continue
            if link.tier2_site not in candidates:
                continue
            propagated = max(0, candidates[link.tier2_site] - link.buffer_days)
            if propagated > prop:
                prop, prop_cause, prop_via = propagated, link.tier2_site, tier1
        if direct > 0 and direct >= prop:
            delays[material.material_id] = _Delay(direct, tier1, None, prop, prop_cause, prop_via)
        elif prop > 0:
            delays[material.material_id] = _Delay(prop, prop_cause, prop_via, prop, prop_cause, prop_via)
    return delays


def _simulate(
    data: ImpactData,
    delays: Mapping[str, _Delay],
    d0: date,
    horizon_days: int,
    confirmed_awal: Mapping[tuple[str, int], date],
    *,
    baseline: bool = False,
    logistics: bool = False,
) -> _Run:
    """R-03 and R-04 for one set of part delays.

    baseline (R-04a v2): the run without the event, every line that existed when the case was created arrives on its original
    confirmation date. logistics (R-03 v2): an air line is not hit by the direct delay of its supplier, only by what
    propagates from tier 2.
    """
    erp = data.erp
    day = lambda value: (value - d0).days  # noqa: E731
    main_supplier = {m.material_id: m.main_supplier_id for m in erp.materials}

    affected: list[_AffectedLine] = []
    supply: dict[str, list[tuple[int, int]]] = {}
    for line in sorted(erp.po_lines, key=lambda l: (l.po_id, l.item_no)):
        if not is_open(line):
            continue
        awal = day(confirmed_awal.get((line.po_id, line.item_no), line.confirmed_date))
        eta = awal if baseline else day(line.confirmed_date)
        delay = delays.get(line.material_id)
        if delay is not None and logistics and line.shipping_condition == "AIR":
            delay = delay.propagated_only()
        # Only lines of the part's main supplier are delayed; a PO to an alternate source is not.
        if delay is not None and delay.days > 0 and line.supplier_id == main_supplier.get(line.material_id):
            if awal >= 0:
                new_eta = max(awal, delay.days)
                if new_eta > awal:
                    affected.append(_AffectedLine(line, awal, new_eta, delay))
                    eta = new_eta
        supply.setdefault(line.material_id, []).append((eta, line.qty))

    orders = sorted((o for o in erp.production_orders if day(o.start_date) <= horizon_days),
                    key=lambda o: (o.start_date, o.order_id))
    per_unit = {(b.product_id, b.material_id): b.qty_per_unit for b in erp.bom_items}
    stock = {s.material_id: s.qty for s in erp.stocks}
    blocked: dict[str, dict[str, int]] = {}
    ready: dict[str, dict[str, int | None]] = {}
    for material in sorted(erp.materials, key=lambda m: m.material_id):
        part = material.material_id
        arrivals = sorted(supply.get(part, []))
        used = 0
        for order in orders:
            qpu = per_unit.get((order.product_id, part), 0)
            if qpu <= 0:
                continue
            start = day(order.start_date)
            available = stock.get(part, 0) + sum(q for eta, q in arrivals if eta <= start) - used
            need = order.qty * qpu
            if available >= need:
                used += need
                continue
            can_build = max(0, available) // qpu
            target = used + need
            used += can_build * qpu
            blocked.setdefault(order.order_id, {})[part] = order.qty - can_build
            on_hand, ready_day = stock.get(part, 0), None
            for eta, q in arrivals:
                on_hand += q
                if on_hand >= target and eta <= horizon_days:
                    ready_day = eta
                    break
            ready.setdefault(order.order_id, {})[part] = ready_day

    result = {}
    for order_id, shortages in blocked.items():
        ready_days = list(ready[order_id].values())
        result[order_id] = _BlockedOrder(
            order_id, max(shortages.values()), tuple(sorted(shortages)),
            None if any(r is None for r in ready_days) else max(ready_days),
            tuple(sorted(shortages.items())),
        )
    return _Run(tuple(affected), result)


def compute_impact(
    data: ImpactData,
    candidates: Mapping[str, int],
    d0: date,
    *,
    confirmed_awal: Mapping[tuple[str, int], date] | None = None,
    horizon_days: int = HORIZON_DAYS,
    logistics: bool = False,
) -> Impact:
    """Net impact of an event on `data` (R-02..R-06, R-03 v2, R-04a v2).

    candidates: site_id -> recovery days d(s), already resolved by the caller (supplier reply, stated downtime,
    site recovery time, excluded sites = 0). confirmed_awal: (po_id, item_no) -> confirmation date
    when the case was created (snapshot version 1, R-03); defaults to the current confirmed date. It is also the arrival
    date of every line in the run without the event (R-04a v2). logistics: the event is a `LOGISTICS` one (R-03 v2).
    """
    awal = confirmed_awal or {}
    delays = part_delays(data, candidates)
    with_event = _simulate(data, delays, d0, horizon_days, awal, logistics=logistics)
    without_event = _simulate(data, {}, d0, horizon_days, awal, baseline=True)
    erp = data.erp
    day = lambda value: (value - d0).days  # noqa: E731
    at = lambda offset: d0 + timedelta(days=offset)  # noqa: E731
    order_by_id = {o.order_id: o for o in erp.production_orders}
    price = {p.product_id: p.sales_price for p in erp.products}
    sales = {s.so_id: s for s in erp.sales_orders}

    po_lines = tuple(
        ImpactPoLine(
            po_id=a.line.po_id, item_no=a.line.item_no, material_id=a.line.material_id,
            supplier_id=a.line.supplier_id, qty=a.line.qty, confirmed_date=at(a.confirmed_awal),
            new_eta=at(a.new_eta), slip_days=a.new_eta - a.confirmed_awal, value=a.line.qty * a.line.net_price,
            cause_site=a.delay.cause_site, via_site=a.delay.via_site,
        )
        for a in with_event.affected
    )

    # R-04a: only the units blocked because of the event count.
    orders, sales_rows = [], []
    for order_id in sorted(with_event.blocked):
        run = with_event.blocked[order_id]
        base = without_event.blocked.get(order_id)
        units = run.units - (base.units if base else 0)
        if units <= 0:
            continue
        order = order_by_id[order_id]
        orders.append(ImpactProductionOrder(
            order_id=order_id, product_id=order.product_id, qty=order.qty, start_date=order.start_date,
            units_blocked=units, blocking_parts=run.parts,
            ready_date=None if run.ready is None else at(run.ready),
            output_value=units * price[order.product_id], sales_order_id=order.sales_order_id,
        ))
        if order.sales_order_id is not None:
            sales_order = sales.get(order.sales_order_id)
            if sales_order is None:
                raise ValueError(f"Production order {order_id} refers to unknown sales order {order.sales_order_id}.")
            old = sales_order.requested_delivery_date
            sales_rows.append(ImpactSalesOrder(
                so_id=sales_order.so_id, customer=sales_order.customer, old_date=old,
                new_date=None if run.ready is None else old + timedelta(days=run.ready - day(order.start_date)),
                units_at_risk=units,
            ))

    blocked_parts = tuple(
        BlockedPart(o.order_id, part, units) for o in orders for part, units in with_event.blocked[o.order_id].per_part
    )
    output_value = sum(o.output_value for o in orders)
    po_value = sum(r.value for r in po_lines)
    affected_parts = {r.material_id for r in po_lines} | {p for o in orders for p in o.blocking_parts}
    totals = ImpactTotals(
        units_blocked=sum(o.units_blocked for o in orders), output_value=output_value, po_value=po_value,
        rar=output_value + po_value, parts_affected=len(affected_parts),
        pos_affected=len({r.po_id for r in po_lines}), po_lines_affected=len(po_lines),
        products_affected=tuple(sorted({o.product_id for o in orders})), orders_blocked=len(orders),
        max_delay_days=max((delays[p].days for p in affected_parts if p in delays), default=0),
        max_po_slip_days=max((r.slip_days for r in po_lines), default=0),
    )
    return Impact(
        part_delays=tuple(PartDelay(p, d.days, d.cause_site, d.via_site) for p, d in sorted(delays.items())),
        po_lines=po_lines, production_orders=tuple(orders), sales_orders=tuple(sales_rows), totals=totals,
        baseline_short=baseline_short(without_event, order_by_id, d0), blocked_parts=blocked_parts,
    )


def baseline_short(run: _Run, order_by_id: Mapping[str, object], d0: date) -> tuple[BaselineShort, ...]:
    return tuple(
        BaselineShort(b.order_id, b.units, b.parts, None if b.ready is None else d0 + timedelta(days=b.ready))
        for _, b in sorted(run.blocked.items())
    )
