"""R-19: schedule status without an event (Schedule Agent)."""

from datetime import date

from sentra_api.impact_engine.engine import HORIZON_DAYS, _simulate, baseline_short
from sentra_api.impact_engine.models import ImpactData, OrderCoverage, PoLineSchedule, ScheduleStatus


def schedule_status(data: ImpactData, d0: date, *, horizon_days: int = HORIZON_DAYS) -> ScheduleStatus:
    """Per PO line: Slipped when confirmed > requested. Per production order: Covered or Short (R-04, D(p) = 0)."""
    lines = []
    for line in sorted(data.erp.po_lines, key=lambda l: (l.po_id, l.item_no)):
        slip = max(0, (line.confirmed_date - line.requested_date).days)
        lines.append(PoLineSchedule(line.po_id, line.item_no, slip, "Slipped" if slip > 0 else "On time"))

    order_by_id = {o.order_id: o for o in data.erp.production_orders}
    short = {b.order_id: b for b in baseline_short(_simulate(data, {}, d0, horizon_days, {}), order_by_id, d0)}
    orders = []
    for order in sorted(data.erp.production_orders, key=lambda o: o.order_id):
        gap = short.get(order.order_id)
        if gap is None:
            orders.append(OrderCoverage(order.order_id, "Covered", (), 0, None))
        else:
            orders.append(OrderCoverage(order.order_id, "Short", gap.short_parts, gap.units_short, gap.ready_date))
    return ScheduleStatus(tuple(lines), tuple(orders))
