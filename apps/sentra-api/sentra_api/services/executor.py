"""F-12 (R-17, R-17 v2): submit an option, approve / reject / retry its actions, execute them in the mock S/4, read the ERP log.

The options, their actions and the 409 checks of EP-21 come from the recommendation the server computes (G-06). An action goes to the
ERP only after the SCM Manager approved it and `execution_rules.validate_action` accepted it against the ERP as it is at that moment;
the ERP call carries the idempotency key of the action, so a repeated call changes nothing (UT-12). When every action of the case is
settled the case becomes `EXECUTED` and a `resume_case` is queued: the impact, score, options and escalation are computed again.

The action table has no `seq`, `object` or creation time (DATA_MODEL 3.2). `seq` is the third part of the idempotency key, `object` is
derived from type and payload (`execution_rules`) and the creation time is that of the `submit_option` trace step of the case.
"""

from datetime import datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentra_api.case_state import CaseEvent, CaseStatus
from sentra_api.erp import ErpClient, ErpData, ErpError, ErpMessage, sentra_message
from sentra_api.errors import ApiError
from sentra_api.execution_rules import action_document, action_object, action_seq, validate_action
from sentra_api.flags import flag_enabled
from sentra_api.models import Action, AgentTrace, AlternateSource, Case, ImpactSnapshot, SupplierSite
from sentra_api.recommendation_rules import AlternateInfo
from sentra_api.services import case_tools
from sentra_api.services.case_service import apply_event, settle_actions
from sentra_api.services.erp_data import erp_client, load_erp_data
from sentra_api.services.ids import next_id
from sentra_api.services.recommendation_service import CaseRecommendation, build_recommendation
from sentra_api.services.runs import request_resume
from sentra_api.timeutil import iso, iso_or_none

ACTION_PREFIX = "ACT"
SUBMIT_STEP, SUPERSEDE_STEP, DECISION_STEP = "submit_option", "supersede_actions", "action_decision"
OPEN_FOR_DECISION = ("PROPOSED", "FAILED")  # a decision (approve, reject, retry) can still be taken
WAITING = ("PROPOSED", "APPROVED", "FAILED")  # EP-22 lists these; EXECUTED and REJECTED are history
IN_RELEASE = "03"  # `PurchasingProcessingStatus` of a new PO that waits for release (E-14, B-53)


def decision_required(built: CaseRecommendation, *, enabled: bool = True) -> tuple[bool, str | None]:
    """Whether a meeting decision must exist before an option of the case can be submitted, and the option it chose (R-22).

    `(True, None)`: tier 2, F-07 on and no `meeting_decision` yet -> EP-21 answers `Meeting decision required before submitting`.
    `(False, "A")`: a decision exists and chose Option A -> any other option is refused. `(False, None)`: nothing to check
    (also with F-07 off: `enabled` is False and the decision notes are not asked for). The latest note of the case counts, not the
    one of the version.
    """
    if not enabled:
        return False, None
    escalation = built.escalation
    if escalation["decision_required"]:
        return True, None
    decision = escalation["decision"]
    return False, None if decision is None else decision["option"]


def _conflict(message: str, **details: Any) -> ApiError:
    return ApiError(409, "CONFLICT", message, details)


# --- views ---------------------------------------------------------------------------------------------------------------

def erp_result_view(erp_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """`erp_result` as EP-22 v1.12 shows it: `document` and `messages`. An action stored before v1.12 has no `messages`: `[]`."""
    if erp_result is None:
        return None
    return {"document": erp_result.get("document"), "messages": list(erp_result.get("messages") or [])}


def action_view(action: Action, created_at: datetime | None = None) -> dict[str, Any]:
    """An action as EP-21 / EP-22 show it: the shared schema G.3 plus the decision and the ERP answer."""
    return {
        "action_id": action.action_id, "case_id": action.case_id, "option": action.option,
        "seq": action_seq(action.idempotency_key), "type": action.type, "object": action_object(action.type, action.payload),
        "payload": action.payload, "status": action.status, "idempotency_key": action.idempotency_key,
        "proposed_by": action.proposed_by, "created_at": iso_or_none(created_at), "decided_by": action.decided_by,
        "decided_at": iso_or_none(action.decided_at), "executed_at": iso_or_none(action.executed_at),
        "erp_result": erp_result_view(action.erp_result), "error": action.error,
    }


def _proposed_times(session: Session, case_ids: set[str]) -> dict[str, datetime]:
    """action_id -> time of the latest `submit_option` step that proposed it (its output lists the ids between commas)."""
    times: dict[str, datetime] = {}
    steps = session.scalars(
        select(AgentTrace).where(AgentTrace.case_id.in_(case_ids), AgentTrace.step == SUBMIT_STEP)
        .order_by(AgentTrace.created_at, AgentTrace.id)
    )
    for step in steps:
        for action_id in (step.output_summary or "").strip(",").split(","):
            times[action_id] = step.created_at
    return times


def _views(session: Session, actions: list[Action]) -> list[dict[str, Any]]:
    times = _proposed_times(session, {a.case_id for a in actions}) if actions else {}
    return [action_view(a, times.get(a.action_id)) for a in actions]


def _case_actions(session: Session, case_id: str, option: str | None = None) -> list[Action]:
    query = select(Action).where(Action.case_id == case_id)
    if option is not None:
        query = query.where(Action.option == option)
    return sorted(session.scalars(query), key=lambda a: (a.option, action_seq(a.idempotency_key), a.action_id))


def list_approvals(session: Session) -> list[dict[str, Any]]:
    """EP-22: the actions that wait for a decision or for a retry, by case, option and `seq`."""
    actions = sorted(session.scalars(select(Action).where(Action.status.in_(WAITING))),
                     key=lambda a: (a.case_id, a.option, action_seq(a.idempotency_key), a.action_id))
    titles = {c.case_id: c.title for c in session.scalars(select(Case).where(Case.case_id.in_({a.case_id for a in actions})))}
    return [{**{k: v for k, v in view.items() if k not in ("idempotency_key", "decided_by", "decided_at", "executed_at")},
             "case_title": titles[view["case_id"]]} for view in _views(session, actions)]


async def erp_log(app: FastAPI) -> list[dict[str, Any]]:
    """EP-23: the change documents of the mock S/4, oldest first, times in +07:00."""
    async with erp_client(app) as client:
        documents = await client.change_documents()
    return [{**d, "changed_at": iso(datetime.fromisoformat(d["changed_at"]))} for d in documents]


# --- EP-21 ---------------------------------------------------------------------------------------------------------------

async def submit_option(app: FastAPI, session: Session, case_id: str, option: str, *, username: str, now: datetime) -> dict[str, Any]:
    """Turn the actions of an option into `PROPOSED` actions. 409 with the exact messages of contract EP-21 (v1.11), in this order."""
    erp = await load_erp_data(app)
    settings = app.state.settings
    built = build_recommendation(session, erp, case_id, app.state.d0_date, settings=settings)
    chosen = next((o for o in built.recommendation.options if o.option == option), None)
    if chosen is None or not chosen.available:
        raise _conflict(f"Option {option} is not available")
    if chosen.pending_confirmation:
        raise _conflict(f"Option {option} is waiting for supplier confirmation")
    if not chosen.actions:
        raise _conflict(f"Option {option} has no ERP actions")
    required, decided = decision_required(built, enabled=flag_enabled(settings, "FEATURE_MEETING_ROOM"))
    if required:
        raise _conflict("Meeting decision required before submitting")
    if decided is not None and decided != option:
        raise _conflict(f"Submitted option differs from the meeting decision (Option {decided})")

    keys = [f"{case_id}:{option}:{a.seq}:v{built.version}" for a in chosen.actions]
    proposed = propose_actions(session, built.case, option, [(a.type, a.payload) for a in chosen.actions], keys,
                               username=username, now=now, reason=built.recommendation.rule_reason)
    return {"case_id": case_id, "option": option, "status": built.case.status, "actions": _views(session, proposed)}


def propose_actions(session: Session, case: Case, option: str, planned: list[tuple[str, dict[str, Any]]], keys: list[str], *,
                    username: str, now: datetime, reason: str | None) -> list[Action]:
    """Write the planned actions of an option as `PROPOSED` (EP-21, and the custom actions of a meeting decision). The case moves to
    `AWAITING_APPROVAL`; the actions that still wait for a decision are replaced (`REJECTED`)."""
    case_id = case.case_id
    if case.status != CaseStatus.AWAITING_APPROVAL:  # a new option while one waits replaces it (PRD FR-17)
        apply_event(session, case_id, CaseEvent.OPTION_SUBMITTED, now=now)  # 409 unless READY_FOR_DECISION or EXECUTED (DEC-043)

    existing = _case_actions(session, case_id)
    by_key = {a.idempotency_key: a for a in existing}
    for key in keys:
        if key in by_key and by_key[key].status in ("APPROVED", "EXECUTED"):
            raise _conflict(f"Option {option} already has executed actions", action_id=by_key[key].action_id)
    replaced = [a for a in existing if a.status in OPEN_FOR_DECISION]
    for stale in replaced:
        stale.status, stale.decided_by, stale.decided_at, stale.error = "REJECTED", username, now, "Replaced by a newer submission"
    if replaced:
        case_tools.add_trace(
            session, run_id=None, case_id=case_id, task=None, step=SUPERSEDE_STEP, tool=None, input_summary=f"by {username}",
            output_summary=", ".join(a.action_id for a in replaced), decision=f"Option {option} was submitted",
            duration_ms=None, status="OK", now=now)

    proposed: list[Action] = []
    for key, (action_type, payload) in zip(keys, planned):
        action = by_key.get(key)  # a rejected action of the same option and version is proposed again
        if action is None:
            action = Action(action_id=next_id(session, Action.action_id, ACTION_PREFIX), case_id=case_id, option=option,
                            type=action_type, payload=dict(payload), idempotency_key=key, proposed_by=username)
            session.add(action)
            session.flush()
        action.type, action.payload, action.status, action.proposed_by = action_type, dict(payload), "PROPOSED", username
        action.decided_by = action.decided_at = action.executed_at = action.erp_result = action.error = None
        proposed.append(action)
    case_tools.add_trace(
        session, run_id=None, case_id=case_id, task=None, step=SUBMIT_STEP, tool=None, input_summary=f"option {option} by {username}",
        output_summary="," + ",".join(a.action_id for a in proposed) + ",", decision=reason,
        duration_ms=None, status="OK", now=now)
    case.updated_at = now
    session.flush()
    return proposed


# --- EP-22 ---------------------------------------------------------------------------------------------------------------

def _load_action(session: Session, action_id: str) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise ApiError(404, "NOT_FOUND", f"Action {action_id} was not found.", {"action_id": action_id})
    return action


def _require_awaiting_approval(session: Session, action: Action) -> Case:
    case = session.get(Case, action.case_id)
    if case.status != CaseStatus.AWAITING_APPROVAL:
        raise _conflict(f"Case {case.case_id} is not waiting for approval (status {case.status})", status=case.status)
    return case


def alternates_of(session: Session) -> dict[tuple[str, str], AlternateInfo]:
    countries = {s.site_id: s.country for s in session.scalars(select(SupplierSite))}
    return {(a.part_id, a.site_id): AlternateInfo(a.part_id, a.site_id, a.price, a.lead_time_days, a.max_qty, a.air_lead_time_days,
                                                 a.air_freight_per_unit, countries.get(a.site_id))
            for a in session.scalars(select(AlternateSource))}


async def _send(client: ErpClient, action: Action, erp: ErpData, by: str,
                messages: list[ErpMessage] | None = None) -> tuple[str, list[ErpMessage]]:
    """The ERP calls of one action (E-11..E-16, E-18); returns the `erp_result.document` text and the messages of every call, in order.

    The messages go into `messages` as each call answers, so when a later call fails (the PO after the PR) the caller still has the
    messages of the calls that went through."""
    sent = messages if messages is not None else []
    p, key = action.payload, action.idempotency_key
    if action.type == "CREATE_PR_PO":
        pr, got = await client.create_requisition(p["material_id"], p["qty"], p["supplier_id"], p["delivery_date"], p["price"],
                                                 key=f"{key}:PR", by=by)
        sent.extend(got)
        po, status, got = await client.create_purchase_order(
            pr, p["supplier_id"], p["material_id"], p["qty"], p["price"], p["delivery_date"], p["incoterms"],
            p["shipping_condition"], p["freight_per_unit"], key=f"{key}:PO", by=by)
        sent.extend(got)
        if status != IN_RELEASE:  # below the approval threshold of the ERP: the PO is active at once
            return f"PR {pr} / PO {po}", sent
        # B-53: the PO waits for release; the Approve of SENTRA releases it. A retry replays PR and PO (the stored answer of the PO
        # still says `03`) and calls the release again with the same key, replayed when it went through before.
        sent.extend(await client.release_purchase_order(po, key=f"{key}:REL", by=by))
        return f"PR {pr} / PO {po} (released)", sent
    document = action_document(action.type, p, erp)  # the old values, read before the change
    if action.type == "CHANGE_PO_DATE":
        got = await client.change_schedule_line(p["po_id"], p["item_no"], p["new_date"], key=key, by=by)
    elif action.type == "REDUCE_OR_CANCEL_PO":
        if p["new_qty"] == 0:  # cancel: the deletion indicator, never a quantity of 0 (v1.12, B-52)
            got = await client.delete_item(p["po_id"], p["item_no"], key=key, by=by)
        else:
            got = await client.set_order_quantity(p["po_id"], p["item_no"], p["new_qty"], key=key, by=by)
    elif action.type == "EXPEDITE_SHIPMENT":
        got = await client.set_shipping(p["po_id"], p["item_no"], p["new_shipping_condition"], p["new_incoterms"], key=key, by=by)
    elif action.type == "RESCHEDULE_PRODUCTION_ORDER":
        got = await client.set_production_start(p["order_id"], p["new_date"], key=key, by=by)
    else:  # RESCHEDULE_SALES_ORDER
        got = await client.set_sales_delivery(p["so_id"], p["new_date"], key=key, by=by)
    sent.extend(got)
    return document, sent


async def _execute(app: FastAPI, session: Session, action: Action, *, username: str, now: datetime) -> None:
    """An `APPROVED` action (already committed) is validated and sent: it ends `EXECUTED` or `FAILED` (ERR-03)."""
    error: str | None = None
    document: str | None = None
    messages: list[ErpMessage] = []
    try:
        async with erp_client(app) as client:
            erp = await client.load()
            error = validate_action(action.type, action.payload, erp, alternates_of(session), app.state.d0_date)
            if error is None:
                document, _ = await _send(client, action, erp, username, messages)
    except ErpError as failure:
        error = failure.message
        messages.extend(failure.messages)  # after the messages of the calls that went through (the PR before a failed PO)
    if error is None:
        result = {"document": document, "messages": [m.as_dict() for m in messages]}
        action.status, action.executed_at, action.erp_result, action.error = "EXECUTED", now, result, None
    else:
        if not messages:  # refused by R-17 before the ERP was called
            messages.append(sentra_message(error))
        result = {"document": None, "messages": [m.as_dict() for m in messages]}
        action.status, action.executed_at, action.erp_result, action.error = "FAILED", None, result, error
    case_tools.add_trace(
        session, run_id=None, case_id=action.case_id, task=None, step="execute_action", tool=None,
        input_summary=f"{action.action_id} {action.type} {action_object(action.type, action.payload)}",
        output_summary=document or error, decision=None, duration_ms=None,
        status="OK" if error is None else "ERROR", now=now)


def _settle(app: FastAPI, session: Session, case_id: str, *, now: datetime) -> bool:
    """Case status after an action changed. True when the case just became EXECUTED and a `resume_case` was queued
    (the caller wakes the queue after the commit)."""
    case = session.get(Case, case_id)
    before = case.status
    actions = _case_actions(session, case_id)
    after = settle_actions(session, case_id, [a.status for a in actions], now=now)
    if before == CaseStatus.AWAITING_APPROVAL and after is CaseStatus.EXECUTED and _executed_since_last_recompute(session, case_id, actions):
        request_resume(session, case_id, trigger="manual", now=now)  # the agent reads case.status = EXECUTED: trigger erp_execution
        return True
    return False


def _executed_since_last_recompute(session: Session, case_id: str, actions: list[Action]) -> bool:
    """An action reached the ERP after the latest `erp_execution` snapshot. False when every action of a follow-up option
    (DEC-043) was rejected: the case goes back to EXECUTED and nothing needs recomputing."""
    last = session.scalar(select(func.max(ImpactSnapshot.created_at)).where(
        ImpactSnapshot.case_id == case_id, ImpactSnapshot.trigger == "erp_execution"))
    return any(a.status == "EXECUTED" and a.executed_at is not None and (last is None or a.executed_at > last) for a in actions)


def _finish(app: FastAPI, session: Session, case_id: str, *, now: datetime) -> None:
    queued = _settle(app, session, case_id, now=now)
    session.commit()
    if queued:
        app.state.run_queue.wake()


async def _approve_and_execute(app: FastAPI, session: Session, action: Action, *, username: str, now: datetime) -> None:
    action.status, action.decided_by, action.decided_at, action.error = "APPROVED", username, now, None
    session.commit()  # APPROVED is visible while the ERP is called: a second click is refused instead of sent twice
    await _execute(app, session, action, username=username, now=now)
    _finish(app, session, action.case_id, now=now)


async def approve_action(app: FastAPI, session: Session, action_id: str, *, username: str, now: datetime) -> dict[str, Any]:
    """`PROPOSED` -> `APPROVED` -> `EXECUTED` / `FAILED`. An executed action answers with its stored result and touches nothing (UT-12)."""
    action = _load_action(session, action_id)
    if action.status == "EXECUTED":
        return _views(session, [action])[0]
    if action.status != "PROPOSED":
        raise _conflict(f"Action {action_id} is {action.status}, only a PROPOSED action can be approved", status=action.status)
    _require_awaiting_approval(session, action)
    await _approve_and_execute(app, session, action, username=username, now=now)
    return _views(session, [action])[0]


async def retry_action(app: FastAPI, session: Session, action_id: str, *, username: str, now: datetime) -> dict[str, Any]:
    """`FAILED` -> `APPROVED` -> `EXECUTED` / `FAILED` again (same idempotency key)."""
    action = _load_action(session, action_id)
    if action.status != "FAILED":
        raise _conflict(f"Action {action_id} is {action.status}, only a FAILED action can be retried", status=action.status)
    _require_awaiting_approval(session, action)
    await _approve_and_execute(app, session, action, username=username, now=now)
    return _views(session, [action])[0]


def reject_action(app: FastAPI, session: Session, action_id: str, comment: str | None, *, username: str, now: datetime) -> dict[str, Any]:
    """`PROPOSED` (or `FAILED`: Cancel remaining actions) -> `REJECTED`; the comment is kept in the trace of the case."""
    if not (comment and comment.strip()):
        raise ApiError(400, "VALIDATION_FAILED", "A comment is required to reject an action.", {"field": "comment"})
    action = _load_action(session, action_id)
    if action.status not in OPEN_FOR_DECISION:
        raise _conflict(f"Action {action_id} is {action.status}, only a PROPOSED or FAILED action can be rejected", status=action.status)
    _require_awaiting_approval(session, action)
    action.status, action.decided_by, action.decided_at = "REJECTED", username, now
    case_tools.add_trace(
        session, run_id=None, case_id=action.case_id, task=None, step=DECISION_STEP, tool=None,
        input_summary=f"reject {action.action_id} by {username}", output_summary="REJECTED", decision=comment.strip(),
        duration_ms=None, status="OK", now=now)
    _finish(app, session, action.case_id, now=now)
    return _views(session, [action])[0]


async def approve_all(app: FastAPI, session: Session, case_id: str, option: str, *, username: str, now: datetime) -> list[dict[str, Any]]:
    """Approve and execute the `PROPOSED` actions of an option in `seq` order; stops at the first one that fails (the actions after
    it stay `PROPOSED` for the next try). Answers with those actions in their new state."""
    case_tools.load_case(session, case_id)
    waiting = [a for a in _case_actions(session, case_id, option) if a.status == "PROPOSED"]
    if not waiting:
        raise _conflict(f"Option {option} has no actions waiting for approval", case_id=case_id, option=option)
    _require_awaiting_approval(session, waiting[0])
    return await execute_in_order(app, session, waiting, username=username, now=now)


async def execute_in_order(app: FastAPI, session: Session, waiting: list[Action], *, username: str, now: datetime) -> list[dict[str, Any]]:
    """Approve and execute `PROPOSED` actions one by one; stop at the first one that fails. Answers with the actions in their new state."""
    for action in waiting:
        await _approve_and_execute(app, session, action, username=username, now=now)
        if action.status == "FAILED":
            break
    return _views(session, waiting)

