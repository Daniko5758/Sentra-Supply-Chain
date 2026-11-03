"""TL-23 / EP-15 recommendation and TL-16 `save_recommendation` on the database.

The rules are the pure functions of `recommendation_rules` (R-14 v2, R-15 v2). The options, their actions, the choice and the
`rule_reason` are recomputed here from the ERP and the case on every call (G-06); the only thing an agent hands over is the
text that explains them, and it is stored after the number validator accepted it (G-04).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.config import Settings
from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.escalation import Limits, escalate
from sentra_api.models import (
    AlternateSource, Case, ImpactSnapshot, PriorityRow, Recommendation as RecommendationRow, SupplierRequest, TierLink,
)
from sentra_api.number_validator import check_numbers
from sentra_api.recommendation_rules import (
    AlternateInfo, Recommendation, RequestState, SiteFact, TierLinkFact, recommend, template_rationale,
)
from sentra_api.services import case_tools
from sentra_api.services.case_detail import CaseDetail, build_case_detail
from sentra_api.services.meeting_records import decision_view, latest_decision, latest_meeting, meeting_view
from sentra_api.services.case_analysis import case_impact, load_case_inputs
from sentra_api.services.validation_attempts import ValidationAttempts


@dataclass(frozen=True)
class CaseRecommendation:
    case: Case
    snapshot: ImpactSnapshot
    recommendation: Recommendation
    escalation: dict[str, Any]  # G.7 (R-22) with the latest meeting and decision note

    @property
    def version(self) -> int:
        return self.snapshot.version

    def tool_view(self) -> dict[str, Any]:
        """TL-23: EP-15 without the rationales and `execution_enabled`."""
        return {"case_id": self.case.case_id, "version": self.version, **self.recommendation.to_dict(),
                "escalation_tier": self.escalation["tier"], "executive_decision_required": self.escalation["decision_required"],
                "decision": self.escalation["decision"]}


def limits_of(settings: Settings) -> Limits:
    """The R-22 limits from the configuration."""
    return Limits(settings.scm_approval_limit_idr, settings.tariff_escalation_duty_idr, settings.close_call_band_pp)


def build_recommendation(
    session: Session, erp: ErpData, case_id: str, d0: date, *, settings: Settings | None = None
) -> CaseRecommendation:
    """R-14 v2 / R-15 v2 and the escalation R-22 of the case now: `before` and every `after` come from the current ERP data and
    case state; the version is that of the latest impact snapshot. 404 unknown case, 409 while the case has no snapshot yet.
    `settings` carries the R-22 limits and the F-07 flag (default: the defaults of the configuration, flag off)."""
    settings = settings if settings is not None else Settings(_env_file=None)
    limits = limits_of(settings)
    case = case_tools.load_case(session, case_id)
    snapshot = case_tools.require_snapshot(session, case_id)
    inputs = load_case_inputs(session, case)
    countries = {site_id: site.country for site_id, site in inputs.sites.items()}
    alternates = tuple(
        AlternateInfo(a.part_id, a.site_id, a.price, a.lead_time_days, a.max_qty, a.air_lead_time_days,
                      a.air_freight_per_unit, countries.get(a.site_id))
        for a in session.scalars(select(AlternateSource).order_by(AlternateSource.part_id, AlternateSource.site_id))
    )
    rows = list(session.scalars(
        select(PriorityRow).where(PriorityRow.case_id == case_id, PriorityRow.version == snapshot.version)))
    rar = {r.site_id: r.rar for r in rows}
    recovery = inputs.recovery()
    sites = tuple(
        SiteFact(
            cs.site_id, inputs.sites[cs.site_id].tier, rar.get(cs.site_id, 0), inputs.sites[cs.site_id].bcp_score,
            recovery[cs.site_id], inputs.sites[cs.site_id].backup_site, inputs.sites[cs.site_id].backup_ramp_days,
            inputs.sites[cs.site_id].backup_surcharge_pct, cs.backup_ramp_confirmed, cs.backup_declined,
        )
        for cs in inputs.case_sites
    )
    tier_links = tuple(
        TierLinkFact(l.tier2_site, l.tier1_site, l.component, tuple(l.parts))
        for l in session.scalars(select(TierLink).order_by(TierLink.tier2_site, TierLink.tier1_site, TierLink.component))
    )
    request_rows = list(session.scalars(select(SupplierRequest).where(SupplierRequest.case_id == case_id)))
    requests = {(r.site_id, r.request_type): RequestState(r.status, r.answer) for r in request_rows}
    # R-15 point 6: an impact question that is not answered yet makes the recommendation preliminary.
    preliminary = any(kind == "IMPACT" and state.status == "REQUESTED" for (_, kind), state in requests.items())
    recommendation = recommend(
        erp, lambda state, replaced=None: case_impact(session, inputs, state, case, d0, replaced), d0=d0,
        alternates=alternates, sites=sites, preliminary=preliminary, risk_type=case.risk_type, tier_links=tier_links,
        requests=requests, close_call_band=limits.close_call_band_pp,
    )
    escalation = escalation_of(
        session, case, snapshot, recommendation, score=max((r.score for r in rows), default=0.0),
        silent_sites=sorted({r.site_id for r in request_rows if r.status == "NO_RESPONSE"}), limits=limits,
        meeting_room_enabled=settings.feature_meeting_room,
    )
    return CaseRecommendation(case, snapshot, recommendation, escalation)


def escalation_of(
    session: Session, case: Case, snapshot: ImpactSnapshot, recommendation: Recommendation, *, score: float,
    silent_sites: list[str], limits: Limits, meeting_room_enabled: bool,
) -> dict[str, Any]:
    """G.7: the R-22 tier of the case and what the case already has (latest meeting, latest decision note).

    The tier is calculated from the snapshot (impact, score) and the live options. `needs_meeting` and `decision_required` need
    F-07: with the flag off the tier is still shown, but no meeting is created and no decision note is asked for."""
    payload = snapshot.payload
    recommended = next((o for o in recommendation.options if o.option == recommendation.recommended_option), None)
    duty = next((o.before.extra_duty for o in recommendation.options), None)  # the extra duty of a tariff case
    result = escalate(
        risk_type=case.risk_type, case_score=score, rar=payload["totals"]["rar"], recommended=recommended,
        close_call=recommendation.close_call, duty=duty, silent_sites=silent_sites,
        impact_has_lines=bool(payload["po_lines"]), impact_has_orders=bool(payload["production_orders"]),
        impact_has_sos=bool(payload["sales_orders"]), limits=limits,
    )
    meeting, decision = latest_meeting(session, case.case_id), latest_decision(session, case.case_id)
    submittable = any(o.available and not o.pending_confirmation and o.actions for o in recommendation.options)
    return {
        "case_id": case.case_id, "version": snapshot.version, **result.to_dict(),
        "recommended_option": recommendation.recommended_option,
        "premium": None if recommended is None or recommended.after is None else recommended.after.premium,
        "extra_duty": duty, "limits": limits.to_dict(), "meeting_tier": case.meeting_tier,
        "needs_meeting": meeting_room_enabled and result.tier > case.meeting_tier,
        "decision_required": meeting_room_enabled and result.tier == 2 and decision is None and submittable,
        "meeting": None if meeting is None else meeting_view(meeting),
        "decision": None if decision is None else decision_view(decision),
    }


def detail_with_escalation(
    session: Session, erp: ErpData, case_id: str, version: int | None, d0: date, settings: Settings
) -> CaseDetail:
    """EP-12 / TL-17: the case page with its escalation (G.7). For a version that is not the latest, the tier saved with that
    version (TL-16) is shown when there is one; the meeting and the decision note are always the latest of the case."""
    if case_tools.latest_snapshot(session, case_id) is None:
        return build_case_detail(session, case_id, version, escalation={})  # 404: the case, or its snapshot, does not exist
    built = build_recommendation(session, erp, case_id, d0, settings=settings)
    escalation = built.escalation
    if version is not None and version != built.version:
        saved = session.get(RecommendationRow, (case_id, version))
        if saved is not None and saved.escalation:
            escalation = {**saved.escalation, "meeting": escalation["meeting"], "decision": escalation["decision"]}
    return build_case_detail(session, case_id, version, escalation=escalation)


def public_view(session: Session, built: CaseRecommendation, *, execution_enabled: bool) -> dict[str, Any]:
    """EP-15: the tool view plus `execution_enabled` and, per option, the saved rationale (null until one is saved)."""
    saved = session.get(RecommendationRow, (built.case.case_id, built.version))
    rationales = (saved.rationale or {}) if saved is not None else {}
    view = built.tool_view()
    for option in view["options"]:
        text = rationales.get(option["option"])
        option["rationale"] = text
        option["rationale_source"] = None if text is None else saved.rationale_source
    return {**view, "execution_enabled": execution_enabled}


# --- TL-16 -------------------------------------------------------------------------------------------------------

def _rationale_error(message: str) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", message, {"field": "rationales"})


def save_rationales(
    session: Session,
    erp: ErpData,
    case_id: str,
    version: int,
    rationales: dict[str, str],
    d0: date,
    *,
    attempts: ValidationAttempts,
    run_id: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """G-04 on the texts an agent wrote for the options, then store them.

    The options are recomputed here and the agent cannot change them. A text with a number that is not in the data of the
    case is refused the first time (`accepted = false` with the offending numbers); the second time for the same run, case
    and version the template text of the option is stored instead (`fallback_template_used`).
    """
    case_tools.load_case(session, case_id)
    if session.get(ImpactSnapshot, (case_id, version)) is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} has no version {version}.", {"case_id": case_id, "version": version})
    built = build_recommendation(session, erp, case_id, d0, settings=settings)
    if version != built.version:
        raise ApiError(409, "CONFLICT", f"Version {version} is not the latest ({built.version}); recompute the options.",
                       {"case_id": case_id, "latest_version": built.version})
    options = built.recommendation.options
    available = {o.option for o in options if o.available}
    if not rationales:
        raise _rationale_error("rationales must not be empty.")
    unknown = sorted(set(rationales) - available)
    if unknown:
        raise _rationale_error(f"rationales has no available option {unknown}; available: {sorted(available)}.")
    if any(not text.strip() for text in rationales.values()):
        raise _rationale_error("A rationale must not be empty.")

    allowed = {
        "recommendation": built.tool_view(), "impact": built.snapshot.payload,
        "priority": [row.model_dump() for row in case_tools.priority_out(session, case_id, built.version)],
    }
    offending = list(dict.fromkeys(n for text in rationales.values() for n in check_numbers(text, allowed)))
    key = ("save_recommendation", case_id, version, run_id)
    attempt = attempts.next(key)
    if offending and attempt == 1:
        return {"accepted": False, "validator": {
            "passed": False, "offending_numbers": offending, "attempt": attempt, "fallback_template_used": False}}
    attempts.clear(key)

    fallback = bool(offending)
    if fallback:
        texts = {o.option: template_rationale(o, recommended=o.option == built.recommendation.recommended_option,
                                              rule_reason=built.recommendation.rule_reason)
                 for o in options if o.available}
    else:
        texts = {name: text.strip() for name, text in rationales.items()}
    row = session.get(RecommendationRow, (case_id, version)) or RecommendationRow(case_id=case_id, version=version)
    row.options = [o.to_dict() for o in options]
    row.recommended_option = built.recommendation.recommended_option
    row.rule_reason = built.recommendation.rule_reason
    row.preliminary = built.recommendation.preliminary
    row.confirmations_needed = [{"site_id": site, "request_type": kind}
                                for site, kind in built.recommendation.confirmations_needed]
    row.escalation = {key: value for key, value in built.escalation.items() if key not in ("meeting", "decision")}
    row.rationale = texts
    row.rationale_source = "template" if fallback else "llm"
    session.add(row)
    return {"accepted": True, "validator": {
        "passed": not fallback, "offending_numbers": offending, "attempt": attempt, "fallback_template_used": fallback}}
