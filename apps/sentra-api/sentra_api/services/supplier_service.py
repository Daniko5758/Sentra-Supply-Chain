"""R-12 v2 / R-13 v2 on the database: send the supplier requests (TL-11), deliver the scripted answers when they are due.

Three kinds of request (`IMPACT`, `BACKUP_ACTIVATION`, `PULL_IN`), one row in `supplier_request` per case, site and kind. Same path
for every case (CLAUDE.md rule 2). The answer script follows the origin of the case's signal (`case.mode`), never the application
mode (DEC-024). The wording and the rules are the pure functions of `supplier_replies`.
"""

import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.case_state import CLOSED_STATUSES, CaseEvent, CaseStatus
from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.impact_engine.engine import is_open
from sentra_api.models import Case, CaseSite, Signal, SupplierMessage, SupplierRequest, SupplierSite, TierLink
from sentra_api.services import case_tools, recommendation_service
from sentra_api.services.case_analysis import first_signal
from sentra_api.services.case_service import apply_event
from sentra_api.services.ids import REQUEST_PREFIX, next_id
from sentra_api.services.runs import request_resume
from sentra_api.supplier_replies import (
    REQUEST_TYPES, SCRIPT_KEY, PoLineFact, ReplyScriptError, RequestFacts, SupplierReply, apply_option_reply, apply_reply,
    pick_script_v2, request_body, resolve_option_reply, resolve_reply, scenario_of,
)

log = logging.getLogger(__name__)

NO_SOURCE = "not stated"


def _source(signal: Signal) -> str:
    return "; ".join(signal.source_urls) or signal.source_label or NO_SOURCE


def _request_facts(
    session: Session, erp: ErpData, case: Case, signal: Signal, site: SupplierSite, request_type: str,
    pull_in: tuple[tuple[str, int], ...] = (), pull_in_date: date | None = None,
) -> RequestFacts:
    po_lines = tuple(
        PoLineFact(line.po_id, line.item_no, line.material_id, line.qty, line.confirmed_date)
        for line in sorted(erp.po_lines, key=lambda l: (l.po_id, l.item_no))
        if line.supplier_id == site.site_id and is_open(line)
    ) if site.tier == "1" else ()
    components = tuple(sorted({link.component for link in session.scalars(
        select(TierLink).where(TierLink.tier2_site == site.site_id))})) if site.tier == "2" else ()
    return RequestFacts(
        case_title=case.title, event_summary=signal.summary, source=_source(signal), supplier_name=site.supplier_name,
        site_id=site.site_id, tier=site.tier, po_lines=po_lines, components=components, request_type=request_type,
        backup_site=site.backup_site, backup_ramp_days=site.backup_ramp_days,
        pull_in_lines=tuple(f"{po_id}/{item_no}" for po_id, item_no in pull_in), pull_in_date=pull_in_date,
    )


def _pull_in_lines(
    erp: ErpData, built: recommendation_service.CaseRecommendation, site_id: str
) -> tuple[tuple[tuple[str, int], ...], date | None]:
    """The PO lines of the site that option E moves, and the date it moves them to (the day before the effective date)."""
    supplier = {(l.po_id, l.item_no): l.supplier_id for l in erp.po_lines}
    option = next((o for o in built.recommendation.options if o.option == "E"), None)
    lines: list[tuple[str, int]] = []
    day: date | None = None
    for action in () if option is None else option.actions:
        key = (action.payload["po_id"], action.payload["item_no"])
        if supplier.get(key) == site_id:
            lines.append(key)
            day = date.fromisoformat(action.payload["new_date"])
    return tuple(lines), day


# --- TL-11 -------------------------------------------------------------------------------------------------------

def request_confirmation(
    session: Session, erp: ErpData, case_id: str, site_ids: list[str], *, request_type: str = "IMPACT",
    reply_delay_s: int, now: datetime, d0: date,
) -> dict[str, Any]:
    """R-12 v2: one outgoing message and one `supplier_request` per site, case and kind, never a reminder.

    A site goes to `skipped` instead of an error: `NOT_CANDIDATE` (not a site of the case), `ALREADY_REQUESTED` (this kind was
    asked already, also when it has been answered), `NOT_NEEDED` (an IMPACT question for a country event; a BACKUP_ACTIVATION or
    PULL_IN question the recommended option does not wait for). The answers are not sent here: `deliver_due_replies` sends them
    `reply_delay_s` seconds after the request.
    """
    if request_type not in REQUEST_TYPES:
        raise ApiError(400, "VALIDATION_FAILED", f"request_type must be one of {list(REQUEST_TYPES)}.", {"field": "request_type"})
    case = case_tools.load_case(session, case_id)
    if case.status in CLOSED_STATUSES:
        raise ApiError(409, "CONFLICT", "The case is closed; no confirmation can be requested.", {"case_id": case_id})
    case_sites = {cs.site_id: cs for cs in session.scalars(select(CaseSite).where(CaseSite.case_id == case_id))}
    asked = set(session.scalars(select(SupplierRequest.site_id).where(
        SupplierRequest.case_id == case_id, SupplierRequest.request_type == request_type)))
    built = None
    needed: set[str] = set()
    if request_type != "IMPACT":
        built = recommendation_service.build_recommendation(session, erp, case_id, d0)
        needed = {site for site, kind in built.recommendation.confirmations_needed if kind == request_type}

    signal = first_signal(session, case_id)
    requested: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for site_id in dict.fromkeys(site_ids):
        case_site = case_sites.get(site_id)
        if case_site is None:
            skipped.append({"site_id": site_id, "reason": "NOT_CANDIDATE"})
            continue
        if site_id in asked or (request_type == "IMPACT" and case_site.verification_status != "NOT_NEEDED"):
            skipped.append({"site_id": site_id, "reason": "ALREADY_REQUESTED"})
            continue
        if (request_type == "IMPACT" and case.scope != "LOCATION") or (request_type != "IMPACT" and site_id not in needed):
            # R-14: a country event applies to every site of the country, so there is nothing to ask the supplier about impact.
            skipped.append({"site_id": site_id, "reason": "NOT_NEEDED"})
            continue
        pull_in, pull_in_date = _pull_in_lines(erp, built, site_id) if request_type == "PULL_IN" else ((), None)
        message = SupplierMessage(
            id=next_id(session, SupplierMessage.id, "MSG"), case_id=case_id, site_id=site_id, direction="OUT",
            body=request_body(_request_facts(session, erp, case, signal, session.get(SupplierSite, site_id), request_type,
                                             pull_in, pull_in_date)),
            structured=None, created_at=now, simulated=True,
            request_id=next_id(session, SupplierRequest.request_id, REQUEST_PREFIX), request_type=request_type,
        )
        session.add(message)
        session.add(SupplierRequest(
            request_id=message.request_id, case_id=case_id, site_id=site_id, request_type=request_type, status="REQUESTED",
            po_lines=[{"po_id": po_id, "item_no": item_no} for po_id, item_no in pull_in] if request_type == "PULL_IN" else None,
            message_out_id=message.id, requested_at=now, due_at=now + timedelta(seconds=reply_delay_s),
        ))
        if request_type == "IMPACT":
            case_site.verification_status = "REQUESTED"
        session.flush()
        requested.append({"site_id": site_id, "request_id": message.request_id, "message_id": message.id})
    if requested and case.status == CaseStatus.ANALYZING.value:
        apply_event(session, case_id, CaseEvent.CONFIRMATION_REQUESTED, now=now)
    return {"requested": requested, "skipped": skipped, "reply_expected_after_s": reply_delay_s}


# --- the scripted answers (R-12 v2 step 3, R-13 v2) ------------------------------------------------------------------------

def _script_for(seed: Mapping[str, Any], signal: Signal, site_id: str, request_type: str) -> Mapping[str, Any] | None:
    sentra = seed["sentra"]
    scenario = None
    if signal.simulated:  # the origin of the case's signal (engine = DEMO), not the application mode (DEC-024)
        scenario = scenario_of(sentra["demo_events"], {
            "risk_type": signal.risk_type, "scope": signal.scope, "country": signal.country,
            "kabupaten": signal.kabupaten, "summary": signal.summary,
        })
    return pick_script_v2(sentra["scripted_replies"], scenario, site_id, request_type, signal.simulated)


def _impact_reply(seed: Mapping[str, Any], script: Mapping[str, Any], site: SupplierSite) -> SupplierReply:
    try:
        return resolve_reply(script, site.ttr_days)
    except ReplyScriptError as error:
        log.warning("scripted answer for %s unusable (%s); using default_live", site.site_id, error)
        return resolve_reply(seed["sentra"]["scripted_replies"]["default_live"], site.ttr_days)


def _option_reply(seed: Mapping[str, Any], request_type: str, script: Mapping[str, Any], site: SupplierSite):
    try:
        return resolve_option_reply(request_type, script, site.backup_ramp_days)
    except ReplyScriptError as error:
        log.warning("scripted %s answer for %s unusable (%s); using default_live", request_type, site.site_id, error)
        default = seed["sentra"]["scripted_replies"]["default_live"].get(SCRIPT_KEY[request_type])
        return resolve_option_reply(request_type, default, site.backup_ramp_days)


def _answer(
    session: Session, seed: Mapping[str, Any], request: SupplierRequest, case_site: CaseSite, site: SupplierSite,
    script: Mapping[str, Any], now: datetime,
) -> None:
    """Store the incoming message, close the request and apply R-13 v2."""
    if request.request_type == "IMPACT":
        reply = _impact_reply(seed, script, site)
        applied = apply_reply(reply)
        body, structured, answer = reply.body(), reply.structured(), reply.affected
        request.delay_days = reply.delay_days
        case_site.delay_override_days, case_site.excluded = applied.delay_override_days, applied.excluded
        case_site.verification_status = applied.verification_status
    else:
        option_reply = _option_reply(seed, request.request_type, script, site)
        applied_option = apply_option_reply(option_reply)
        body, structured, answer = option_reply.body(), option_reply.structured(), option_reply.answer
        request.ramp_days = option_reply.ramp_days
        if applied_option.backup_ramp_confirmed is not None:
            case_site.backup_ramp_confirmed = applied_option.backup_ramp_confirmed
        if applied_option.backup_declined:
            case_site.backup_declined = True
    incoming = SupplierMessage(
        id=next_id(session, SupplierMessage.id, "MSG"), case_id=request.case_id, site_id=request.site_id, direction="IN",
        body=body, structured=structured, created_at=now, simulated=True, request_id=request.request_id,
        request_type=request.request_type,
    )
    session.add(incoming)
    request.status, request.answer, request.answered_at, request.message_in_id = "ANSWERED", answer, now, incoming.id


def deliver_due_replies(session: Session, seed: Mapping[str, Any], *, now: datetime) -> list[tuple[str, str]]:
    """Answer every REQUESTED supplier request that is due (R-12 v2 step 3), or mark it NO_RESPONSE when the script of the site is
    silent; apply R-13 v2; and queue ONE `resume_case` per case (answers that arrive together are processed together, PRD 6.4).

    Returns the `(case_id, site_id)` pairs decided. A request is decided once: it stops being REQUESTED. The caller commits.
    """
    pending = [r for r in session.scalars(
        select(SupplierRequest).where(SupplierRequest.status == "REQUESTED").order_by(SupplierRequest.request_id)
    ) if r.due_at <= now]
    delivered: list[tuple[str, str]] = []
    for request in pending:
        case_site = session.get(CaseSite, (request.case_id, request.site_id))
        site = session.get(SupplierSite, request.site_id)
        script = _script_for(seed, first_signal(session, request.case_id), request.site_id, request.request_type)
        if script is None:  # no_reply: nothing arrives, the analysis goes on with the rule values
            request.status = "NO_RESPONSE"
            if request.request_type == "IMPACT":
                case_site.verification_status = "NO_RESPONSE"
        else:
            _answer(session, seed, request, case_site, site, script, now)
        session.flush()
        delivered.append((request.case_id, request.site_id))
    for case_id in dict.fromkeys(case_id for case_id, _ in delivered):
        if session.get(Case, case_id).status not in CLOSED_STATUSES:
            request_resume(session, case_id, trigger="auto", now=now)
    return delivered
