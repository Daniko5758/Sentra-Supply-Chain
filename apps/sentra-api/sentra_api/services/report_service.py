"""TL-19 `get_report_data`, TL-20 `save_report` and EP-17 on the database (FR-15).

The data of a report (KPI, impact, verification, schedule, recommendation, actions, charts) is read from the snapshots and the
rules on every call, so it is always the data of the latest snapshot (G-06). The only thing an agent hands over is the narrative;
it is stored after the number validator accepted it against that data (G-04), together with the data it was written for.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.config import Settings
from sentra_api.erp import ErpData
from sentra_api.errors import ApiError
from sentra_api.models import Action, AgentRun, Case, ImpactSnapshot, Report, SupplierMessage
from sentra_api.number_validator import check_numbers
from sentra_api.report_rules import SECTIONS, build_charts, file_title, template_narrative
from sentra_api.services import case_tools, recommendation_service, schedule_service
from sentra_api.services.case_detail import CaseSiteOut, build_case_detail
from sentra_api.services.runs import GENERATE_REPORT_TASK, enqueue_run
from sentra_api.services.validation_attempts import ValidationAttempts
from sentra_api.timeutil import iso


@dataclass(frozen=True)
class ReportData:
    case: Case
    version: int
    body: dict[str, Any]  # everything of TL-19 except `version`; this is what is stored as `report.payload`

    def tool_view(self) -> dict[str, Any]:
        """TL-19."""
        return {"version": self.version, **self.body}


def is_simulated(case: Case) -> bool:
    """The SIMULATED badge follows the origin of the signal that opened the case (DEC-024), not the application mode."""
    return case.mode == "DEMO"


def _verification(session: Session, case_id: str, sites: list[CaseSiteOut]) -> list[dict[str, Any]]:
    """One entry per site of the case, in the order of the priority: the answer state, the confidence scores and the reply."""
    latest_reply: dict[str, SupplierMessage] = {}
    for message in session.scalars(
        select(SupplierMessage)
        .where(SupplierMessage.case_id == case_id, SupplierMessage.direction == "IN")
        .order_by(SupplierMessage.created_at, SupplierMessage.id)
    ):
        latest_reply[message.site_id] = message
    entries = []
    for site in sites:
        reply = latest_reply.get(site.site_id)
        entries.append({
            "site_id": site.site_id, "supplier_name": site.supplier_name, "tier": site.tier,
            "verification_status": site.verification_status, "delay_override_days": site.delay_override_days,
            "excluded": site.excluded, "k_a": site.k_a, "k_b": site.k_b, "rpt_status": site.rpt_status,
            "reply": None if reply is None else {
                "body": reply.body, "created_at": iso(reply.created_at), "simulated": reply.simulated},
        })
    return entries


def _actions(session: Session, case_id: str) -> list[dict[str, Any]]:
    """The actions of the case (F-12); none exist while the options are only proposals."""
    rows = session.scalars(select(Action).where(Action.case_id == case_id).order_by(Action.action_id))
    return [{"action_id": a.action_id, "option": a.option, "type": a.type, "status": a.status, "payload": a.payload,
             "erp_result": a.erp_result, "error": a.error} for a in rows]


def build_report_data(
    session: Session, erp: ErpData, case_id: str, d0: date, *, execution_enabled: bool, settings: Settings | None = None
) -> ReportData:
    """TL-19 for the latest snapshot. 404 unknown case, 409 while the case has no snapshot yet."""
    case = case_tools.load_case(session, case_id)
    case_tools.require_snapshot(session, case_id)
    built = recommendation_service.build_recommendation(session, erp, case_id, d0, settings=settings)
    detail = build_case_detail(session, case_id, None, escalation=built.escalation)  # the figures of the case page (EP-12)
    snapshots = [(s.version, s.payload) for s in session.scalars(
        select(ImpactSnapshot).where(ImpactSnapshot.case_id == case_id).order_by(ImpactSnapshot.version))]
    priority = [row.model_dump() for row in detail.priority]
    body = {
        "kpi": detail.kpi.model_dump(),
        "signal": detail.signal.model_dump(),
        "impact": detail.impact,
        "verification": _verification(session, case_id, detail.sites),
        "schedule": schedule_service.po_schedule(session, erp, d0)["totals"],
        "recommendation": recommendation_service.public_view(session, built, execution_enabled=execution_enabled),
        "escalation": built.escalation,
        "actions": _actions(session, case_id),
        "charts": build_charts(priority, detail.impact, snapshots),
    }
    return ReportData(case, detail.version, body)


# --- TL-20 -------------------------------------------------------------------------------------------------------

def save_narrative(
    session: Session,
    erp: ErpData,
    case_id: str,
    version: int,
    narrative_md: str,
    d0: date,
    *,
    attempts: ValidationAttempts,
    run_id: str | None,
    execution_enabled: bool,
    now: datetime,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """G-04 on the narrative an agent wrote, then store it with the data of the report.

    The data is recomputed here and the agent cannot change it. A text with a number that is not in the data is refused the first
    time (`accepted = false` with the offending numbers); the second time for the same run, case and version the template text
    is stored instead (`fallback_template_used`).
    """
    case_tools.load_case(session, case_id)
    if session.get(ImpactSnapshot, (case_id, version)) is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} has no version {version}.", {"case_id": case_id, "version": version})
    data = build_report_data(session, erp, case_id, d0, execution_enabled=execution_enabled, settings=settings)
    if version != data.version:
        raise ApiError(409, "CONFLICT", f"Version {version} is not the latest ({data.version}); read the report data again.",
                       {"case_id": case_id, "latest_version": data.version})
    text = narrative_md.strip()
    if not text:
        raise ApiError(400, "VALIDATION_FAILED", "narrative_md must not be empty.", {"field": "narrative_md"})

    view = data.tool_view()
    offending = check_numbers(text, view)
    key = ("save_report", case_id, version, run_id)
    attempt = attempts.next(key)
    if offending and attempt == 1:
        return {"accepted": False, "validator": {
            "passed": False, "offending_numbers": offending, "attempt": attempt, "fallback_template_used": False}}
    attempts.clear(key)

    fallback = bool(offending)
    row = session.get(Report, (case_id, version)) or Report(case_id=case_id, version=version)
    row.payload = data.body
    row.narrative_md = template_narrative(view, simulated=is_simulated(data.case)) if fallback else text
    row.narrative_source = "template" if fallback else "llm"
    row.generated_at = now
    session.add(row)
    return {"accepted": True, "validator": {
        "passed": not fallback, "offending_numbers": offending, "attempt": attempt, "fallback_template_used": fallback}}


# --- EP-17 -------------------------------------------------------------------------------------------------------

def latest_report(session: Session, case_id: str) -> Report | None:
    return session.scalars(select(Report).where(Report.case_id == case_id).order_by(Report.version.desc()).limit(1)).first()


def public_view(case: Case, report: Report) -> dict[str, Any]:
    """EP-17: the stored report; the data and the charts are those saved with the narrative."""
    data = {key: value for key, value in report.payload.items() if key != "charts"}
    return {
        "case_id": report.case_id, "version": report.version, "generated_at": iso(report.generated_at),
        "simulated": is_simulated(case), "file_title": file_title(report.case_id, report.version),
        "narrative_md": report.narrative_md, "narrative_source": report.narrative_source, "sections": list(SECTIONS),
        "data": data, "charts": report.payload["charts"],
    }


def request_generation(session: Session, case_id: str, now: datetime) -> AgentRun:
    """`Generate report`: queue a `generate_report` run, or return the one already waiting or running for the case.
    404 unknown case, 409 while the case has no snapshot to report on."""
    case_tools.load_case(session, case_id)
    case_tools.require_snapshot(session, case_id)
    return enqueue_run(session, task=GENERATE_REPORT_TASK, trigger="manual", case_id=case_id, now=now)
