"""Agent run queue rows (`agent_run`). The worker that executes them is agent_runner (B-13).

Rules of the queue (PRD 6.4): one run per case at a time, runs of different cases may go in parallel, triggers that
arrive while a case is busy merge into one `resume_case`, one scan at a time.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.models import AgentRun
from sentra_api.services.ids import next_id
from sentra_api.timeutil import JAKARTA

ACTIVE_STATUSES = ("QUEUED", "RUNNING")
SCAN_TASK = "scan_external"
GENERATE_REPORT_TASK = "generate_report"


def _new_run(session: Session, *, task: str, trigger: str, case_id: str | None, now: datetime) -> AgentRun:
    day = now.astimezone(JAKARTA).strftime("%Y%m%d")
    run = AgentRun(run_id=next_id(session, AgentRun.run_id, f"RUN-{day}"), task=task, trigger=trigger,
                   case_id=case_id, status="QUEUED")
    session.add(run)
    session.flush()
    return run


def enqueue_run(session: Session, *, task: str, trigger: str, case_id: str | None, now: datetime) -> AgentRun:
    """Queue a run, or return the one already queued/running for the same case and task (one run per case, PRD 6.4)."""
    if case_id is not None:
        active = session.scalar(
            select(AgentRun)
            .where(AgentRun.case_id == case_id, AgentRun.task == task, AgentRun.status.in_(ACTIVE_STATUSES))
            .order_by(AgentRun.run_id)
            .limit(1)
        )
        if active is not None:
            return active
    return _new_run(session, task=task, trigger=trigger, case_id=case_id, now=now)


def start_inline_run(session: Session, *, task: str, trigger: str, case_id: str | None, now: datetime) -> AgentRun:
    """A run the request itself executes (chat, PRD 6.4): created RUNNING, so the queue worker never claims it."""
    run = _new_run(session, task=task, trigger=trigger, case_id=case_id, now=now)
    run.status, run.started_at = "RUNNING", now
    return run


def request_resume(session: Session, case_id: str, *, trigger: str, now: datetime) -> AgentRun:
    """Ask for a `resume_case` (a supplier replied, an ERP execution finished).

    Triggers that arrive while the case is busy wait in ONE queued run (PRD 6.4: three replies at once give one
    `resume_case`). A run that is already RUNNING has read the case before this trigger, so it needs a new run.
    """
    queued = session.scalar(
        select(AgentRun)
        .where(AgentRun.case_id == case_id, AgentRun.task == "resume_case", AgentRun.status == "QUEUED")
        .order_by(AgentRun.run_id)
        .limit(1)
    )
    if queued is not None:
        return queued
    return _new_run(session, task="resume_case", trigger=trigger, case_id=case_id, now=now)


def start_scan(session: Session, *, trigger: str, now: datetime, record_skipped: bool = False) -> AgentRun | None:
    """Queue a `scan_external` run: the one place that creates scan runs (EP-08, DEMO -> LIVE, the scheduler of B-30).

    N-16B adds `scan_partition` here and in `agent_runner.build_payload`, the one place that builds the scan payload.

    While another scan is queued or running this returns None (EP-08 answers 409), or, with `record_skipped`, a run
    that is already `SKIPPED` (EP-29, the scheduler): the attempt stays visible and no second scan starts.
    """
    busy = session.scalar(
        select(AgentRun.run_id).where(AgentRun.task == SCAN_TASK, AgentRun.status.in_(ACTIVE_STATUSES)).limit(1)
    )
    if busy is None:
        return _new_run(session, task=SCAN_TASK, trigger=trigger, case_id=None, now=now)
    if not record_skipped:
        return None
    run = _new_run(session, task=SCAN_TASK, trigger=trigger, case_id=None, now=now)
    run.status, run.started_at, run.finished_at = "SKIPPED", now, now
    run.summary = f"Skipped: scan {busy} is still running."
    return run
