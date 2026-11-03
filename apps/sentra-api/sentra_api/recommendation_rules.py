"""R-15 `simulate_plan` (TL-15), the option builders (R-14 v2 options E and S, R-15 v2 options A, B, C, D and R) and the choice
between them (TL-23) as pure functions. No DB, HTTP, clock, or environment access: today (D0) and the master data come as arguments.

Options, actions, the choice and `rule_reason` are decided here, never by an LLM (G-06); an LLM only writes the text that
explains them (G-04).
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sentra_api.erp import ErpData, PoLine
from sentra_api.impact_engine import Impact
from sentra_api.priority import format_idr

ACTION_TYPES = (
    "CHANGE_PO_DATE", "CREATE_PR_PO", "REDUCE_OR_CANCEL_PO", "RESCHEDULE_PRODUCTION_ORDER", "RESCHEDULE_SALES_ORDER",
    "EXPEDITE_SHIPMENT",
)
SHIPPING_CONDITIONS = ("ROAD", "SEA", "AIR")
INCOTERMS = ("FCA", "FOB")
# Payload keys the simulation needs (API_CONTRACT G.3 v1.11 lists the old values of some actions too; they are not used here).
REQUIRED_PAYLOAD = {
    "CHANGE_PO_DATE": ("po_id", "item_no", "new_date"),
    "CREATE_PR_PO": ("material_id", "supplier_id", "qty", "price", "delivery_date", "shipping_condition", "incoterms",
                     "freight_per_unit"),
    "REDUCE_OR_CANCEL_PO": ("po_id", "item_no", "new_qty"),
    "RESCHEDULE_PRODUCTION_ORDER": ("order_id", "new_date"),
    "RESCHEDULE_SALES_ORDER": ("so_id", "new_date"),
    "EXPEDITE_SHIPMENT": ("po_id", "item_no", "old_shipping_condition", "new_shipping_condition", "old_incoterms",
                          "new_incoterms", "freight_cost"),
}


class PlanError(ValueError):
    """An action cannot be applied to the ERP data; `index` is its position in the plan."""

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"actions[{index}]: {message}")
        self.index = index
        self.message = message


@dataclass(frozen=True)
class PlanAction:
    type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class Metrics:
    """API_CONTRACT G.2."""

    units_blocked: int
    output_value: int
    po_value: int
    rar: int
    so_delayed: int
    premium: int
    extra_duty: int | None = None  # v1.11: estimated extra duty of a tariff case; None for the other cases


@dataclass(frozen=True)
class PlanSimulation:
    before: Metrics
    after: Metrics
    impact_after: Impact

    def to_dict(self) -> dict[str, Any]:
        return {"before": asdict(self.before), "after": asdict(self.after), "impact_after": self.impact_after.to_dict()}


def _date(index: int, payload: Mapping[str, Any], key: str) -> date:
    value = payload[key]
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PlanError(index, f"{key} must be a date YYYY-MM-DD") from None


def _int(index: int, payload: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlanError(index, f"{key} must be an integer >= {minimum}")
    return value


def _choice(index: int, payload: Mapping[str, Any], key: str, allowed: Sequence[str]) -> str:
    value = payload[key]
    if value not in allowed:
        raise PlanError(index, f"{key} must be one of {list(allowed)}")
    return value


def _check(index: int, action: PlanAction) -> None:
    if action.type not in REQUIRED_PAYLOAD:
        raise PlanError(index, f"type must be one of {list(ACTION_TYPES)}")
    missing = [key for key in REQUIRED_PAYLOAD[action.type] if key not in action.payload]
    if missing:
        raise PlanError(index, f"payload is missing {missing}")


def apply_actions(erp: ErpData, actions: Iterable[PlanAction]) -> ErpData:
    """A copy of the ERP data with the hypothetical actions applied, in order. The input is not changed."""
    po_lines = list(erp.po_lines)
    orders = list(erp.production_orders)
    sales = list(erp.sales_orders)
    materials = {m.material_id for m in erp.materials}
    new_lines = 0

    def position(items: Sequence, matches: Callable[[Any], bool], index: int, what: str) -> int:
        for i, item in enumerate(items):
            if matches(item):
                return i
        raise PlanError(index, f"{what} not found")

    for index, action in enumerate(actions):
        _check(index, action)
        p = action.payload
        if action.type == "CHANGE_PO_DATE":
            i = position(po_lines, lambda l: (l.po_id, l.item_no) == (p["po_id"], p["item_no"]), index, "PO line")
            po_lines[i] = replace(po_lines[i], confirmed_date=_date(index, p, "new_date"))
        elif action.type == "REDUCE_OR_CANCEL_PO":
            i = position(po_lines, lambda l: (l.po_id, l.item_no) == (p["po_id"], p["item_no"]), index, "PO line")
            qty = _int(index, p, "new_qty", minimum=0)
            if qty >= po_lines[i].qty:
                raise PlanError(index, "new_qty must be lower than the current quantity")
            po_lines[i] = replace(po_lines[i], qty=qty, status="CANCELLED" if qty == 0 else po_lines[i].status)
        elif action.type == "CREATE_PR_PO":
            if p["material_id"] not in materials:
                raise PlanError(index, "material not found")
            delivery = _date(index, p, "delivery_date")
            new_lines += 1
            po_lines.append(PoLine(
                po_id=f"PR-{new_lines:04d}", item_no=10, material_id=p["material_id"], supplier_id=p["supplier_id"],
                qty=_int(index, p, "qty", minimum=1), net_price=_int(index, p, "price", minimum=0),
                requested_date=delivery, confirmed_date=delivery,
                incoterms=_choice(index, p, "incoterms", INCOTERMS),
                shipping_condition=_choice(index, p, "shipping_condition", SHIPPING_CONDITIONS),
                freight_per_unit=_int(index, p, "freight_per_unit", minimum=0),
            ))
        elif action.type == "EXPEDITE_SHIPMENT":
            i = position(po_lines, lambda l: (l.po_id, l.item_no) == (p["po_id"], p["item_no"]), index, "PO line")
            _int(index, p, "freight_cost", minimum=0)
            po_lines[i] = replace(po_lines[i], shipping_condition=_choice(index, p, "new_shipping_condition", SHIPPING_CONDITIONS),
                                  incoterms=_choice(index, p, "new_incoterms", INCOTERMS))
        elif action.type == "RESCHEDULE_PRODUCTION_ORDER":
            i = position(orders, lambda o: o.order_id == p["order_id"], index, "production order")
            orders[i] = replace(orders[i], start_date=_date(index, p, "new_date"))
        else:  # RESCHEDULE_SALES_ORDER
            i = position(sales, lambda s: s.so_id == p["so_id"], index, "sales order")
            sales[i] = replace(sales[i], confirmed_delivery_date=_date(index, p, "new_date"))
    return replace(erp, po_lines=tuple(po_lines), production_orders=tuple(orders), sales_orders=tuple(sales))


def premium(erp: ErpData, actions: Iterable[PlanAction]) -> int:
    """Extra cost of the plan (R-15 v2): (alternate price - standard price + air freight per unit) x qty for every purchase,
    plus the freight cost of every expedited line. The surcharge of a backup site (option C) is not an action."""
    standard = {m.material_id: m.std_price for m in erp.materials}
    total = 0
    for a in actions:
        if a.type == "CREATE_PR_PO":
            p = a.payload
            total += (p["price"] - standard[p["material_id"]] + p.get("freight_per_unit", 0)) * p["qty"]
        elif a.type == "EXPEDITE_SHIPMENT":
            total += a.payload["freight_cost"]
    return total


def _rescheduled_sales_orders(erp: ErpData, actions: Iterable[PlanAction]) -> set[str]:
    """Sales orders that the plan moves later than their current delivery date."""
    current = {s.so_id: s.confirmed_delivery_date for s in erp.sales_orders}
    return {a.payload["so_id"] for a in actions
            if a.type == "RESCHEDULE_SALES_ORDER" and date.fromisoformat(str(a.payload["new_date"])) > current[a.payload["so_id"]]}


def metrics_of(impact: Impact, *, so_delayed: int, premium_: int = 0) -> Metrics:
    totals = impact.totals
    extra_duty = None if impact.tariff is None else impact.tariff.extra_duty
    return Metrics(totals.units_blocked, totals.output_value, totals.po_value, totals.rar, so_delayed, premium_, extra_duty)


def simulate_plan(
    erp: ErpData, actions: Sequence[PlanAction], evaluate: Callable[[ErpData], Impact]
) -> PlanSimulation:
    """Before / After of a plan. `evaluate` computes the case impact on a given ERP state (same engine for both).

    Sales orders delayed after the plan = those the engine still delays + those the plan itself moves later.
    """
    changed = apply_actions(erp, actions)
    impact_before = evaluate(erp)
    impact_after = evaluate(changed)
    delayed_after = {s.so_id for s in impact_after.sales_orders} | _rescheduled_sales_orders(erp, actions)
    return PlanSimulation(
        before=metrics_of(impact_before, so_delayed=len(impact_before.sales_orders)),
        after=metrics_of(impact_after, so_delayed=len(delayed_after), premium_=premium(erp, actions)),
        impact_after=impact_after,
    )


# --- options (R-14 v2 options E and S, R-15 v2 options A, B, C, D and R) ----------------------------------------------------

PREMIUM_MAX_RATIO = Decimal("0.10")  # RULES_AND_GOLDEN section 3, parameter PREMIUM_MAX_RATIO
CLOSE_CALL_BAND = Decimal(2)  # RULES_AND_GOLDEN 3A, parameter CLOSE_CALL_BAND_PP (default; the service passes the configured one)
OPTION_TITLES = {
    "A": "Protect production", "B": "Reschedule", "C": "Activate supplier backup site", "D": "Expedite shipment",
    "E": "Pull in before effective date", "R": "Update ERP dates only", "S": "Switch source outside tariff country",
}
PENDING_LABEL = "Pending supplier confirmation"
PHYSICAL_RISKS = ("FLOOD", "LANDSLIDE", "FIRE", "EARTHQUAKE")  # the risk types option C (backup site) answers
HOME_COUNTRY = "ID"  # the country of the plant: its suppliers ship by road (R-14 v2 "domestic")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_NOTHING_BLOCKED = "No option: no production units are blocked, so there is nothing to protect or reschedule"


@dataclass(frozen=True)
class AlternateInfo:
    """An approved alternate source of the master data (`alternate_source`) and the country of its site."""

    part_id: str
    site_id: str
    price: int
    lead_time_days: int
    max_qty: int
    air_lead_time_days: int | None = None
    air_freight_per_unit: int | None = None
    country: str | None = None


@dataclass(frozen=True)
class SiteFact:
    """A candidate site of the case: its standalone RAR (R-06), its BCP score, its d(s) now and what the master data and the
    answers say about its backup site (R-15 v2 option C)."""

    site_id: str
    tier: str
    rar: int
    bcp_score: int = 100
    recovery_days: int = 0
    backup_site: str | None = None
    backup_ramp_days: int | None = None
    backup_surcharge_pct: float | None = None
    backup_ramp_confirmed: int | None = None
    backup_declined: bool = False


@dataclass(frozen=True)
class TierLinkFact:
    """A tier-2 -> tier-1 link with the name of the component and the parts it feeds (for the step `Start qualifying…`)."""

    tier2_site: str
    tier1_site: str
    component: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class RequestState:
    """State of the supplier request of one kind for one site in the case (`supplier_request`)."""

    status: str  # REQUESTED | ANSWERED | NO_RESPONSE
    answer: str | None = None


@dataclass(frozen=True)
class Shortfall:
    """R-15 point 1: units of a part that the blocked orders miss, and the earliest start among those orders."""

    material_id: str
    qty: int
    first_start: date


@dataclass(frozen=True)
class OptionAction:
    """One ERP action of an option (EP-15 `actions`; payload keys by type as in API_CONTRACT G.3)."""

    seq: int
    type: str
    object: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "object": self.object, "payload": dict(self.payload)}


@dataclass(frozen=True)
class ConfirmationSite:
    site_id: str
    status: str | None  # None = not requested yet
    answer: str | None


@dataclass(frozen=True)
class Confirmation:
    """EP-15 `confirmation`: the supplier answers the option waits for."""

    request_type: str
    sites: tuple[ConfirmationSite, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"request_type": self.request_type, "sites": [asdict(s) for s in self.sites]}


@dataclass(frozen=True)
class Option:
    option: str
    title: str
    available: bool
    unavailable_reason: str | None
    label: str | None
    before: Metrics
    after: Metrics | None  # None while the option is unavailable
    actions: tuple[OptionAction, ...]
    pending_confirmation: bool = False
    confirmation: Confirmation | None = None
    saving: int | None = None  # options E and S (R-14 v2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "option": self.option, "title": self.title, "available": self.available,
            "unavailable_reason": self.unavailable_reason, "label": self.label,
            "pending_confirmation": self.pending_confirmation,
            "confirmation": None if self.confirmation is None else self.confirmation.to_dict(),
            "saving": self.saving, "before": asdict(self.before),
            "after": None if self.after is None else asdict(self.after), "actions": [a.to_dict() for a in self.actions],
        }


@dataclass(frozen=True)
class Recommendation:
    """TL-23 / EP-15 without the case id, the version, the rationales and the escalation."""

    preliminary: bool
    recommended_option: str | None
    rule_reason: str
    options: tuple[Option, ...]
    non_erp_steps: tuple[str, ...]
    # (site_id, request_type) the recommended option still needs and nobody has asked yet (R-12 v2)
    confirmations_needed: tuple[tuple[str, str], ...] = ()
    # a protecting option costs within CLOSE_CALL_BAND points of the 10% premium policy (R-22 M2)
    close_call: bool = field(default=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preliminary": self.preliminary, "recommended_option": self.recommended_option,
            "rule_reason": self.rule_reason, "options": [o.to_dict() for o in self.options],
            "non_erp_steps": list(self.non_erp_steps),
            "confirmations_needed": [{"site_id": site, "request_type": kind} for site, kind in self.confirmations_needed],
        }


_Step = tuple[str, str, Mapping[str, Any]]  # (action type, object label, payload)
Evaluate = Callable[..., Impact]  # evaluate(erp) or evaluate(erp, recovery): the case impact on an ERP state


def _numbered(steps: Iterable[_Step]) -> tuple[OptionAction, ...]:
    return tuple(OptionAction(seq, kind, obj, payload) for seq, (kind, obj, payload) in enumerate(steps, start=1))


def _line_label(po_id: str, item_no: int) -> str:
    return f"{po_id}/{item_no}"


def _ship_default(country: str | None) -> tuple[str, str]:
    """Shipping condition and incoterms of a new PO line to a site in `country` (R-14 v2): domestic by road, abroad by sea."""
    return ("ROAD", "FCA") if country == HOME_COUNTRY else ("SEA", "FOB")


def shortfalls(erp: ErpData, impact: Impact) -> tuple[Shortfall, ...]:
    """R-15 point 1: per part, Σ units blocked × quantity per unit over the blocked orders of the impact; sorted by part."""
    per_unit = {(b.product_id, b.material_id): b.qty_per_unit for b in erp.bom_items}
    orders = {o.order_id: o for o in erp.production_orders}
    quantity: dict[str, int] = {}
    first_start: dict[str, date] = {}
    for blocked in impact.blocked_parts:
        order = orders[blocked.order_id]
        part = blocked.material_id
        quantity[part] = quantity.get(part, 0) + blocked.units * per_unit[(order.product_id, part)]
        first_start[part] = min(first_start.get(part, order.start_date), order.start_date)
    return tuple(Shortfall(part, quantity[part], first_start[part]) for part in sorted(quantity))


def _date_changes(erp: ErpData, impact: Impact, skip: frozenset[tuple[str, int]] = frozenset()) -> list[_Step]:
    """`CHANGE_PO_DATE` for every PO line the event delays: the confirmed date becomes the new ETA (R-03).
    A line that the plan cancels, or whose date already is that late, needs no change."""
    current = {(l.po_id, l.item_no): l.confirmed_date for l in erp.po_lines}
    changes: list[_Step] = []
    for line in impact.po_lines:
        key = (line.po_id, line.item_no)
        if key in skip or current[key] >= line.new_eta:
            continue
        changes.append(("CHANGE_PO_DATE", _line_label(*key), {
            "po_id": line.po_id, "item_no": line.item_no, "old_date": current[key].isoformat(),
            "new_date": line.new_eta.isoformat(),
        }))
    return changes


def _simulated(
    erp: ErpData, evaluate: Callable[[ErpData], Impact], option: str, actions: tuple[OptionAction, ...], *,
    premium_: int | None = None, **extra: Any,
) -> Option:
    """The option with Before / After from `simulate_plan`; `premium_` replaces the premium the plan itself implies."""
    result = simulate_plan(erp, [PlanAction(a.type, a.payload) for a in actions], evaluate)
    after = result.after if premium_ is None else replace(result.after, premium=premium_)
    return Option(option, OPTION_TITLES[option], True, None, None, result.before, after, actions, **extra)


def _unavailable(option: str, reason: str, before: Metrics) -> Option:
    return Option(option, OPTION_TITLES[option], False, reason, None, before, None, ())


@dataclass(frozen=True)
class _Source:
    alternate: AlternateInfo
    lead: int  # days until the goods arrive: the standard lead time, or the air lead time for the air variant
    mode: str  # STANDARD | AIR
    freight: int  # air freight per unit (0 for the standard variant)


def _pick_sources(
    needs: Sequence[Shortfall], alternates: Sequence[AlternateInfo], d0: date
) -> tuple[dict[str, _Source], list[str]]:
    """R-15 v2 option A: for each short part the cheapest approved source that is in time (by the standard lead time, else by
    air when the source has air data) and big enough, else the reasons."""
    chosen: dict[str, _Source] = {}
    missing: list[str] = []
    problems: list[str] = []
    for need in needs:
        sources = [a for a in alternates if a.part_id == need.material_id]
        days = (need.first_start - d0).days
        if not sources:
            missing.append(need.material_id)
            continue
        in_time: list[AlternateInfo] = []
        feasible: list[tuple[int, int, str, _Source]] = []
        for a in sources:
            if a.lead_time_days <= days:
                in_time.append(a)
                if a.max_qty >= need.qty:
                    feasible.append((a.price, a.lead_time_days, a.site_id, _Source(a, a.lead_time_days, "STANDARD", 0)))
            elif a.air_lead_time_days is not None and a.air_lead_time_days <= days:
                in_time.append(a)
                if a.max_qty >= need.qty:
                    freight = a.air_freight_per_unit or 0
                    feasible.append((a.price + freight, a.air_lead_time_days, a.site_id,
                                     _Source(a, a.air_lead_time_days, "AIR", freight)))
        if feasible:
            chosen[need.material_id] = min(feasible, key=lambda f: f[:3])[3]
        elif not in_time:
            lead = min(min(a.lead_time_days, a.air_lead_time_days) if a.air_lead_time_days is not None else a.lead_time_days
                       for a in sources)
            problems.append(f"Alternate source for {need.material_id} is too slow: lead time {lead} days, "
                            f"production starts in {days} days")
        else:
            largest = max(a.max_qty for a in in_time)
            problems.append(f"Alternate source for {need.material_id} is too small: maximum quantity {largest}, "
                            f"shortage {need.qty}")
    reasons = ([f"No approved alternate source for {', '.join(missing)}"] if missing else []) + problems
    return chosen, reasons


def option_a(
    erp: ErpData, impact: Impact, evaluate: Callable[[ErpData], Impact], d0: date, alternates: Sequence[AlternateInfo]
) -> Option:
    """Protect production (R-15 v2): buy the shortage from approved alternate sources (by air when only that is in time), cut the
    delayed PO lines by the same quantity when that adds no blocked unit, and move the dates of the delayed lines that remain."""
    before = metrics_of(impact, so_delayed=len(impact.sales_orders))
    needs = shortfalls(erp, impact)
    sources, reasons = _pick_sources(needs, alternates, d0)
    if reasons or not needs:
        return _unavailable("A", "; ".join(reasons) or "No shortage to cover", before)

    purchases: list[_Step] = []
    for n in needs:
        source = sources[n.material_id]
        if source.mode == "AIR":
            ship, inc = "AIR", "FCA"
        else:
            ship, inc = _ship_default(source.alternate.country)
        purchases.append(("CREATE_PR_PO", f"{n.material_id} @ {source.alternate.site_id}", {
            "material_id": n.material_id, "supplier_id": source.alternate.site_id, "qty": n.qty,
            "price": source.alternate.price, "delivery_date": (d0 + timedelta(days=source.lead)).isoformat(),
            "shipping_condition": ship, "incoterms": inc, "freight_per_unit": source.freight,
        }))

    def units_after(extra: Sequence[_Step]) -> int:
        trial = apply_actions(erp, [PlanAction(kind, payload) for kind, _, payload in [*purchases, *extra]])
        return evaluate(trial).totals.units_blocked

    ceiling = units_after([])
    reductions: list[_Step] = []
    for need in needs:
        delayed = sorted((l for l in impact.po_lines if l.material_id == need.material_id),
                         key=lambda l: (l.confirmed_date, l.po_id, l.item_no))
        if not delayed:
            continue
        line = delayed[0]
        proposal: _Step = ("REDUCE_OR_CANCEL_PO", _line_label(line.po_id, line.item_no), {
            "po_id": line.po_id, "item_no": line.item_no, "old_qty": line.qty,
            "new_qty": line.qty - min(line.qty, need.qty),
        })
        if units_after([*reductions, proposal]) <= ceiling:  # R-15 point 2: no new blocked unit
            reductions.append(proposal)
    cancelled = frozenset((p["po_id"], p["item_no"]) for _, _, p in reductions if p["new_qty"] == 0)
    actions = _numbered([*purchases, *reductions, *_date_changes(erp, impact, cancelled)])
    return _simulated(erp, evaluate, "A", actions)


def option_b(erp: ErpData, impact: Impact, evaluate: Callable[[ErpData], Impact]) -> Option:
    """Reschedule (R-15 point 3): the delayed PO lines, the blocked production orders and their sales orders move to the
    dates of the impact calculation. An order or sales order without a date in the horizon cannot be moved."""
    start = {o.order_id: o.start_date for o in erp.production_orders}
    confirmed = {s.so_id: s.confirmed_delivery_date for s in erp.sales_orders}
    moves = _date_changes(erp, impact)
    moves += [("RESCHEDULE_PRODUCTION_ORDER", o.order_id, {
        "order_id": o.order_id, "old_date": start[o.order_id].isoformat(), "new_date": o.ready_date.isoformat(),
    }) for o in impact.production_orders if o.ready_date is not None and o.ready_date > start[o.order_id]]
    moves += [("RESCHEDULE_SALES_ORDER", s.so_id, {
        "so_id": s.so_id, "old_date": confirmed[s.so_id].isoformat(), "new_date": s.new_date.isoformat(),
    }) for s in impact.sales_orders if s.new_date is not None and s.new_date > confirmed[s.so_id]]
    return _simulated(erp, evaluate, "B", _numbered(moves))


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def option_c(
    erp: ErpData, impact: Impact, evaluate: Evaluate, sites: Mapping[str, SiteFact], recovery: Mapping[str, int],
    requests: Mapping[tuple[str, str], RequestState],
) -> Option:
    """Activate supplier backup site (R-15 v2): the sites that cause the blocking parts and have a backup site that is faster than
    their own recovery. The premium is the surcharge on the PO value those sites delay. Always waits for the supplier."""
    before = metrics_of(impact, so_delayed=len(impact.sales_orders))
    blocking = {p for o in impact.production_orders for p in o.blocking_parts}
    cause = {d.part_id: d.cause_site for d in impact.part_delays}
    causes = sorted({cause[p] for p in blocking if p in cause})

    def fact(site_id: str) -> SiteFact | None:
        return sites.get(site_id)

    declined = [s for s in causes if fact(s) is not None and fact(s).backup_declined]
    qualified = [s for s in causes if fact(s) is not None and not fact(s).backup_declined
                 and fact(s).backup_ramp_days is not None and fact(s).backup_ramp_days < recovery.get(s, 0)]
    if not qualified:
        if declined:
            return _unavailable("C", f"Supplier declined backup activation for {', '.join(declined)}", before)
        return _unavailable("C", f"No qualified backup site for {', '.join(causes)}", before)
    faster = {**recovery, **{s: fact(s).backup_ramp_days for s in qualified}}
    after_impact = evaluate(erp, faster)
    surcharge = sum(
        _round_half_up(Decimal(str(fact(s).backup_surcharge_pct or 0)) / 100
                       * sum(l.value for l in impact.po_lines if l.cause_site == s))
        for s in qualified
    )
    actions = _numbered(_date_changes(erp, after_impact))
    return Option(
        "C", OPTION_TITLES["C"], True, None, PENDING_LABEL, before,
        metrics_of(after_impact, so_delayed=len(after_impact.sales_orders), premium_=surcharge), actions,
        pending_confirmation=True, confirmation=_confirmation("BACKUP_ACTIVATION", qualified, requests),
    )


def option_d(erp: ErpData, impact: Impact, evaluate: Callable[[ErpData], Impact]) -> Option:
    """Expedite shipment (R-15 v2, `LOGISTICS` only): every PO line the event delays directly (not through tier 2) whose part
    blocks production goes by air at the expedite rate of the part."""
    before = metrics_of(impact, so_delayed=len(impact.sales_orders))
    blocking = {p for o in impact.production_orders for p in o.blocking_parts}
    lines = [l for l in impact.po_lines if l.via_site is None and l.material_id in blocking]
    if not lines:
        return _unavailable("D", "No directly delayed PO line blocks production", before)
    rate = {m.material_id: m.expedite_cost_per_unit for m in erp.materials}
    no_rate = sorted({l.material_id for l in lines if rate.get(l.material_id) is None})
    if no_rate:
        return _unavailable("D", f"No expedite rate for {', '.join(no_rate)}", before)
    shipping = {(l.po_id, l.item_no): (l.shipping_condition, l.incoterms) for l in erp.po_lines}
    steps: list[_Step] = []
    for line in lines:
        old_ship, old_inc = shipping[(line.po_id, line.item_no)]
        steps.append(("EXPEDITE_SHIPMENT", _line_label(line.po_id, line.item_no), {
            "po_id": line.po_id, "item_no": line.item_no, "old_shipping_condition": old_ship,
            "new_shipping_condition": "AIR", "old_incoterms": old_inc, "new_incoterms": "FCA",
            "freight_cost": line.qty * rate[line.material_id],
        }))
    return _simulated(erp, evaluate, "D", _numbered(steps))


def option_r(erp: ErpData, impact: Impact) -> Option | None:
    """Update ERP dates only (R-15 v2): nothing is blocked, but delayed PO lines still carry an older date. None when no line does."""
    changes = _date_changes(erp, impact)
    if not changes:
        return None
    before = metrics_of(impact, so_delayed=len(impact.sales_orders))
    return Option("R", OPTION_TITLES["R"], True, None, None, before, replace(before), _numbered(changes))


def _answered(state: RequestState | None, answer: str) -> bool:
    return state is not None and state.status == "ANSWERED" and state.answer == answer


def _confirmation(kind: str, sites: Iterable[str], requests: Mapping[tuple[str, str], RequestState]) -> Confirmation:
    rows = []
    for site in sites:
        state = requests.get((site, kind))
        rows.append(ConfirmationSite(site, None if state is None else state.status, None if state is None else state.answer))
    return Confirmation(kind, tuple(rows))


def _percent(part: int, whole: int) -> Decimal:
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _protecting_phrase(option: Option, protected_output: int) -> str:
    if not option.available or option.after is None:
        return f"Option {option.option} unavailable ({option.unavailable_reason})"
    if option.after.units_blocked > 0:
        return f"Option {option.option} leaves {option.after.units_blocked} units blocked"
    premium_ = option.after.premium
    return (f"premium of Option {option.option} {format_idr(premium_)} = {_percent(premium_, protected_output)}% "
            f"of protected output (> {int(PREMIUM_MAX_RATIO * 100)}%)")


def choose_disruption(
    options: Sequence[Option], protected_output: int, band: Decimal | int = CLOSE_CALL_BAND
) -> tuple[str, str, bool]:
    """R-15 v2 choice for a disruption with blocked units: the protecting option (A, C, D) that is available, leaves no unit
    blocked and costs at most 10% of the output it protects; the smallest premium wins (tie: the letter), else B.

    Returns the option, the `rule_reason` and whether a protecting option costs within `band` points (CLOSE_CALL_BAND_PP, default
    `CLOSE_CALL_BAND`) of the 10% limit (R-22 M2).
    """
    protecting = [o for o in options if o.option in ("A", "C", "D")]
    limit = f"{int(PREMIUM_MAX_RATIO * 100)}%"

    def protects(o: Option) -> bool:
        return o.available and o.after is not None and o.after.units_blocked == 0 and protected_output > 0

    fits = [o for o in protecting if protects(o) and Decimal(o.after.premium) <= PREMIUM_MAX_RATIO * protected_output]
    close = any(protects(o) and abs(Decimal(o.after.premium) * 100 / Decimal(protected_output) - 10) <= Decimal(band)
                for o in protecting)
    if fits:
        best = min(fits, key=lambda o: (o.after.premium, o.option))
        reason = (f"Option {best.option}: premium {format_idr(best.after.premium)} = "
                  f"{_percent(best.after.premium, protected_output)}% of protected output (≤ {limit})")
        if best.pending_confirmation:
            reason += " — pending supplier confirmation"
        return best.option, reason, close
    return "B", "Option B: " + "; ".join(_protecting_phrase(o, protected_output) for o in protecting), close


def choose_tariff(options: Sequence[Option], duty: int | None) -> tuple[str | None, str]:
    """R-14 v2 choice for a tariff case: the available option with the largest saving (tie: E), if any saves money.
    Returns the option (None = no recommendation) and the `rule_reason`."""
    if not options:
        return None, "No option: no PO line is affected"
    if duty is None:
        return None, "No option is recommended by rule: the tariff rate or effective date is not stated in the source"
    saving = [o for o in options if o.available and o.saving is not None and o.saving > 0]
    if not saving:
        unavailable = [f"Option {o.option} unavailable ({o.unavailable_reason})" for o in options if not o.available]
        return None, "No option: " + ("; ".join(unavailable) or "no available option saves money")
    best = min(saving, key=lambda o: (-o.saving, o.option))
    if best.option == "E":
        reason = f"Option E: avoids extra duty {format_idr(best.saving)}"
    else:
        reason = f"Option S: saves {format_idr(best.saving)} after the higher alternate price"
    if best.pending_confirmation:
        reason += " — pending supplier confirmation"
    return best.option, reason


def option_e(
    erp: ErpData, impact: Impact, evaluate: Callable[[ErpData], Impact], d0: date,
    requests: Mapping[tuple[str, str], RequestState],
) -> Option | None:
    """Pull in before the effective date (R-14 v2): every line that bears the tariff gets the day before it takes effect.
    None when no line bears the tariff. It waits for every supplier of those lines to answer `PULL_IN` with YES."""
    tariff = impact.tariff
    if tariff is None or not impact.po_lines:
        return None
    before = metrics_of(impact, so_delayed=0)
    if tariff.effective_date is None:
        return _unavailable("E", "The effective date is not stated in the source", before)
    target = tariff.effective_date - timedelta(days=1)
    if target < d0:
        return _unavailable("E", "The tariff is already in effect, so an earlier date cannot avoid it", before)
    sites = sorted({l.supplier_id for l in impact.po_lines})
    declined = [s for s in sites if _answered(requests.get((s, "PULL_IN")), "NO")]
    if declined:
        return _unavailable("E", f"Supplier declined the earlier date ({', '.join(declined)})", before)
    current = {(l.po_id, l.item_no): l.confirmed_date for l in erp.po_lines}
    actions = _numbered(
        ("CHANGE_PO_DATE", _line_label(l.po_id, l.item_no), {
            "po_id": l.po_id, "item_no": l.item_no, "old_date": current[(l.po_id, l.item_no)].isoformat(),
            "new_date": target.isoformat(),
        }) for l in impact.po_lines
    )
    pending = any(not _answered(requests.get((s, "PULL_IN")), "YES") for s in sites)
    option = _simulated(erp, evaluate, "E", actions, pending_confirmation=pending,
                        confirmation=_confirmation("PULL_IN", sites, requests))
    saving = None if option.before.extra_duty is None else option.before.extra_duty - (option.after.extra_duty or 0)
    return replace(option, label=PENDING_LABEL if pending else None, saving=saving)


def option_s(
    erp: ErpData, impact: Impact, evaluate: Callable[[ErpData], Impact], d0: date, alternates: Sequence[AlternateInfo]
) -> Option:
    """Switch source outside the tariff country (R-14 v2): per line that bears the tariff, buy from the cheapest approved alternate
    in another country that is in time, when it costs less than the landed price (price + tariff) of the line."""
    tariff = impact.tariff
    before = metrics_of(impact, so_delayed=0)
    if tariff.tariff_pp is None:
        return _unavailable("S", "The tariff rate is not stated in the source", before)
    rate = Decimal(str(tariff.tariff_pp))
    line_of = {(l.po_id, l.item_no): l for l in erp.po_lines}
    reasons: list[str] = []
    steps: list[_Step] = []
    used: dict[tuple[str, str], int] = {}
    premium_ = 0
    for row in sorted(impact.po_lines, key=lambda r: (r.po_id, r.item_no)):
        abroad = [a for a in alternates if a.part_id == row.material_id and a.country != tariff.country]
        if not abroad:
            reasons.append(f"No approved alternate outside {tariff.country} for {row.material_id}")
            continue
        due = (row.confirmed_date - d0).days
        in_time = [a for a in abroad if a.lead_time_days <= due]
        if not in_time:
            reasons.append(f"Alternate source for {row.material_id} is too slow: lead time "
                           f"{min(a.lead_time_days for a in abroad)} days, PO line due in {due} days")
            continue
        best = min(in_time, key=lambda a: (a.price, a.lead_time_days, a.site_id))
        qty = min(row.qty, best.max_qty - used.get((row.material_id, best.site_id), 0))
        if qty <= 0:
            reasons.append(f"Alternate source for {row.material_id} has no capacity left")
            continue
        net_price = line_of[(row.po_id, row.item_no)].net_price
        landed = _round_half_up(Decimal(net_price) * (1 + rate / 100))
        if landed - best.price <= 0:
            reasons.append(f"Alternate for {row.material_id} costs {format_idr(best.price, compact=False)} vs landed "
                           f"{format_idr(landed, compact=False)} per unit")
            continue
        used[(row.material_id, best.site_id)] = used.get((row.material_id, best.site_id), 0) + qty
        ship, inc = _ship_default(best.country)
        steps.append(("CREATE_PR_PO", f"{row.material_id} @ {best.site_id}", {
            "material_id": row.material_id, "supplier_id": best.site_id, "qty": qty, "price": best.price,
            "delivery_date": (d0 + timedelta(days=best.lead_time_days)).isoformat(),
            "shipping_condition": ship, "incoterms": inc, "freight_per_unit": 0,
        }))
        steps.append(("REDUCE_OR_CANCEL_PO", _line_label(row.po_id, row.item_no), {
            "po_id": row.po_id, "item_no": row.item_no, "old_qty": row.qty, "new_qty": row.qty - qty,
        }))
        premium_ += (best.price - net_price) * qty
    if not steps:
        return _unavailable("S", reasons[0], before)
    option = _simulated(erp, evaluate, "S", _numbered(steps), premium_=premium_)
    saving = None if option.before.extra_duty is None else option.before.extra_duty - (option.after.extra_duty or 0) - premium_
    return replace(option, saving=saving)


# --- steps outside the ERP (FR-13 v2) -------------------------------------------------------------------------------------

def _long_date(day: date) -> str:
    return f"{day.day:02d} {_MONTHS[day.month - 1]} {day.year}"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _location_steps(
    erp: ErpData, impact: Impact, sites: Sequence[SiteFact], tier_links: Sequence[TierLinkFact]
) -> list[str]:
    """Steps for a disruption (a location event, or a country event with a stated downtime): confirm the dates, inform the
    customers, monitor tier 2, and start a second source for every affected site whose BCP score is below 60."""
    affected = sorted((s for s in sites if s.rar > 0), key=lambda s: s.site_id)
    steps = []
    if affected:
        steps.append(f"Confirm recovery dates with {', '.join(s.site_id for s in affected)}")
    if impact.sales_orders:
        steps.append(f"Inform customers of {', '.join(s.so_id for s in impact.sales_orders)}")
    steps += [f"Monitor tier-2 site {s.site_id} until recovered" for s in affected if s.tier == "2"]
    delayed = {d.part_id for d in impact.part_delays}
    for site in affected:
        if site.bcp_score >= 60:
            continue
        if site.tier == "1":
            items = sorted(m.material_id for m in erp.materials if m.main_supplier_id == site.site_id and m.material_id in delayed)
        else:
            items = sorted({l.component for l in tier_links if l.tier2_site == site.site_id and delayed & set(l.parts)})
        if items:
            steps.append(f"Start qualifying a second source for {', '.join(items)} — {site.site_id} "
                         f"BCP score {site.bcp_score} (HIGH RISK)")
    return steps


def _tariff_steps(impact: Impact, alternates: Sequence[AlternateInfo]) -> list[str]:
    tariff = impact.tariff
    if tariff is None or not impact.po_lines:
        return []
    pos = _unique(l.po_id for l in impact.po_lines)
    po_label = f"PO {pos[0]}" if len(pos) == 1 else f"POs {', '.join(pos)}"
    parts = _unique(l.material_id for l in impact.po_lines)
    if tariff.not_stated:
        names = {"tariff_pp": "tariff rate", "effective_date": "effective date"}
        steps = [f"Confirm the {' and '.join(names[n] for n in tariff.not_stated)} in the source before deciding on {po_label}"]
    else:
        steps = [f"Check whether {po_label} can arrive before {_long_date(tariff.effective_date)} "
                 f"to avoid about {format_idr(tariff.extra_duty)}"]
    steps.append(f"Update landed cost for {', '.join(parts)}")
    if not any(a.part_id in parts for a in alternates):
        steps.append(f"Look for a source outside {tariff.country} (no approved alternate in master data)")
    return steps


def _exposure_steps(impact: Impact, sites: Sequence[SiteFact]) -> list[str]:
    exposure = impact.exposure
    if exposure is None or exposure.po_value <= 0:
        return []
    steps = [f"Review open POs from {exposure.country} worth {format_idr(exposure.po_value)}"]
    affected = sorted(s.site_id for s in sites if s.rar > 0)
    if affected:
        steps.append(f"Ask Buyer to confirm shipment status with {', '.join(affected)}")
    return steps


# --- the recommendation ---------------------------------------------------------------------------------------------------

def _needed(option: Option | None, requests: Mapping[tuple[str, str], RequestState]) -> tuple[tuple[str, str], ...]:
    """R-12 v2: the sites a waiting recommended option needs an answer from, and nobody has asked yet."""
    if option is None or not option.pending_confirmation or option.confirmation is None:
        return ()
    kind = option.confirmation.request_type
    return tuple((s.site_id, kind) for s in option.confirmation.sites if (s.site_id, kind) not in requests)


def recommend(
    erp: ErpData,
    evaluate: Evaluate,
    *,
    d0: date,
    alternates: Sequence[AlternateInfo],
    sites: Sequence[SiteFact],
    preliminary: bool,
    risk_type: str = "",
    tier_links: Sequence[TierLinkFact] = (),
    requests: Mapping[tuple[str, str], RequestState] | None = None,
    close_call_band: Decimal | int = CLOSE_CALL_BAND,
) -> Recommendation:
    """Options, choice and steps for the case whose impact on a given ERP state is `evaluate(erp)` (R-14 v2, R-15 v2).

    A tariff gets options E and S; a disruption (a location event, or a country event with a stated downtime) with blocked units
    gets A and B, C for `FLOOD`/`LANDSLIDE`/`FIRE`/`EARTHQUAKE` and D for `LOGISTICS`, and a choice by rule; with nothing blocked it
    gets R or nothing; other country events get steps only. `evaluate(erp, recovery)` is the same impact with another d(s) per
    site (option C). `requests`: the supplier requests of the case by (site, kind). `preliminary`: an impact request is unanswered.
    """
    requests = requests or {}
    impact = evaluate(erp)
    if impact.tariff is not None:
        return _tariff_recommendation(erp, impact, evaluate, d0, alternates, requests, preliminary)
    if impact.exposure is not None:
        return Recommendation(preliminary, None, "No option: a country-level event without a tariff has no ERP action", (),
                              tuple(_exposure_steps(impact, sites)))
    plain: Callable[[ErpData], Impact] = lambda state: evaluate(state)  # noqa: E731
    steps = tuple(_location_steps(erp, impact, sites, tier_links))
    if impact.totals.units_blocked <= 0:
        reschedule = option_r(erp, impact)
        if reschedule is None:
            return Recommendation(preliminary, None, _NOTHING_BLOCKED, (), steps)
        count = len(reschedule.actions)
        return Recommendation(
            preliminary, "R", f"Option R: no production order is blocked; update {count} delayed PO line(s) to the new ETA",
            (reschedule,), steps,
        )
    options = [option_a(erp, impact, plain, d0, alternates), option_b(erp, impact, plain)]
    if risk_type in PHYSICAL_RISKS:
        recovery = {s.site_id: s.recovery_days for s in sites}
        options.append(option_c(erp, impact, evaluate, {s.site_id: s for s in sites}, recovery, requests))
    if risk_type == "LOGISTICS":
        options.append(option_d(erp, impact, plain))
    chosen, reason, close = choose_disruption(options, impact.totals.output_value, close_call_band)
    picked = next((o for o in options if o.option == chosen), None)
    return Recommendation(preliminary, chosen, reason, tuple(options), steps, _needed(picked, requests), close)


def _tariff_recommendation(
    erp: ErpData, impact: Impact, evaluate: Evaluate, d0: date, alternates: Sequence[AlternateInfo],
    requests: Mapping[tuple[str, str], RequestState], preliminary: bool,
) -> Recommendation:
    plain: Callable[[ErpData], Impact] = lambda state: evaluate(state)  # noqa: E731
    pull_in = option_e(erp, impact, plain, d0, requests)
    steps = tuple(_tariff_steps(impact, alternates))
    if pull_in is None:  # no line bears the tariff
        chosen, reason = choose_tariff((), impact.tariff.extra_duty)
        return Recommendation(preliminary, chosen, reason, (), steps)
    options = (pull_in, option_s(erp, impact, plain, d0, alternates))
    chosen, reason = choose_tariff(options, impact.tariff.extra_duty)
    picked = next((o for o in options if o.option == chosen), None)
    return Recommendation(preliminary, chosen, reason, options, steps, _needed(picked, requests))


def template_rationale(option: Option, *, recommended: bool, rule_reason: str) -> str | None:
    """The text that replaces an LLM rationale after the number validator failed twice (G-04). It uses only figures of the
    option and the rule reason. None for an option that is not available."""
    if not option.available or option.after is None:
        return None
    before, after = option.before, option.after
    text = (f"{option.title}: units blocked {before.units_blocked} -> {after.units_blocked}; "
            f"revenue at risk {format_idr(before.rar)} -> {format_idr(after.rar)}; extra cost {format_idr(after.premium)}.")
    if before.extra_duty is not None:
        text += f" Extra duty {format_idr(before.extra_duty)} -> {format_idr(after.extra_duty or 0)}."
    if option.label:
        text += f" {option.label}."
    if recommended:
        text += f" Recommended by rule: {rule_reason}."
    return text
