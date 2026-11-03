from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sentra_api.db import Base, UtcDateTime, utc_now


class SupplierSite(Base):
    __tablename__ = "supplier_site"

    site_id: Mapped[str] = mapped_column(String, primary_key=True)
    supplier_name: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    kabupaten: Mapped[str | None] = mapped_column(String)
    # DATA_MODEL v1.4 (DEC-025): local-script names for CN/JP sites; [] when absent.
    kabupaten_local: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    kecamatan: Mapped[str | None] = mapped_column(String)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    hazard_zone_flood: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ttr_days: Mapped[int] = mapped_column(Integer, nullable=False)
    otd_12m: Mapped[float | None] = mapped_column(Float, nullable=True)
    backup_site: Mapped[str | None] = mapped_column(String)
    bcp_backup_site: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bcp_emergency_plan: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bcp_safety_stock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bcp_insurance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bcp_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # DATA_MODEL v1.6 (DEC-036): eight questionnaire answers 1-5 (`bcp_score` is R-07a v2 of them), stated RTO, backup ramp-up.
    bcp_answers: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    bcp_rto_days: Mapped[int] = mapped_column(Integer, nullable=False)
    backup_ramp_days: Mapped[int | None] = mapped_column(Integer)
    backup_surcharge_pct: Mapped[float | None] = mapped_column(Float)


class TierLink(Base):
    __tablename__ = "tier_link"

    tier2_site: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), primary_key=True)
    tier1_site: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), primary_key=True)
    component: Mapped[str] = mapped_column(String, primary_key=True)
    parts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    buffer_days: Mapped[int] = mapped_column(Integer, nullable=False)
    component_category: Mapped[str | None] = mapped_column(String)  # v1.6: category filter of R-01 v2 / R-18 v2


class AlternateSource(Base):
    __tablename__ = "alternate_source"

    part_id: Mapped[str] = mapped_column(String, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), primary_key=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    # v1.6: air-freight variant of option A (R-15 v2).
    air_lead_time_days: Mapped[int | None] = mapped_column(Integer)
    air_freight_per_unit: Mapped[int | None] = mapped_column(Integer)


class RptHistoryAffected(Base):
    __tablename__ = "rpt_history_affected"

    ID: Mapped[str] = mapped_column(String, primary_key=True)
    EVENT_TYPE: Mapped[str] = mapped_column(String, nullable=False)
    SEVERITY: Mapped[int] = mapped_column(Integer, nullable=False)
    SUBDISTRICT_MENTIONED: Mapped[str] = mapped_column(String, nullable=False)
    HAZARD_ZONE: Mapped[str] = mapped_column(String, nullable=False)
    HAS_BACKUP_SITE: Mapped[str] = mapped_column(String, nullable=False)
    AFFECTED: Mapped[str] = mapped_column(String, nullable=False)


class RptHistoryDelivery(Base):
    __tablename__ = "rpt_history_delivery"

    ID: Mapped[str] = mapped_column(String, primary_key=True)
    SITE_EVENT_STATUS: Mapped[str] = mapped_column(String, nullable=False)
    HAS_BACKUP_SITE: Mapped[str] = mapped_column(String, nullable=False)
    OTD_12M: Mapped[float] = mapped_column(Float, nullable=False)
    CURRENT_SLIP_DAYS: Mapped[int] = mapped_column(Integer, nullable=False)
    ON_TIME: Mapped[str] = mapped_column(String, nullable=False)


class Case(Base):
    __tablename__ = "case"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    risk_type: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    # DEC-024: origin of the first signal (DEMO/LIVE), not the application mode.
    mode: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="NEW")
    d0_date: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str | None] = mapped_column(String)
    # v1.6: highest escalation tier that already has a meeting (R-22: a meeting only when the tier rises).
    meeting_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Not used since v1.6 (DEC-039: Zoom replaces Jitsi, meetings live in `meeting`); always null.
    meeting_room_notified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    jitsi_url: Mapped[str | None] = mapped_column(String)
    # runtimeSessionId of the AgentCore runtime (PRD 6.4).
    agent_session_id: Mapped[str] = mapped_column(String, nullable=False, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class Signal(Base):
    __tablename__ = "signal"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    risk_type: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    kabupaten: Mapped[str | None] = mapped_column(String)
    kecamatan: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # DEC-019: 1-5 for LOCATION, null for COUNTRY.
    severity: Mapped[int | None] = mapped_column(Integer)
    downtime_days: Mapped[int | None] = mapped_column(Integer)  # v1.6: length of a country disruption (R-02 v2, R-18 v2)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    tariff_pp: Mapped[float | None] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    # DEC-015: null for DEMO; LIVE signals are stored only when VERIFIED.
    verification: Mapped[str | None] = mapped_column(String)
    # DEC-031: [] for DEMO.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_label: Mapped[str | None] = mapped_column(String)
    match: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("case.case_id"))


class CaseSite(Base):
    __tablename__ = "case_site"

    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), primary_key=True)
    k_a: Mapped[float | None] = mapped_column(Float)
    k_a_formula: Mapped[float | None] = mapped_column(Float)
    p_rpt_a: Mapped[float | None] = mapped_column(Float)
    k_b: Mapped[float | None] = mapped_column(Float)
    k_b_formula: Mapped[float | None] = mapped_column(Float)
    p_rpt_b: Mapped[float | None] = mapped_column(Float)
    rpt_status: Mapped[str] = mapped_column(String, nullable=False, default="OK")
    verification_status: Mapped[str] = mapped_column(String, nullable=False, default="NOT_NEEDED")
    delay_override_days: Mapped[int | None] = mapped_column(Integer)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # v1.6 (R-13 v2): answer to a BACKUP_ACTIVATION request.
    backup_ramp_confirmed: Mapped[int | None] = mapped_column(Integer)
    backup_declined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ImpactSnapshot(Base):
    __tablename__ = "impact_snapshot"

    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class PriorityRow(Base):
    __tablename__ = "priority_row"

    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), primary_key=True)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    recovery_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rar: Mapped[int] = mapped_column(Integer, nullable=False)
    output_value: Mapped[int] = mapped_column(Integer, nullable=False)
    po_value: Mapped[int] = mapped_column(Integer, nullable=False)
    bcp_score: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SupplierMessage(Base):
    __tablename__ = "supplier_message"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    request_id: Mapped[str | None] = mapped_column(String)  # v1.6: null for messages made before supplier requests
    request_type: Mapped[str] = mapped_column(String, nullable=False, default="IMPACT")


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    meeting_id: Mapped[str | None] = mapped_column(String)  # v1.6: the meeting the e-mail is about (R-22)
    kind: Mapped[str | None] = mapped_column(String)  # MEETING_ROOM | EXECUTIVE_MEETING_ROOM


class Recommendation(Base):
    __tablename__ = "recommendation"

    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    recommended_option: Mapped[str | None] = mapped_column(String)
    rule_reason: Mapped[str | None] = mapped_column(Text)
    # JSON per option; only this text may come from the LLM (G-04, G-06).
    rationale: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rationale_source: Mapped[str] = mapped_column(String, nullable=False, default="template")
    preliminary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # v1.6: TL-28 object without `meeting`/`decision`, and the supplier confirmations the recommended option still needs.
    escalation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confirmations_needed: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class SupplierRequest(Base):
    """One question to a supplier site per case and kind (R-12 v2, R-13 v2); `IMPACT` stays in step with `case_site.verification_status`."""

    __tablename__ = "supplier_request"
    __table_args__ = (UniqueConstraint("case_id", "site_id", "request_type"),)

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    site_id: Mapped[str] = mapped_column(ForeignKey("supplier_site.site_id"), nullable=False)
    request_type: Mapped[str] = mapped_column(String, nullable=False)  # IMPACT | BACKUP_ACTIVATION | PULL_IN
    status: Mapped[str] = mapped_column(String, nullable=False, default="REQUESTED")  # REQUESTED | ANSWERED | NO_RESPONSE
    answer: Mapped[str | None] = mapped_column(String)  # YES | PARTIAL | NO
    delay_days: Mapped[int | None] = mapped_column(Integer)  # IMPACT
    ramp_days: Mapped[int | None] = mapped_column(Integer)  # BACKUP_ACTIVATION
    po_lines: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)  # PULL_IN: [{po_id, item_no}]
    message_out_id: Mapped[str | None] = mapped_column(String)
    message_in_id: Mapped[str | None] = mapped_column(String)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    due_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Meeting(Base):
    """Meeting Room / Executive Meeting Room (R-22). `start_url`, tokens and passwords of Zoom are never stored."""

    __tablename__ = "meeting"

    meeting_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 | 2
    kind: Mapped[str] = mapped_column(String, nullable=False)  # MEETING_ROOM | EXECUTIVE_MEETING_ROOM
    provider: Mapped[str] = mapped_column(String, nullable=False)  # ZOOM | FALLBACK | NONE
    status: Mapped[str] = mapped_column(String, nullable=False)  # SCHEDULED | FAILED
    zoom_meeting_id: Mapped[str | None] = mapped_column(String)
    join_url: Mapped[str | None] = mapped_column(String)
    start_time: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(String, nullable=False)
    agenda: Mapped[str] = mapped_column(Text, nullable=False)
    agenda_source: Mapped[str] = mapped_column(String, nullable=False)  # llm | template
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    required_approvers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)  # [{code, text}]
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class MeetingDecision(Base):
    """Minutes of the decision of an Executive Meeting Room (EP-30). A case may have several; the latest one counts."""

    __tablename__ = "meeting_decision"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meeting.meeting_id"), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    option: Mapped[str | None] = mapped_column(String)  # null when the case has no option
    budget_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)  # roles
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String, nullable=False)  # username
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class Action(Base):
    __tablename__ = "action"

    action_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False)
    option: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PROPOSED")
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    proposed_by: Mapped[str] = mapped_column(String, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    executed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    erp_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class Report(Base):
    __tablename__ = "report"

    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    narrative_md: Mapped[str] = mapped_column(Text, nullable=False)
    narrative_source: Mapped[str] = mapped_column(String, nullable=False, default="template")
    generated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("case.case_id"))
    # UUID of the conversation = runtimeSessionId of the `chat` task.
    chat_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class AgentTrace(Base):
    __tablename__ = "agent_trace"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("case.case_id"))
    run_id: Mapped[str | None] = mapped_column(String)
    task: Mapped[str | None] = mapped_column(String)
    step: Mapped[str] = mapped_column(String, nullable=False)
    tool: Mapped[str | None] = mapped_column(String)
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


class SchedulerRun(Base):
    """O-03 job log; not cleared by Reset demo data."""

    __tablename__ = "scheduler_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_run"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    task: Mapped[str] = mapped_column(String, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("case.case_id"))
    status: Mapped[str] = mapped_column(String, nullable=False, default="QUEUED")
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Alert(Base):
    """One alert per case (updated when the level changes)."""

    __tablename__ = "alert"

    alert_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.case_id"), nullable=False, unique=True)
    level: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    units_at_risk: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue_at_risk: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    # Usernames that have read the alert (EP-10 `read` is per user).
    read_by: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class IntegrationCall(Base):
    """Source of System status (FR-27); secrets are never recorded; not cleared by Reset demo data."""

    __tablename__ = "integration_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    called_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)


# Children before parents (v1.6): meeting_decision < meeting, supplier_request < supplier_message, all before `case`.
RESET_TRANSACTION_TABLES = (
    "meeting_decision", "meeting", "supplier_request", "supplier_message", "notification", "recommendation", "action",
    "report", "chat_message", "agent_trace", "agent_run", "alert", "priority_row", "impact_snapshot", "signal",
    "case_site", "case",
)
