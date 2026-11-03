"""R-22 escalation tier of a case as a pure function (B-45). No DB, HTTP, clock, or environment access: the limits come as an argument.

Tier 2 (Executive Meeting Room) when a W reason holds; else tier 1 (Meeting Room) when an M reason holds; else tier 0 (in the app).
The texts, their order and the participants are those of RULES_AND_GOLDEN R-22; the code decides, never an LLM (G-06).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sentra_api.priority import format_idr, round1
from sentra_api.recommendation_rules import Option

# Order of the participants (R-22) and the roles that must approve in an Executive Meeting Room.
PARTICIPANT_ORDER = ("BUYER", "PPIC", "SALES", "SCM_MANAGER", "CFO", "COO", "CEO", "LEGAL")
EXECUTIVES = ("CFO", "COO", "CEO", "LEGAL")
KINDS = {1: "MEETING_ROOM", 2: "EXECUTIVE_MEETING_ROOM"}
HIGH_SCORE = 70  # M1: the case is HIGH (R-08)


@dataclass(frozen=True)
class Limits:
    """The company policy that R-22 reads (config SCM_APPROVAL_LIMIT_IDR, TARIFF_ESCALATION_DUTY_IDR, CLOSE_CALL_BAND_PP)."""

    scm_approval_limit: int = 100_000_000
    tariff_escalation_duty: int = 250_000_000
    close_call_band_pp: int = 2
    premium_max_pct: int = 10  # PREMIUM_MAX_RATIO of R-15 v2, as a percent

    def to_dict(self) -> dict[str, int]:
        return {"scm_approval_limit": self.scm_approval_limit, "tariff_escalation_duty": self.tariff_escalation_duty,
                "close_call_band_pp": self.close_call_band_pp, "premium_max_pct": self.premium_max_pct}


@dataclass(frozen=True)
class Reason:
    code: str  # W1 | W2 | W3 | M1 | M2 | M3
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "text": self.text}


@dataclass(frozen=True)
class Escalation:
    tier: int
    reasons: tuple[Reason, ...]
    participants: tuple[str, ...]
    required_approvers: tuple[str, ...]

    @property
    def kind(self) -> str | None:
        return KINDS.get(self.tier)

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "kind": self.kind, "reasons": [r.to_dict() for r in self.reasons],
                "participants": list(self.participants), "required_approvers": list(self.required_approvers)}


def _ordered(order: Sequence[str], people: set[str]) -> tuple[str, ...]:
    return tuple(role for role in order if role in people)


def escalate(
    *,
    risk_type: str,
    case_score: float,
    rar: int,
    recommended: Option | None,
    close_call: bool,
    duty: int | None,
    silent_sites: Sequence[str],
    impact_has_lines: bool,
    impact_has_orders: bool,
    impact_has_sos: bool,
    limits: Limits,
) -> Escalation:
    """The tier, the reasons, the participants and the approvers that must sign off.

    `recommended`: the option the rule chose (None = none); `close_call`: a protecting option costs within the band of the 10%
    premium policy (R-22 M2, decided by `recommendation_rules.choose_disruption`); `duty`: the extra duty of a tariff case (None
    when it is not stated or the case is no tariff); `silent_sites`: sites with a supplier request that got no reply (sorted).
    """
    executives: set[str] = set()
    weighty: list[Reason] = []
    premium = recommended.after.premium if recommended is not None and recommended.after is not None else 0
    if recommended is not None and premium > limits.scm_approval_limit:
        weighty.append(Reason("W1", f"Extra cost of Option {recommended.option} {format_idr(premium)} is above the "
                                    f"SCM Manager approval limit {format_idr(limits.scm_approval_limit)}"))
        executives.add("CFO")
    if recommended is not None and recommended.after is not None and recommended.after.units_blocked > 0:
        weighty.append(Reason("W2", f"{recommended.after.units_blocked} units stay blocked even with Option {recommended.option}"))
        executives.update(("COO", "CEO"))
    if risk_type == "POLITICAL" and rar > 0:
        weighty.append(Reason("W3", "Geopolitical or compliance event with revenue at risk"))
        executives.update(("CEO", "LEGAL"))
    if duty is not None and duty > limits.tariff_escalation_duty:
        weighty.append(Reason("W3", f"Extra duty {format_idr(duty)} is above {format_idr(limits.tariff_escalation_duty)}"))
        executives.add("CFO")

    base = {"SCM_MANAGER"}
    if impact_has_lines:
        base.add("BUYER")
    if impact_has_orders:
        base.add("PPIC")
    if impact_has_sos:
        base.add("SALES")
    if weighty:
        return Escalation(2, tuple(weighty), _ordered(PARTICIPANT_ORDER, base | executives), _ordered(EXECUTIVES, executives))

    moderate: list[Reason] = []
    if case_score >= HIGH_SCORE and recommended is not None and recommended.option == "B" and recommended.after is not None:
        moderate.append(Reason("M1", f"Score {round1(case_score):.1f} is HIGH and the recommended Option B delays "
                                     f"{recommended.after.so_delayed} sales order(s)"))
    if close_call:
        moderate.append(Reason("M2", f"A protecting option costs within {limits.close_call_band_pp} points of the "
                                     f"{limits.premium_max_pct}% premium policy"))
    if silent_sites:
        moderate.append(Reason("M3", f"No reply from {', '.join(sorted(silent_sites))}; impact is not confirmed"))
    if moderate:
        return Escalation(1, tuple(moderate), _ordered(PARTICIPANT_ORDER, base), ())
    return Escalation(0, (), (), ())
