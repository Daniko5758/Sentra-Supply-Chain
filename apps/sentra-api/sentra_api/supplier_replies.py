"""R-12 v2 / R-13 v2 as pure functions: the text of the three kinds of request, the scripted answer, what an answer does to a case
site.

No DB, HTTP, clock or environment. `services/supplier_service.py` loads the data and stores the result.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sentra_api.signals import normalize_place

SIMULATED = "SIMULATED"
ANSWERS = ("YES", "PARTIAL", "NO")  # "Affected? (YES / PARTIAL / NO)"
CONFIRMED = "CONFIRMED_BY_SUPPLIER"  # verification_status after an answer (R-13)
TTR_DAYS = "ttr_days"  # a scripted `delay_days` that means "the recovery time of the site itself" (default_live)
BACKUP_RAMP_DAYS = "backup_ramp_days"  # a scripted `ramp_days` that means "the ramp-up time in the master data of the site"
REQUEST_TYPES = ("IMPACT", "BACKUP_ACTIVATION", "PULL_IN")
OPTION_ANSWERS = ("YES", "NO")  # "(YES / NO)"
SCRIPT_KEY = {"BACKUP_ACTIVATION": "backup_activation", "PULL_IN": "pull_in"}  # the part of a script that answers the kind
MAX_PO_LINES_LISTED = 12
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ReplyScriptError(ValueError):
    """A scripted answer in the seed is not usable (unknown `affected`, bad `delay_days`)."""


@dataclass(frozen=True)
class PoLineFact:
    po_id: str
    item_no: int
    material_id: str
    qty: int
    confirmed_date: date


@dataclass(frozen=True)
class RequestFacts:
    """What the outgoing message says. `po_lines` for a tier-1 site, `components` for a tier-2 site (IMPACT); the backup site and
    its ramp-up days for BACKUP_ACTIVATION; the lines to ship and the day before the effective date for PULL_IN."""

    case_title: str
    event_summary: str
    source: str
    supplier_name: str
    site_id: str
    tier: str
    po_lines: tuple[PoLineFact, ...] = ()
    components: tuple[str, ...] = ()
    request_type: str = "IMPACT"
    backup_site: str | None = None
    backup_ramp_days: int | None = None
    pull_in_lines: tuple[str, ...] = ()  # "4500001009/10"
    pull_in_date: date | None = None


def _day(value: date) -> str:
    return f"{value.day:02d} {MONTHS[value.month - 1]} {value.year}"


def request_body(facts: RequestFacts) -> str:
    """The outgoing message (English): event, source and the questions of its kind (R-12 v2)."""
    lines = [
        f"{facts.case_title} ({SIMULATED})",
        "",
        f"To: {facts.supplier_name} ({facts.site_id})",
        f"Event: {facts.event_summary}",
        f"Source: {facts.source}",
    ]
    if facts.request_type == "BACKUP_ACTIVATION":
        backup = f" {facts.backup_site}" if facts.backup_site else ""
        lines += ["", "Please answer:",
                  f"1. Can you move production to your backup site{backup} and start shipping within "
                  f"{facts.backup_ramp_days} days? (YES / NO)",
                  "2. Ramp-up days", "3. Notes"]
    elif facts.request_type == "PULL_IN":
        lines += ["", "Please answer:",
                  f"1. Can you ship PO {', '.join(facts.pull_in_lines)} by {_day(facts.pull_in_date)}? (YES / NO)",
                  "2. Notes"]
    else:
        lines += _impact_questions(facts)
    return "\n".join(lines)


def _impact_questions(facts: RequestFacts) -> list[str]:
    """IMPACT: the PO lines (tier 1) or components (tier 2) of the site, then the three questions of R-12 v1.0."""
    lines: list[str] = []
    if facts.tier == "1" and facts.po_lines:
        lines += ["", "Your open purchase order lines:"]
        lines += [f"- {p.po_id}/{p.item_no}: {p.material_id} x {p.qty}, confirmed for {_day(p.confirmed_date)}"
                  for p in facts.po_lines[:MAX_PO_LINES_LISTED]]
        if len(facts.po_lines) > MAX_PO_LINES_LISTED:
            lines.append(f"- and {len(facts.po_lines) - MAX_PO_LINES_LISTED} more lines")
    elif facts.components:
        lines += ["", f"Components you supply: {', '.join(facts.components)}"]
    lines += [
        "",
        "Please answer three questions:",
        "1. Affected? (YES / PARTIAL / NO)",
        "2. Expected delay (days)",
        "3. Notes",
    ]
    return lines


@dataclass(frozen=True)
class SupplierReply:
    affected: str
    delay_days: int | None
    notes: str

    def structured(self) -> dict[str, Any]:
        """`structured` of the incoming message (EP-14)."""
        return {"affected": self.affected, "delay_days": self.delay_days, "notes": self.notes}

    def body(self) -> str:
        if self.notes:
            return self.notes
        delay = "" if self.delay_days is None else f"; expected delay {self.delay_days} days"
        return f"Affected: {self.affected}{delay}."


def resolve_reply(script: Mapping[str, Any], ttr_days: int) -> SupplierReply:
    """A scripted answer of the seed (`scripted_replies`): `delay_days` may be the word `ttr_days`."""
    affected = script.get("affected")
    if affected not in ANSWERS:
        raise ReplyScriptError(f"affected must be one of {list(ANSWERS)}")
    delay = script.get("delay_days")
    if delay == TTR_DAYS:
        delay = ttr_days
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, int) or delay < 0):
        raise ReplyScriptError("delay_days must be a whole number of days >= 0 or 'ttr_days'")
    notes = script.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ReplyScriptError("notes must be text")
    return SupplierReply(affected=affected, delay_days=delay, notes=(notes or "").strip())


@dataclass(frozen=True)
class AppliedReply:
    """R-13: the new state of the case site."""

    delay_override_days: int | None
    excluded: bool
    verification_status: str


def apply_reply(reply: SupplierReply) -> AppliedReply:
    """YES / PARTIAL -> `delay_override_days = delay_days`; NO -> the site is `excluded`; always CONFIRMED_BY_SUPPLIER."""
    if reply.affected == "NO":
        return AppliedReply(delay_override_days=None, excluded=True, verification_status=CONFIRMED)
    return AppliedReply(delay_override_days=reply.delay_days, excluded=False, verification_status=CONFIRMED)


@dataclass(frozen=True)
class OptionReply:
    """The answer to a BACKUP_ACTIVATION or PULL_IN question (R-12 v2). `ramp_days` only for a BACKUP_ACTIVATION that says YES."""

    request_type: str
    answer: str
    ramp_days: int | None
    notes: str

    def structured(self) -> dict[str, Any]:
        """`structured` of the incoming message (EP-14)."""
        if self.request_type == "BACKUP_ACTIVATION":
            return {"answer": self.answer, "ramp_days": self.ramp_days, "notes": self.notes}
        return {"answer": self.answer, "notes": self.notes}

    def body(self) -> str:
        if self.notes:
            return self.notes
        yes = self.answer == "YES"
        if self.request_type == "BACKUP_ACTIVATION":
            return (f"Yes. Production can ship from the backup site in {self.ramp_days} days." if yes
                    else "No. The backup site cannot take the production.")
        return "Yes. We can ship by the earlier date." if yes else "No. We cannot ship by the earlier date."


def resolve_option_reply(request_type: str, script: Mapping[str, Any] | None, backup_ramp_days: int | None) -> OptionReply:
    """A scripted answer to BACKUP_ACTIVATION or PULL_IN; `ramp_days` may be the word `backup_ramp_days` (the master data)."""
    if not isinstance(script, Mapping):
        raise ReplyScriptError(f"no scripted answer for {request_type}")
    answer = script.get("answer")
    if answer not in OPTION_ANSWERS:
        raise ReplyScriptError(f"answer must be one of {list(OPTION_ANSWERS)}")
    notes = script.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ReplyScriptError("notes must be text")
    ramp = None
    if request_type == "BACKUP_ACTIVATION" and answer == "YES":
        ramp = script.get("ramp_days")
        if ramp == BACKUP_RAMP_DAYS:
            ramp = backup_ramp_days
        if isinstance(ramp, bool) or not isinstance(ramp, int) or ramp < 0:
            raise ReplyScriptError("ramp_days must be a whole number of days >= 0 or 'backup_ramp_days'")
    return OptionReply(request_type, answer, ramp, (notes or "").strip())


@dataclass(frozen=True)
class AppliedOptionReply:
    """R-13 v2: what an answer does to the case site."""

    backup_ramp_confirmed: int | None
    backup_declined: bool


def apply_option_reply(reply: OptionReply) -> AppliedOptionReply:
    """BACKUP_ACTIVATION YES -> the confirmed ramp (R-02 v2); NO -> option C is off for the site. PULL_IN: nothing on the site; the
    answer is read from the request when the options are built (R-14 v2)."""
    if reply.request_type != "BACKUP_ACTIVATION":
        return AppliedOptionReply(None, False)
    if reply.answer == "YES":
        return AppliedOptionReply(reply.ramp_days, False)
    return AppliedOptionReply(None, True)


def pick_script_v2(
    scripts: Mapping[str, Any], scenario: str | None, site_id: str, request_type: str, from_demo: bool
) -> Mapping[str, Any] | None:
    """The scripted answer for a site and kind of request, or None when the site stays silent (`no_reply`, every kind).

    A case that came from a DEMO signal uses the script of its scenario for the site, if there is one; every other case (a LIVE
    signal, a custom DEMO signal, a site the scenario does not script) gets `default_live`. IMPACT takes the script when it
    answers that question (`affected`), the other kinds take their own part of the script; a part that is missing is
    `default_live`'s (R-12 v2).
    """
    default = scripts["default_live"]
    script = None
    if from_demo and scenario is not None:
        scripted = scripts.get(scenario)
        if isinstance(scripted, Mapping) and isinstance(scripted.get(site_id), Mapping):
            script = scripted[site_id]
    if script is not None and script.get("no_reply") is True:
        return None
    if request_type == "IMPACT":
        return script if script is not None and "affected" in script else default
    key = SCRIPT_KEY[request_type]
    own = script.get(key) if script is not None else None
    return own if isinstance(own, Mapping) else default.get(key)


def scenario_of(events: Mapping[str, Mapping[str, Any]], signal: Mapping[str, Any]) -> str | None:
    """The name of the demo event the signal was made from (None for a free-form signal). A signal and an event match when
    risk type, scope, country, place and summary are equal."""
    for name, event in events.items():
        if (event.get("risk_type") == signal["risk_type"] and event.get("scope") == signal["scope"]
                and event.get("country") == signal["country"] and event.get("summary") == signal["summary"]
                and normalize_place(event.get("kabupaten")) == normalize_place(signal["kabupaten"])):
            return name
    return None
