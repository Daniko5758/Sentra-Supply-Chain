"""Custom ERP actions of a Meeting Room (tier 1) or an Executive Meeting Room decision (tier 2): Option X, F-07 + F-12.

The meeting may decide on its own ERP actions instead of one of the computed options. The SCM Manager names the object and the new
value; everything else of the payload (old values, price, freight, delivery date, incoterms) is read from the ERP and the approved
alternate sources, never typed in (G-06). Each action is checked with R-17 (`execution_rules.validate_action`) before anything is
written; then the decision note is saved, the actions are proposed and, because the meeting approvers and the SCM Manager approved
them in the same step, approved and sent to the mock S/4 at once, in order, stopping at the first failure (like approve-all).
"""

from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.execution_rules import OPEN_PRODUCTION_STATUSES, action_object, validate_action
from sentra_api.escalation import PARTICIPANT_ORDER
from sentra_api.models import Action, Meeting, SupplierSite
from sentra_api.priority import format_idr
from sentra_api.recommendation_rules import AlternateInfo, PlanAction, _ship_default, premium
from sentra_api.services import case_tools, executor, meeting_service
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.ids import next_id
from sentra_api.services.meeting_records import ParticipantRole
from sentra_api.services.meeting_service import DecisionIn
from sentra_api.services.recommendation_service import CaseRecommendation

CUSTOM_OPTION = "X"
CUSTOM_STEP = "record_custom_actions"  # trace step of the custom actions of a tier-1 Meeting Room
MAX_CUSTOM_ACTIONS = 10
ActionType = Literal["CHANGE_PO_DATE", "CREATE_PR_PO", "REDUCE_OR_CANCEL_PO", "RESCHEDULE_PRODUCTION_ORDER",
                     "RESCHEDULE_SALES_ORDER", "EXPEDITE_SHIPMENT"]


# --- request ---------------------------------------------------------------------------------------------------------------------

class CustomActionIn(BaseModel):
    """One action as the meeting decided it: the object and the new value. Fields that the type does not use are ignored."""

    type: ActionType
    po_id: str | None = None  # CHANGE_PO_DATE, REDUCE_OR_CANCEL_PO, EXPEDITE_SHIPMENT
    item_no: int | None = None
    new_date: str | None = None  # CHANGE_PO_DATE, RESCHEDULE_PRODUCTION_ORDER, RESCHEDULE_SALES_ORDER (YYYY-MM-DD)
    new_qty: int | None = None  # REDUCE_OR_CANCEL_PO (0 = cancel the line)
    order_id: str | None = None  # RESCHEDULE_PRODUCTION_ORDER
    so_id: str | None = None  # RESCHEDULE_SALES_ORDER
    material_id: str | None = None  # CREATE_PR_PO: an approved alternate source (material @ supplier site)
    supplier_id: str | None = None
    qty: int | None = None
    by_air: bool = False  # CREATE_PR_PO: the air variant of the alternate source


class CustomDecisionIn(BaseModel):
    meeting_id: str
    budget_approved: bool = False
    approved_by: list[ParticipantRole]
    notes: str | None = Field(default=None, max_length=1000)
    actions: list[CustomActionIn] = Field(min_length=1, max_length=MAX_CUSTOM_ACTIONS)


# --- catalog (what can be chosen) ------------------------------------------------------------------------------------------------

class CatalogPoLineOut(BaseModel):
    po_id: str
    item_no: int
    material_id: str
    material_name: str
    supplier_id: str
    supplier_name: str
    qty: int
    confirmed_date: str
    shipping_condition: str
    incoterms: str
    expedite_cost_per_unit: int | None
    affected: bool  # the line is in the impact of this case


class CatalogProductionOrderOut(BaseModel):
    order_id: str
    product_id: str
    product_name: str
    qty: int
    start_date: str
    status: str
    affected: bool  # the order is in the impact of this case


class CatalogSalesOrderOut(BaseModel):
    so_id: str
    customer: str
    product_id: str
    product_name: str
    qty: int
    confirmed_delivery_date: str
    affected: bool  # the sales order is in the impact of this case


class CatalogSourceOut(BaseModel):
    material_id: str
    material_name: str
    supplier_id: str
    supplier_name: str
    country: str | None
    price: int
    max_qty: int
    lead_time_days: int
    delivery_date: str
    air_lead_time_days: int | None
    air_freight_per_unit: int | None
    air_delivery_date: str | None
    affected: bool  # the material is delayed or blocking in this case


class CustomCatalogOut(BaseModel):
    """The ERP objects a custom action can name: open PO lines, open production orders, sales orders, approved alternate sources.

    Every list has the objects in the impact of the case (latest snapshot) first, marked `affected`; the others follow, so the
    meeting can still name them on purpose."""

    case_id: str
    option: str
    today: str
    max_actions: int
    po_lines: list[CatalogPoLineOut]
    production_orders: list[CatalogProductionOrderOut]
    sales_orders: list[CatalogSalesOrderOut]
    alternate_sources: list[CatalogSourceOut]


def _names(session: Session) -> dict[str, str]:
    return {s.site_id: s.supplier_name for s in session.scalars(select(SupplierSite))}


def _impact_scope(session: Session, case_id: str) -> tuple[set[tuple[str, int]], set[str], set[str], set[str]]:
    """PO lines, production orders, sales orders and materials in the impact of the case (its latest snapshot)."""
    snapshot = case_tools.latest_snapshot(session, case_id)
    if snapshot is None:
        return set(), set(), set(), set()
    payload = snapshot.payload
    lines = {(row["po_id"], row["item_no"]) for row in payload["po_lines"]}
    orders = {row["order_id"] for row in payload["production_orders"]}
    sales = {row["so_id"] for row in payload["sales_orders"]}
    parts = {row["material_id"] for row in payload["po_lines"]} | {
        part for row in payload["production_orders"] for part in row.get("blocking_parts", ())}
    return lines, orders, sales, parts


def _affected_first(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: not row.affected)  # stable: the ERP order stays inside each group


def catalog(session: Session, erp: ErpData, alternates: dict[tuple[str, str], AlternateInfo], case_id: str, d0: date) -> CustomCatalogOut:
    suppliers = _names(session)
    in_lines, in_orders, in_sales, in_parts = _impact_scope(session, case_id)
    materials = {m.material_id: m for m in erp.materials}
    products = {p.product_id: p.description for p in erp.products}
    po_lines = [
        CatalogPoLineOut(
            po_id=l.po_id, item_no=l.item_no, material_id=l.material_id,
            material_name=materials[l.material_id].description if l.material_id in materials else l.material_id,
            supplier_id=l.supplier_id, supplier_name=suppliers.get(l.supplier_id, l.supplier_id), qty=l.qty,
            confirmed_date=l.confirmed_date.isoformat(), shipping_condition=l.shipping_condition, incoterms=l.incoterms,
            expedite_cost_per_unit=materials[l.material_id].expedite_cost_per_unit if l.material_id in materials else None,
            affected=(l.po_id, l.item_no) in in_lines)
        for l in sorted(erp.po_lines, key=lambda l: (l.po_id, l.item_no)) if l.status == "OPEN"
    ]
    orders = [
        CatalogProductionOrderOut(order_id=o.order_id, product_id=o.product_id, product_name=products.get(o.product_id, o.product_id),
                                  qty=o.qty, start_date=o.start_date.isoformat(), status=o.status,
                                  affected=o.order_id in in_orders)
        for o in sorted(erp.production_orders, key=lambda o: o.order_id) if o.status in OPEN_PRODUCTION_STATUSES
    ]
    first_items: dict[str, Any] = {}
    for s in sorted(erp.sales_orders, key=lambda s: (s.so_id, s.item_no)):
        first_items.setdefault(s.so_id, s)  # R-17 and the ERP call read the first item of the order
    sales = [
        CatalogSalesOrderOut(so_id=s.so_id, customer=s.customer, product_id=s.product_id,
                             product_name=products.get(s.product_id, s.product_id), qty=s.qty,
                             confirmed_delivery_date=s.confirmed_delivery_date.isoformat(), affected=s.so_id in in_sales)
        for s in first_items.values()
    ]
    sources = [
        CatalogSourceOut(
            material_id=a.part_id, material_name=materials[a.part_id].description if a.part_id in materials else a.part_id,
            supplier_id=a.site_id, supplier_name=suppliers.get(a.site_id, a.site_id), country=a.country, price=a.price,
            max_qty=a.max_qty, lead_time_days=a.lead_time_days, delivery_date=(d0 + timedelta(days=a.lead_time_days)).isoformat(),
            air_lead_time_days=a.air_lead_time_days, air_freight_per_unit=a.air_freight_per_unit,
            air_delivery_date=None if a.air_lead_time_days is None else (d0 + timedelta(days=a.air_lead_time_days)).isoformat(),
            affected=a.part_id in in_parts)
        for a in sorted(alternates.values(), key=lambda a: (a.part_id, a.site_id))
    ]
    return CustomCatalogOut(case_id=case_id, option=CUSTOM_OPTION, today=d0.isoformat(), max_actions=MAX_CUSTOM_ACTIONS,
                            po_lines=_affected_first(po_lines), production_orders=_affected_first(orders),
                            sales_orders=_affected_first(sales), alternate_sources=_affected_first(sources))


# --- payloads ----------------------------------------------------------------------------------------------------------------------

def _invalid(index: int, message: str, **details: Any) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", f"Action {index + 1}: {message}", {"index": index, **details})


def _required(index: int, spec: CustomActionIn, *fields: str) -> None:
    missing = [f for f in fields if getattr(spec, f) is None]
    if missing:
        raise _invalid(index, f"{', '.join(missing)} is required for {spec.type}.", fields=missing)


def build_payload(index: int, spec: CustomActionIn, erp: ErpData, alternates: dict[tuple[str, str], AlternateInfo],
                  d0: date) -> dict[str, Any]:
    """The full payload of contract G.3 from the object and the new value; the old values come from the ERP as it is now."""
    if spec.type in ("CHANGE_PO_DATE", "REDUCE_OR_CANCEL_PO", "EXPEDITE_SHIPMENT"):
        _required(index, spec, "po_id", "item_no")
        line = next((l for l in erp.po_lines if (l.po_id, l.item_no) == (spec.po_id, spec.item_no)), None)
        if line is None:
            raise _invalid(index, f"PO line {spec.po_id}/{spec.item_no} was not found.", field="po_id")
        if spec.type == "CHANGE_PO_DATE":
            _required(index, spec, "new_date")
            return {"po_id": line.po_id, "item_no": line.item_no, "old_date": line.confirmed_date.isoformat(), "new_date": spec.new_date}
        if spec.type == "REDUCE_OR_CANCEL_PO":
            _required(index, spec, "new_qty")
            return {"po_id": line.po_id, "item_no": line.item_no, "old_qty": line.qty, "new_qty": spec.new_qty}
        rate = next((m.expedite_cost_per_unit for m in erp.materials if m.material_id == line.material_id), None)
        if rate is None:
            raise _invalid(index, f"No expedite rate for {line.material_id}.", field="po_id")
        return {"po_id": line.po_id, "item_no": line.item_no, "old_shipping_condition": line.shipping_condition,
                "new_shipping_condition": "AIR", "old_incoterms": line.incoterms, "new_incoterms": "FCA",
                "freight_cost": line.qty * rate}
    if spec.type == "RESCHEDULE_PRODUCTION_ORDER":
        _required(index, spec, "order_id", "new_date")
        order = next((o for o in erp.production_orders if o.order_id == spec.order_id), None)
        if order is None:
            raise _invalid(index, f"Production order {spec.order_id} was not found.", field="order_id")
        return {"order_id": order.order_id, "old_date": order.start_date.isoformat(), "new_date": spec.new_date}
    if spec.type == "RESCHEDULE_SALES_ORDER":
        _required(index, spec, "so_id", "new_date")
        sales = next((s for s in sorted(erp.sales_orders, key=lambda s: s.item_no) if s.so_id == spec.so_id), None)
        if sales is None:
            raise _invalid(index, f"Sales order {spec.so_id} was not found.", field="so_id")
        return {"so_id": sales.so_id, "old_date": sales.confirmed_delivery_date.isoformat(), "new_date": spec.new_date}
    # CREATE_PR_PO
    _required(index, spec, "material_id", "supplier_id", "qty")
    source = alternates.get((spec.material_id, spec.supplier_id))
    if source is None:
        raise _invalid(index, f"No approved alternate source for {spec.material_id} at {spec.supplier_id}.", field="supplier_id")
    if spec.by_air:
        if source.air_lead_time_days is None:
            raise _invalid(index, f"The alternate source {spec.supplier_id} has no air option for {spec.material_id}.", field="by_air")
        ship, inc, freight, lead = "AIR", "FCA", source.air_freight_per_unit or 0, source.air_lead_time_days
    else:
        (ship, inc), freight, lead = _ship_default(source.country), 0, source.lead_time_days
    return {"material_id": source.part_id, "supplier_id": source.site_id, "qty": spec.qty, "price": source.price,
            "delivery_date": (d0 + timedelta(days=lead)).isoformat(), "shipping_condition": ship, "incoterms": inc,
            "freight_per_unit": freight}


def _object_key(action_type: str, payload: dict[str, Any]) -> str:
    """The ERP object an action writes: the three PO-line actions share the line."""
    if action_type in ("CHANGE_PO_DATE", "REDUCE_OR_CANCEL_PO", "EXPEDITE_SHIPMENT"):
        return f"PO:{payload['po_id']}/{payload['item_no']}"
    return f"{action_type}:{action_object(action_type, payload)}"


def _object_label(action_type: str, payload: dict[str, Any]) -> str:
    if action_type in ("CHANGE_PO_DATE", "REDUCE_OR_CANCEL_PO", "EXPEDITE_SHIPMENT"):
        return f"PO line {payload['po_id']}/{payload['item_no']}"
    if action_type == "RESCHEDULE_PRODUCTION_ORDER":
        return f"Production order {payload['order_id']}"
    if action_type == "RESCHEDULE_SALES_ORDER":
        return f"Sales order {payload['so_id']}"
    return f"The purchase of {payload['material_id']} from {payload['supplier_id']}"


def plan(specs: list[CustomActionIn], erp: ErpData, alternates: dict[tuple[str, str], AlternateInfo],
         d0: date) -> list[tuple[str, dict[str, Any]]]:
    """Every action with its full payload, each accepted by R-17 against the ERP as it is now; else 400 naming the action.

    An ERP object (a PO line, a purchase from one source, a production or sales order) may be named by one action only: every
    action is checked against the ERP before the first one runs, so a second change of the same object would be checked
    against values the first one has already changed (a cancelled line, an old date)."""
    planned: list[tuple[str, dict[str, Any]]] = []
    seen: dict[str, int] = {}
    for index, spec in enumerate(specs):
        payload = build_payload(index, spec, erp, alternates, d0)
        target = _object_key(spec.type, payload)
        if target in seen:
            raise _invalid(index, f"{_object_label(spec.type, payload)} is already changed by action {seen[target] + 1}; "
                                  "combine the change into one action.", field="object", duplicate_of=seen[target])
        seen[target] = index
        error = validate_action(spec.type, payload, erp, alternates, d0)
        if error is not None:
            raise _invalid(index, error, error=error)
        planned.append((spec.type, payload))
    return planned


# --- decision + execution ------------------------------------------------------------------------------------------------------

def _meeting_room_check(session: Session, built: CaseRecommendation, body: CustomDecisionIn) -> list[str]:
    """Tier 1: the meeting is a Meeting Room of this case and every required approver approved. No decision note is written
    (EP-30 is for the Executive Meeting Room only, R-22), so a later tier 2 still asks for one. Returns the approvers in order."""
    meeting = session.get(Meeting, body.meeting_id)
    if meeting is None or meeting.case_id != built.case.case_id:
        raise ApiError(400, "VALIDATION_FAILED", "meeting_id must be a meeting of this case.", {"field": "meeting_id"})
    approved = [role for role in PARTICIPANT_ORDER if role in set(body.approved_by)]
    missing = [role for role in built.escalation["required_approvers"] if role not in approved]
    if missing:
        raise ApiError(400, "VALIDATION_FAILED", "approved_by must include every required approver.", {"missing": missing})
    return approved


async def decide_and_execute(app: FastAPI, session: Session, built: CaseRecommendation, body: CustomDecisionIn, *,
                             username: str, now: datetime) -> dict[str, Any]:
    """Check everything first (decision fields, every action, the budget), then write the note and the proposed actions in one
    commit, then execute them. A failed action stays `FAILED` on the Approvals page for Retry or Reject.

    Tier 2 writes the decision note of the Executive Meeting Room (option `X`). Tier 1 has no note: the approvers and the notes of
    the Meeting Room go into the trace of the case. Tier 0 has no meeting, so it has no custom actions either."""
    tier = built.escalation["tier"]
    if tier not in (1, 2):
        raise ApiError(409, "CONFLICT", "Custom ERP actions are only for Meeting Room and Executive Meeting Room cases",
                       {"escalation_tier": tier})
    decision: dict[str, Any] | None = None
    if tier == 2:
        note = DecisionIn(meeting_id=body.meeting_id, option=None, budget_approved=body.budget_approved,
                          approved_by=body.approved_by, notes=body.notes)
        decision = meeting_service.record_decision(session, built, note, username=username, now=now, custom_option=CUSTOM_OPTION)
    else:
        approved = _meeting_room_check(session, built, body)

    erp = await load_erp_data(app)
    alternates = executor.alternates_of(session)
    planned = plan(body.actions, erp, alternates, app.state.d0_date)
    extra = premium(erp, [PlanAction(kind, payload) for kind, payload in planned])
    limit = built.escalation["limits"]["scm_approval_limit"]
    if extra > limit and not body.budget_approved:
        raise ApiError(400, "VALIDATION_FAILED",
                       f"budget_approved must be true: the extra cost of the custom actions ({format_idr(extra)}) is above the "
                       f"SCM Manager approval limit ({format_idr(limit)}).", {"field": "budget_approved", "premium": extra})

    case_id = built.case.case_id
    if decision is not None:
        batch, reason = decision["decision_id"], f"Custom ERP actions of meeting decision {decision['decision_id']}"
    else:
        batch = next_id(session, Action.action_id, executor.ACTION_PREFIX)  # the id of the first action: unique per submission
        reason = f"Custom ERP actions of Meeting Room {body.meeting_id}"
        case_tools.add_trace(
            session, run_id=None, case_id=case_id, task=meeting_service.DECISION_TASK, step=CUSTOM_STEP, tool=None,
            input_summary=f"meeting {body.meeting_id} by {username}", output_summary=f"{len(planned)} action(s)",
            decision=f"Custom ERP actions (Option {CUSTOM_OPTION}) approved by {', '.join(approved) or username}"
                     + ("; budget approved" if body.budget_approved else "") + (f"; notes: {body.notes}" if body.notes else ""),
            duration_ms=None, status="OK", now=now)
    keys = [f"{case_id}:{CUSTOM_OPTION}:{seq}:{batch}" for seq in range(1, len(planned) + 1)]
    actions = executor.propose_actions(session, built.case, CUSTOM_OPTION, planned, keys, username=username, now=now, reason=reason)
    session.commit()
    views = await executor.execute_in_order(app, session, actions, username=username, now=now)
    session.refresh(built.case)
    return {"decision": decision, "case_status": built.case.status, "premium": extra, "actions": views}
