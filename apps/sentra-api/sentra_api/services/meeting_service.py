"""F-07 (R-22, DEC-039): the Zoom meeting and the e-mail when the escalation tier of a case rises (TL-13 v2), the decision note of an
Executive Meeting Room (EP-30), and the Meeting Room page of a case (EP-20).

The tier, the reasons and the participants are decided by `escalation.escalate` (G-06). Only the agenda text may come from the agent,
and it is used only when it passes the number validator against the data of the case (G-04); otherwise the template of R-22 is used.
The network calls (Zoom, SMTP) run in a worker thread and before the rows are written, so no database write lock is held meanwhile.
"""

import asyncio
from datetime import datetime, timedelta
from html import escape
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.config import Settings
from sentra_api.errors import ApiError
from sentra_api.escalation import PARTICIPANT_ORDER
from sentra_api.flags import flag_enabled
from sentra_api.integrations import timed_call
from sentra_api.integrations.mailer import addresses_for, send_mail
from sentra_api.integrations.zoom import ZoomClient
from sentra_api.models import Case, CaseSite, Meeting, MeetingDecision, Notification
from sentra_api.number_validator import check_numbers
from sentra_api.priority import format_idr, level as score_level
from sentra_api.services import case_tools
from sentra_api.services.case_detail import PriorityRowOut
from sentra_api.services.ids import DECISION_PREFIX, MEETING_PREFIX, next_id
from sentra_api.services.meeting_records import (
    EscalationOut, MeetingOut, NotificationOut, ParticipantRole, case_meetings, decision_view, latest_notification,
    meeting_view, notification_view,
)
from sentra_api.services.recommendation_service import CaseRecommendation
from sentra_api.services.system_status import record_integration_call
from sentra_api.timeutil import JAKARTA, iso

KIND_TITLES = {"MEETING_ROOM": "Meeting Room", "EXECUTIVE_MEETING_ROOM": "Executive Meeting Room"}
AGENDA_LIMIT = 1500
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DECISION_STEP, DECISION_TASK = "record_decision", "decision"


# --- agenda, topic, e-mail (pure text) -----------------------------------------------------------------------------------------

def template_agenda(case_title: str, built: CaseRecommendation) -> str:
    """R-22: `Decide on {case title}. Recommended: Option {X} ({title}). Reasons: {reasons joined by "; "}.`"""
    escalation = built.escalation
    reasons = "; ".join(r["text"] for r in escalation["reasons"])
    chosen = next((o for o in built.recommendation.options if o.option == escalation["recommended_option"]), None)
    recommended = f" Recommended: Option {chosen.option} ({chosen.title})." if chosen is not None else " No option is recommended by rule."
    return f"Decide on {case_title}.{recommended} Reasons: {reasons}."


def choose_agenda(agent_text: str | None, case_title: str, built: CaseRecommendation) -> tuple[str, str, dict[str, Any]]:
    """The agenda, its source (`llm` | `template`) and `agenda_validator` of TL-13. The agent text is used when it is not empty, has
    at most 1.500 characters and every number in it is in the data of the case (TL-28 and the latest recommendation); else the
    template is used, without a second try."""
    text = (agent_text or "").strip()
    offending: list[str] = []
    if text and len(text) <= AGENDA_LIMIT:
        offending = check_numbers(text, {"escalation": built.escalation, "recommendation": built.tool_view()})
        if not offending:
            return text, "llm", {"passed": True, "offending_numbers": [], "fallback_template_used": False}
    return (template_agenda(case_title, built), "template",
            {"passed": False, "offending_numbers": offending, "fallback_template_used": True})


def meeting_topic(kind: str, case_title: str, case_id: str) -> str:
    return f"SENTRA {KIND_TITLES[kind]} — {case_title} ({case_id})"


def _long_date(moment: datetime) -> str:
    local = moment.astimezone(JAKARTA)
    return f"{local.day:02d} {_MONTHS[local.month - 1]} {local.year} {local:%H:%M} WIB"


def case_figures(session: Session, built: CaseRecommendation) -> dict[str, Any]:
    """The numbers the e-mail and the Meeting Room page show: case score and level, units blocked and revenue at risk."""
    rows = case_tools.priority_out(session, built.case.case_id, built.version)
    score = max((row.score for row in rows), default=0.0)
    totals = built.snapshot.payload["totals"]
    return {"score": score, "level": score_level(score), "units": totals["units_blocked"], "rar": totals["rar"]}


def compose_email(settings: Settings, built: CaseRecommendation, figures: dict[str, Any], meeting: dict[str, Any]) -> tuple[str, str, str]:
    """Subject, plain text and HTML of the meeting e-mail (R-22). It names the reasons, the recommended option with its extra cost,
    the approvers, `Join Zoom meeting` (when there is a link) and `Open Meeting Room` (the case page)."""
    case, escalation = built.case, built.escalation
    kind, simulated = meeting["kind"], case.mode == "DEMO"
    subject = (f"[SENTRA][{KIND_TITLES[kind].upper()} · {figures['level']} {figures['score']:.1f}] {case.title}: "
               f"{figures['units']} units, {format_idr(figures['rar'])} at risk" + (" (SIMULATED)" if simulated else ""))
    chosen = next((o for o in built.recommendation.options if o.option == escalation["recommended_option"]), None)
    if chosen is None:
        recommended = "No option is recommended by rule."
    else:
        recommended = f"Recommended: Option {chosen.option} ({chosen.title}), extra cost {format_idr(escalation['premium'] or 0)}."
    when = _long_date(datetime.fromisoformat(meeting["start_time"]))
    open_url = f"{settings.app_base_url.rstrip('/')}/cases/{case.case_id}"
    lines = [f"{KIND_TITLES[kind]}: {case.title} ({case.case_id})" + (" — SIMULATED" if simulated else ""), "",
             f"Score {figures['score']:.1f} ({figures['level']}), {figures['units']} units blocked, {format_idr(figures['rar'])} at risk.",
             "", "Why this meeting:"]
    lines += [f"- {reason['text']}" for reason in meeting["reasons"]]
    lines += ["", recommended]
    if meeting["required_approvers"]:
        lines.append("Required approvers: " + ", ".join(meeting["required_approvers"]) + ".")
    lines += ["", f"When: {when}, {meeting['duration_min']} minutes."]
    if meeting["join_url"]:
        lines.append(f"Join Zoom meeting: {meeting['join_url']}")
    lines.append(f"Open Meeting Room: {open_url}")
    if simulated:
        lines += ["", "This case was opened by a simulated signal (SIMULATED)."]
    text = "\n".join(lines)

    items = "".join(f"<li>{escape(reason['text'])}</li>" for reason in meeting["reasons"])
    join = (f'<a href="{escape(meeting["join_url"])}">Join Zoom meeting</a> · ' if meeting["join_url"] else "")
    approvers = (f"<p>Required approvers: {escape(', '.join(meeting['required_approvers']))}.</p>"
                 if meeting["required_approvers"] else "")
    html = (f"<h3>{escape(KIND_TITLES[kind])}: {escape(case.title)} ({escape(case.case_id)})"
            f"{' — SIMULATED' if simulated else ''}</h3>"
            f"<p>Score {figures['score']:.1f} ({figures['level']}), {figures['units']} units blocked, "
            f"{escape(format_idr(figures['rar']))} at risk.</p><p>Why this meeting:</p><ul>{items}</ul>"
            f"<p>{escape(recommended)}</p>{approvers}<p>When: {escape(when)}, {meeting['duration_min']} minutes.</p>"
            f'<p>{join}<a href="{escape(open_url)}">Open Meeting Room</a></p>')
    return subject, text, html


# --- TL-13 v2 ------------------------------------------------------------------------------------------------------------------

def _notification_summary(notification: Notification) -> dict[str, Any]:
    """TL-13 `notification`."""
    return {"status": notification.status, "kind": notification.kind, "recipients": list(notification.recipients),
            "error": notification.error}


def _skipped(status: str, tier: int) -> dict[str, Any]:
    return {"status": status, "tier": tier, "meeting": None, "notification": None, "agenda_validator": None}


async def _send_meeting_email(
    app: FastAPI, session: Session, built: CaseRecommendation, meeting: dict[str, Any], *, now: datetime
) -> Notification:
    """Send the e-mail of a meeting, write the `notification` row and the `gmail` call. SENT or FAILED, never an exception."""
    settings = app.state.settings
    figures = case_figures(session, built)
    subject, text, html = compose_email(settings, built, figures, meeting)
    recipients = addresses_for(settings, meeting["participants"])
    _, millis, error = await asyncio.to_thread(
        timed_call, send_mail, settings, recipients=recipients, subject=subject, text=text, html=html)
    record_integration_call(session, "gmail", "OK" if error is None else "FAILED", millis, error, now)
    notification = Notification(
        case_id=meeting["case_id"], recipients=list(meeting["participants"]), subject=subject,
        status="SENT" if error is None else "FAILED", error=error, sent_at=now if error is None else None,
        meeting_id=meeting["meeting_id"], kind=meeting["kind"])
    session.add(notification)
    session.flush()
    return notification


async def notify(app: FastAPI, session: Session, built: CaseRecommendation, *, agenda: str | None, now: datetime) -> dict[str, Any]:
    """TL-13 v2. The meeting and the e-mail are made only when the tier is above `case.meeting_tier` (0 -> 1, 0 -> 2, 1 -> 2): a tier
    that stays or falls makes nothing new, and the meeting of before stays on the page. Zoom or SMTP failing never raises."""
    settings, case, escalation = app.state.settings, built.case, built.escalation
    tier = escalation["tier"]
    if not flag_enabled(settings, "FEATURE_MEETING_ROOM"):
        return _skipped("SKIPPED_FEATURE_OFF", tier)
    if tier <= case.meeting_tier:
        return _skipped("NOT_NEEDED", tier)

    kind = escalation["kind"]
    text, source, validator = choose_agenda(agenda, case.title, built)
    topic = meeting_topic(kind, case.title, case.case_id)
    start = now + timedelta(minutes=settings.meeting_start_delay_min)
    duration = settings.meeting_duration_tier2_min if tier == 2 else settings.meeting_duration_tier1_min

    zoom: ZoomClient = app.state.zoom
    created, millis, zoom_error = await asyncio.to_thread(
        timed_call, zoom.create_meeting, topic=topic, start_time=start, duration_min=duration, agenda=text)
    record_integration_call(session, "zoom", "OK" if zoom_error is None else "FAILED", millis, zoom_error, now)
    provider, status, zoom_id, join_url, error = "ZOOM", "SCHEDULED", None, None, None
    if created is not None:
        zoom_id, join_url = created.zoom_meeting_id, created.join_url
    elif settings.zoom_fallback_join_url:
        provider, join_url = "FALLBACK", settings.zoom_fallback_join_url
    else:
        provider, status, error = "NONE", "FAILED", zoom_error

    meeting = Meeting(
        meeting_id=next_id(session, Meeting.meeting_id, MEETING_PREFIX), case_id=case.case_id, tier=tier, kind=kind,
        provider=provider, status=status, zoom_meeting_id=zoom_id, join_url=join_url, start_time=start,
        duration_min=duration, topic=topic, agenda=text, agenda_source=source, participants=list(escalation["participants"]),
        required_approvers=list(escalation["required_approvers"]), reasons=list(escalation["reasons"]),
        case_version=built.version, error=error, created_at=now)
    session.add(meeting)
    case.meeting_tier = tier
    session.flush()
    notification = await _send_meeting_email(app, session, built, meeting_view(meeting), now=now)
    return {"status": "CREATED", "tier": tier, "meeting": meeting_view(meeting), "notification": _notification_summary(notification),
            "agenda_validator": validator}


# --- EP-20 ---------------------------------------------------------------------------------------------------------------------

class MeetingSiteOut(PriorityRowOut):
    """G.5 plus the answer state of the site."""

    verification_status: str


class LatestRecommendationOut(BaseModel):
    recommended_option: str | None
    preliminary: bool
    rule_reason: str


class MeetingRoomOut(BaseModel):
    """EP-20 (v1.11)."""

    case_id: str
    title: str
    simulated: bool
    score: float
    level: str
    status: str
    updated_at: str
    escalation: EscalationOut
    meetings: list[MeetingOut]
    notification: NotificationOut | None
    sites: list[MeetingSiteOut]
    latest_recommendation: LatestRecommendationOut


def meeting_room_view(session: Session, built: CaseRecommendation) -> MeetingRoomOut:
    """The Meeting Room page of a case: also for a tier-0 case (no meetings), then it shows `escalation.tier = 0`."""
    case = built.case
    figures = case_figures(session, built)
    states = {cs.site_id: cs.verification_status
              for cs in session.scalars(select(CaseSite).where(CaseSite.case_id == case.case_id))}
    notification = latest_notification(session, case.case_id)
    return MeetingRoomOut(
        case_id=case.case_id, title=case.title, simulated=case.mode == "DEMO", score=figures["score"], level=figures["level"],
        status=case.status, updated_at=iso(case.updated_at), escalation=EscalationOut(**built.escalation),
        meetings=[MeetingOut(**meeting_view(m)) for m in case_meetings(session, case.case_id)],
        notification=None if notification is None else NotificationOut(**notification_view(notification)),
        sites=[MeetingSiteOut(**row.model_dump(), verification_status=states.get(row.site_id, "NOT_NEEDED"))
               for row in case_tools.priority_out(session, case.case_id, built.version)],
        latest_recommendation=LatestRecommendationOut(
            recommended_option=built.recommendation.recommended_option, preliminary=built.recommendation.preliminary,
            rule_reason=built.recommendation.rule_reason))


async def resend_email(app: FastAPI, session: Session, built: CaseRecommendation, *, now: datetime) -> dict[str, Any]:
    """`Resend email`: the e-mail of the latest meeting again, only when the latest one failed. No new Zoom meeting."""
    latest = latest_notification(session, built.case.case_id)
    if latest is None or latest.status != "FAILED":
        raise ApiError(409, "CONFLICT", "The latest meeting e-mail did not fail, so there is nothing to resend.",
                       {"case_id": built.case.case_id})
    meeting = session.get(Meeting, latest.meeting_id)
    notification = await _send_meeting_email(app, session, built, meeting_view(meeting), now=now)
    return notification_view(notification)


# --- EP-30 ---------------------------------------------------------------------------------------------------------------------

class DecisionIn(BaseModel):
    meeting_id: str
    option: str | None = None
    budget_approved: bool = False
    approved_by: list[ParticipantRole]
    notes: str | None = Field(default=None, max_length=1000)


def _invalid(message: str, **details: Any) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", message, details)


def record_decision(session: Session, built: CaseRecommendation, body: DecisionIn, *, username: str, now: datetime,
                    custom_option: str | None = None) -> dict[str, Any]:
    """EP-30: the minutes of an Executive Meeting Room. The checks run in the order of the contract; the note is the notes of the
    meeting, not a second approval (the action approval stays with the SCM Manager, R-17).

    `custom_option`: the meeting chose its own ERP actions instead of a computed option (`custom_actions`); the option checks are
    skipped and the note carries that code. EP-30 itself never passes it, so a body with that code is refused there."""
    case, escalation = built.case, built.escalation
    if escalation["tier"] != 2:
        raise ApiError(409, "CONFLICT", "A decision record is only needed for Executive Meeting Room cases",
                       {"escalation_tier": escalation["tier"]})
    meeting = session.get(Meeting, body.meeting_id)
    if meeting is None or meeting.case_id != case.case_id or meeting.kind != "EXECUTIVE_MEETING_ROOM":
        raise _invalid("meeting_id must be an Executive Meeting Room of this case.", field="meeting_id")
    if custom_option is not None:
        body = body.model_copy(update={"option": custom_option})
    else:
        choosable = {o.option for o in built.recommendation.options if o.available and not o.pending_confirmation}
        if body.option is not None and body.option not in choosable:
            raise _invalid(f"Option {body.option} is not available or is waiting for supplier confirmation.", field="option")
        if body.option is None and choosable:
            raise _invalid("option is required: the case has an option that can be chosen.", field="option")
    approved = [role for role in PARTICIPANT_ORDER if role in set(body.approved_by)]
    missing = [role for role in escalation["required_approvers"] if role not in approved]
    if missing:
        raise _invalid("approved_by must include every required approver.", missing=missing)
    if any(r["code"] == "W1" for r in escalation["reasons"]) and body.budget_approved is not True:
        raise _invalid("budget_approved must be true: the extra cost is above the SCM Manager approval limit.", field="budget_approved")

    decision = MeetingDecision(
        decision_id=next_id(session, MeetingDecision.decision_id, DECISION_PREFIX), case_id=case.case_id,
        meeting_id=body.meeting_id, case_version=built.version, option=body.option, budget_approved=body.budget_approved,
        approved_by=approved, notes=body.notes, decided_by=username, decided_at=now)
    session.add(decision)
    session.flush()
    chosen = f"Option {body.option}" if body.option else "No option"
    case_tools.add_trace(
        session, run_id=None, case_id=case.case_id, task=DECISION_TASK, step=DECISION_STEP, tool=None,
        input_summary=f"meeting {body.meeting_id} by {username}", output_summary=decision.decision_id,
        decision=f"{chosen} approved by {', '.join(approved)}" + ("; budget approved" if body.budget_approved else ""),
        duration_ms=None, status="OK", now=now)
    case.updated_at = now
    return decision_view(decision)
