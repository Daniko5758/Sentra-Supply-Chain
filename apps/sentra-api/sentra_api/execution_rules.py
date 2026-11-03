"""R-17 / R-17 v2: what an ERP action must satisfy before it is sent to the ERP, and how it is described afterwards.

Pure functions: the ERP data, the approved alternate sources and today's date (D0) are arguments. The text of a refusal
is shown to the SCM Manager as the `error` of a `FAILED` action (ERR-03), so it names the value and the limit.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from sentra_api.erp import ErpData
from sentra_api.recommendation_rules import ACTION_TYPES, INCOTERMS, REQUIRED_PAYLOAD, SHIPPING_CONDITIONS, AlternateInfo

OPEN_PRODUCTION_STATUSES = ("RELEASED", "PLANNED", "OPEN")


def action_object(action_type: str, payload: Mapping[str, Any]) -> str:
    """The `object` of an action (contract G.3): the PO line, the purchase (`part @ site`), the production or sales order."""
    if action_type == "CREATE_PR_PO":
        return f"{payload['material_id']} @ {payload['supplier_id']}"
    if action_type == "RESCHEDULE_PRODUCTION_ORDER":
        return str(payload["order_id"])
    if action_type == "RESCHEDULE_SALES_ORDER":
        return str(payload["so_id"])
    return f"{payload['po_id']}/{payload['item_no']}"


def action_seq(idempotency_key: str) -> int:
    """`seq` is the third part of the key `{case_id}:{option}:{seq}:v{version}` (the action table has no column for it)."""
    return int(idempotency_key.split(":")[2])


class _Refused(Exception):
    pass


def _date(payload: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(payload[key])
    except (TypeError, ValueError):
        raise _Refused(f"{key} must be a date YYYY-MM-DD") from None


def _not_before_today(value: date, d0: date) -> None:
    if value < d0:
        raise _Refused(f"New date {value.isoformat()} is before today {d0.isoformat()}")


def _integer(payload: Mapping[str, Any], key: str, minimum: int) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _Refused(f"{key} must be an integer >= {minimum}")
    return value


def _choice(payload: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    if payload[key] not in allowed:
        raise _Refused(f"{key} must be one of {list(allowed)}")
    return payload[key]


def _po_line(erp: ErpData, payload: Mapping[str, Any]):
    wanted = (payload["po_id"], payload["item_no"])
    line = next((l for l in erp.po_lines if (l.po_id, l.item_no) == wanted), None)
    if line is None:
        raise _Refused(f"PO line {wanted[0]}/{wanted[1]} was not found")
    if line.status != "OPEN":
        raise _Refused(f"PO line {line.po_id}/{line.item_no} is not open (status {line.status})")
    return line


def _check(action_type: str, payload: Mapping[str, Any], erp: ErpData, alternates: Mapping[tuple[str, str], AlternateInfo],
           d0: date) -> None:
    if action_type == "CHANGE_PO_DATE":
        _not_before_today(_date(payload, "new_date"), d0)
        _po_line(erp, payload)
    elif action_type == "REDUCE_OR_CANCEL_PO":
        line = _po_line(erp, payload)
        new_qty = _integer(payload, "new_qty", 0)
        if new_qty >= line.qty:
            raise _Refused(f"New quantity {new_qty} must be lower than the current quantity {line.qty}")
    elif action_type == "CREATE_PR_PO":
        source = alternates.get((payload["material_id"], payload["supplier_id"]))
        if source is None:
            raise _Refused(f"No approved alternate source for {payload['material_id']} at {payload['supplier_id']}")
        qty = _integer(payload, "qty", 1)
        if qty > source.max_qty:
            raise _Refused(f"Quantity {qty} is above the maximum {source.max_qty} of the alternate source")
        if payload["price"] != source.price:
            raise _Refused(f"Price {payload['price']} differs from the price {source.price} of the alternate source")
        _choice(payload, "shipping_condition", SHIPPING_CONDITIONS)
        _choice(payload, "incoterms", INCOTERMS)
        _integer(payload, "freight_per_unit", 0)
        _not_before_today(_date(payload, "delivery_date"), d0)
    elif action_type == "EXPEDITE_SHIPMENT":
        line = _po_line(erp, payload)
        if line.shipping_condition == "AIR":
            raise _Refused(f"PO line {line.po_id}/{line.item_no} already ships by air")
        if payload["new_shipping_condition"] != "AIR":
            raise _Refused("new_shipping_condition must be AIR")
        _choice(payload, "new_incoterms", INCOTERMS)
        _integer(payload, "freight_cost", 0)
    elif action_type == "RESCHEDULE_PRODUCTION_ORDER":
        new_date = _date(payload, "new_date")
        order = next((o for o in erp.production_orders if o.order_id == payload["order_id"]), None)
        if order is None:
            raise _Refused(f"Production order {payload['order_id']} was not found")
        if order.status not in OPEN_PRODUCTION_STATUSES:
            raise _Refused(f"Production order {order.order_id} is not open (status {order.status})")
        _not_before_today(new_date, d0)
    else:  # RESCHEDULE_SALES_ORDER
        new_date = _date(payload, "new_date")
        sales = next((s for s in erp.sales_orders if s.so_id == payload["so_id"]), None)
        if sales is None:
            raise _Refused(f"Sales order {payload['so_id']} was not found")
        _not_before_today(new_date, d0)
        if new_date < sales.confirmed_delivery_date:
            raise _Refused(f"New date {new_date.isoformat()} is earlier than the current delivery date "
                           f"{sales.confirmed_delivery_date.isoformat()}")


def validate_action(action_type: str, payload: Mapping[str, Any], erp: ErpData,
                    alternates: Mapping[tuple[str, str], AlternateInfo], d0: date) -> str | None:
    """None when the action may go to the ERP, otherwise the reason it may not (R-17: the object exists and is open, the new date
    is not before D0, a reduction is below the current quantity, a purchase is from an approved source within `max_qty` at its
    price, a sales order is not moved earlier; R-17 v2: an expedite only for an open line that does not fly yet)."""
    if action_type not in ACTION_TYPES:
        return f"Unknown action type {action_type}"
    missing = [key for key in REQUIRED_PAYLOAD[action_type] if key not in payload]
    if missing:
        return f"payload is missing {missing}"
    try:
        _check(action_type, payload, erp, alternates, d0)
    except _Refused as refusal:
        return str(refusal)
    return None


def action_document(action_type: str, payload: Mapping[str, Any], erp: ErpData) -> str:
    """The `erp_result.document` of an executed action that is not a purchase: what changed, from the ERP state before the call."""
    if action_type in ("CHANGE_PO_DATE", "REDUCE_OR_CANCEL_PO", "EXPEDITE_SHIPMENT"):
        line = next(l for l in erp.po_lines if (l.po_id, l.item_no) == (payload["po_id"], payload["item_no"]))
        label = f"PO {line.po_id}/{line.item_no}"
        if action_type == "CHANGE_PO_DATE":
            return f"{label} delivery {line.confirmed_date.isoformat()}→{payload['new_date']}"
        if action_type == "EXPEDITE_SHIPMENT":
            return (f"{label} shipping {line.shipping_condition}→{payload['new_shipping_condition']}, "
                    f"incoterms {line.incoterms}→{payload['new_incoterms']}")
        if payload["new_qty"] == 0:
            return f"{label} deleted (deletion indicator L, quantity {line.qty})"
        return f"{label} quantity {line.qty}→{payload['new_qty']}"
    if action_type == "RESCHEDULE_PRODUCTION_ORDER":
        order = next(o for o in erp.production_orders if o.order_id == payload["order_id"])
        return f"Production order {order.order_id} start {order.start_date.isoformat()}→{payload['new_date']}"
    sales = next(s for s in erp.sales_orders if s.so_id == payload["so_id"])
    return f"Sales order {sales.so_id} delivery {sales.confirmed_delivery_date.isoformat()}→{payload['new_date']}"
