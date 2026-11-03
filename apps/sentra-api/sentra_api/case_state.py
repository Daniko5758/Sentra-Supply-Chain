"""Case status machine, PRD 8.3 / DATA_MODEL 3.3. Pure: no DB, clock, or env."""

from collections.abc import Iterable
from enum import StrEnum


class CaseStatus(StrEnum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    AWAITING_SUPPLIER = "AWAITING_SUPPLIER"
    READY_FOR_DECISION = "READY_FOR_DECISION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # F-12 only
    EXECUTED = "EXECUTED"  # F-12 only
    CLOSED = "CLOSED"
    DISMISSED = "DISMISSED"


CLOSED_STATUSES = (CaseStatus.CLOSED.value, CaseStatus.DISMISSED.value)  # a case in one of these is no longer open


class CaseEvent(StrEnum):
    ANALYSIS_STARTED = "ANALYSIS_STARTED"  # run analyze_case begins
    CONFIRMATION_REQUESTED = "CONFIRMATION_REQUESTED"  # a supplier confirmation is outstanding
    RECOMMENDATION_READY = "RECOMMENDATION_READY"  # recommendation saved, nothing waiting
    OPTION_SUBMITTED = "OPTION_SUBMITTED"  # Submit for approval (F-12)
    CLOSE = "CLOSE"  # Close case (SCM Manager)
    DISMISS = "DISMISS"  # Dismiss (SCM Manager)


class InvalidTransition(Exception):
    def __init__(self, status: CaseStatus, event: CaseEvent) -> None:
        super().__init__(f"event {event} is not allowed while the case is {status}")
        self.status = status
        self.event = event


S = CaseStatus
E = CaseEvent

_TRANSITIONS: dict[tuple[CaseStatus, CaseEvent], CaseStatus] = {
    (S.NEW, E.ANALYSIS_STARTED): S.ANALYZING,
    (S.ANALYZING, E.CONFIRMATION_REQUESTED): S.AWAITING_SUPPLIER,
    (S.ANALYZING, E.RECOMMENDATION_READY): S.READY_FOR_DECISION,
    (S.AWAITING_SUPPLIER, E.RECOMMENDATION_READY): S.READY_FOR_DECISION,
    (S.READY_FOR_DECISION, E.OPTION_SUBMITTED): S.AWAITING_APPROVAL,
    # DEC-043: after an execution the recomputed case can recommend a follow-up option (e.g. R after D); it is submitted from EXECUTED
    (S.EXECUTED, E.OPTION_SUBMITTED): S.AWAITING_APPROVAL,
    (S.READY_FOR_DECISION, E.CLOSE): S.CLOSED,
    (S.EXECUTED, E.CLOSE): S.CLOSED,
    # "Any status except CLOSED" -> DISMISSED. A dismissed case is terminal, so dismissing it again is not a transition.
    **{(status, E.DISMISS): S.DISMISSED for status in S if status not in (S.CLOSED, S.DISMISSED)},
}


def next_status(current: CaseStatus, event: CaseEvent) -> CaseStatus:
    try:
        return _TRANSITIONS[(CaseStatus(current), CaseEvent(event))]
    except KeyError:
        raise InvalidTransition(CaseStatus(current), CaseEvent(event)) from None


def settle_approval(action_statuses: Iterable[str]) -> CaseStatus:
    """Status of a case in AWAITING_APPROVAL after an action changed (F-12), from the statuses of its actions.

    EXECUTED: no PROPOSED/APPROVED/FAILED left and at least one EXECUTED (partial approval is fine).
    READY_FOR_DECISION: every action REJECTED. Otherwise stays AWAITING_APPROVAL (a FAILED action keeps the red banner
    until Retry succeeds or Cancel remaining actions turns it into REJECTED).
    """
    statuses = list(action_statuses)
    if not statuses:
        return S.AWAITING_APPROVAL
    if all(status == "REJECTED" for status in statuses):
        return S.READY_FOR_DECISION
    if all(status in ("EXECUTED", "REJECTED") for status in statuses):
        return S.EXECUTED
    return S.AWAITING_APPROVAL
