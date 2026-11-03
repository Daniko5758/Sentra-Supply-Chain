"""Meetings, decision notes and meeting e-mails as the API shows them (API_CONTRACT G.7, G.8, G.9; EP-12, EP-20, EP-30, TL-13, TL-28).

Reads only (the rows are written by `meeting_service`). The response models live here because EP-12, EP-17, EP-20 and the tools
share the `escalation` object.
"""

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.models import Meeting, MeetingDecision, Notification
from sentra_api.timeutil import iso, iso_or_none

ParticipantRole = Literal["BUYER", "PPIC", "SALES", "SCM_MANAGER", "CFO", "COO", "CEO", "LEGAL"]
MeetingKind = Literal["MEETING_ROOM", "EXECUTIVE_MEETING_ROOM"]


class ReasonOut(BaseModel):
    code: Literal["M1", "M2", "M3", "W1", "W2", "W3"]
    text: str


class LimitsOut(BaseModel):
    scm_approval_limit: int
    tariff_escalation_duty: int
    close_call_band_pp: int
    premium_max_pct: int


class MeetingOut(BaseModel):
    """G.8. There is no `start_url`, token or password of Zoom in any response."""

    meeting_id: str
    case_id: str
    tier: Literal[1, 2]
    kind: MeetingKind
    provider: Literal["ZOOM", "FALLBACK", "NONE"]
    status: Literal["SCHEDULED", "FAILED"]
    join_url: str | None
    start_time: str
    duration_min: int
    topic: str
    agenda: str
    agenda_source: Literal["llm", "template"]
    participants: list[ParticipantRole]
    required_approvers: list[ParticipantRole]
    reasons: list[ReasonOut]
    case_version: int
    error: str | None
    created_at: str


class DecisionOut(BaseModel):
    """G.9."""

    decision_id: str
    case_id: str
    meeting_id: str
    case_version: int
    option: str | None
    budget_approved: bool
    approved_by: list[ParticipantRole]
    notes: str | None
    decided_by: str
    decided_at: str


class EscalationOut(BaseModel):
    """G.7."""

    case_id: str
    version: int
    tier: Literal[0, 1, 2]
    kind: MeetingKind | None
    reasons: list[ReasonOut]
    participants: list[ParticipantRole]
    required_approvers: list[ParticipantRole]
    recommended_option: str | None
    premium: int | None
    extra_duty: int | None
    limits: LimitsOut
    meeting_tier: Literal[0, 1, 2]
    needs_meeting: bool
    decision_required: bool
    meeting: MeetingOut | None
    decision: DecisionOut | None


class NotificationOut(BaseModel):
    """The latest meeting e-mail of EP-20."""

    status: Literal["SENT", "FAILED"]
    kind: MeetingKind | None
    recipients: list[ParticipantRole]
    sent_at: str | None
    error: str | None
    meeting_id: str | None


def meeting_view(meeting: Meeting) -> dict[str, Any]:
    return {
        "meeting_id": meeting.meeting_id, "case_id": meeting.case_id, "tier": meeting.tier, "kind": meeting.kind,
        "provider": meeting.provider, "status": meeting.status, "join_url": meeting.join_url,
        "start_time": iso(meeting.start_time), "duration_min": meeting.duration_min, "topic": meeting.topic,
        "agenda": meeting.agenda, "agenda_source": meeting.agenda_source, "participants": list(meeting.participants),
        "required_approvers": list(meeting.required_approvers), "reasons": list(meeting.reasons),
        "case_version": meeting.case_version, "error": meeting.error, "created_at": iso(meeting.created_at),
    }


def decision_view(decision: MeetingDecision) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id, "case_id": decision.case_id, "meeting_id": decision.meeting_id,
        "case_version": decision.case_version, "option": decision.option, "budget_approved": decision.budget_approved,
        "approved_by": list(decision.approved_by), "notes": decision.notes, "decided_by": decision.decided_by,
        "decided_at": iso(decision.decided_at),
    }


def notification_view(notification: Notification) -> dict[str, Any]:
    """EP-20 `notification`."""
    return {"status": notification.status, "kind": notification.kind, "recipients": list(notification.recipients),
            "sent_at": iso_or_none(notification.sent_at), "error": notification.error, "meeting_id": notification.meeting_id}


def latest_meeting(session: Session, case_id: str) -> Meeting | None:
    return session.scalars(
        select(Meeting).where(Meeting.case_id == case_id).order_by(Meeting.created_at.desc(), Meeting.meeting_id.desc()).limit(1)
    ).first()


def case_meetings(session: Session, case_id: str) -> list[Meeting]:
    """Newest first (EP-20 `meetings`)."""
    return list(session.scalars(
        select(Meeting).where(Meeting.case_id == case_id).order_by(Meeting.created_at.desc(), Meeting.meeting_id.desc())))


def latest_decision(session: Session, case_id: str) -> MeetingDecision | None:
    """The note that counts: the latest of the case, not per version (R-22)."""
    return session.scalars(
        select(MeetingDecision).where(MeetingDecision.case_id == case_id)
        .order_by(MeetingDecision.decided_at.desc(), MeetingDecision.decision_id.desc()).limit(1)
    ).first()


def latest_notification(session: Session, case_id: str) -> Notification | None:
    """The latest meeting e-mail (rows made for a meeting carry `meeting_id`)."""
    return session.scalars(
        select(Notification).where(Notification.case_id == case_id, Notification.meeting_id.is_not(None))
        .order_by(Notification.id.desc()).limit(1)
    ).first()
