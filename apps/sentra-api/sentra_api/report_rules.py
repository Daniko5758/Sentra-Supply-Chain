"""FR-15 report rules as pure functions: the chart series of EP-17 / TL-19, the file title, and the template narrative that
replaces an LLM text after the number validator failed twice (G-04). No DB, HTTP, clock, or environment access.

The series are computed from the stored snapshots (the impact payloads and the priority rows), never by the LLM. The template
uses only values of the report data, written in the R-20 formats, so the validator accepts it against that data.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sentra_api.priority import format_idr, round1

SECTIONS = ("executive_summary", "what_happened", "impact", "supplier_verification", "schedule_status",
            "recommended_plan", "escalation", "action_status", "appendix")
HEADINGS = {
    "executive_summary": "Executive summary", "what_happened": "What happened", "impact": "Impact",
    "supplier_verification": "Supplier verification", "schedule_status": "Schedule status",
    "recommended_plan": "Recommended plan", "escalation": "Escalation", "action_status": "Action status", "appendix": "Appendix: sources and assumptions",
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_NOT_EXECUTED = "Proposed actions — not executed"


def file_title(case_id: str, version: int) -> str:
    """Title of the printed report (FR-15: the name of the PDF file)."""
    return f"SENTRA_Report_{case_id}_v{version}"


# --- chart series (EP-17 `charts`) ---------------------------------------------------------------------------------------------

def rar_by_site(priority: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Revenue at risk of each candidate site (R-06, standalone), in the order of the priority rows."""
    return [{"site_id": row["site_id"], "rar": row["rar"]} for row in priority]


def delay_by_part(impact: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Days late per part (R-02); parts that are not late are not drawn."""
    return [{"part_id": item["part_id"], "days": item["days"]} for item in impact["part_delays"] if item["days"] > 0]


def units_by_order(impact: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Units blocked per production order (R-04, net of the shortage that exists without the event)."""
    return [{"order_id": order["order_id"], "units": order["units_blocked"]}
            for order in impact["production_orders"] if order["units_blocked"] > 0]


def rar_by_version(snapshots: Iterable[tuple[int, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Revenue at risk of every snapshot version, oldest first (before vs after a supplier answer or an execution)."""
    ordered = sorted(snapshots, key=lambda item: item[0])
    return [{"version": version, "rar": impact["totals"]["rar"]} for version, impact in ordered]


def build_charts(
    priority: Iterable[Mapping[str, Any]], impact: Mapping[str, Any], snapshots: Iterable[tuple[int, Mapping[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """The four series of FR-15. `impact` is the latest snapshot, `snapshots` are all versions of the case."""
    return {
        "rar_by_site": rar_by_site(priority), "delay_by_part": delay_by_part(impact),
        "units_by_order": units_by_order(impact), "rar_by_version": rar_by_version(snapshots),
    }


# --- template narrative ---------------------------------------------------------------------------------------------------------

def _date(value: str) -> str:
    """R-20: `2026-11-14` -> `14 Nov 2026`."""
    year, month, day = value.split("-")
    return f"{int(day):02d} {_MONTHS[int(month) - 1]} {year}"


def _score(value: float) -> str:
    return f"{round1(value):.1f}"


def _executive_summary(report: Mapping[str, Any], simulated: bool) -> list[str]:
    kpi, recommendation = report["kpi"], report["recommendation"]
    sentences = []
    if simulated:
        sentences.append("This case was opened by a simulated signal (SIMULATED).")
    if kpi.get("level") is not None and kpi.get("priority_score") is not None:
        sentences.append(f"The case is {kpi['level']} with a priority score of {_score(kpi['priority_score'])}.")
    sentences.append(
        f"{kpi['units_at_risk']} production units are at risk and revenue at risk is {format_idr(kpi['revenue_at_risk'])}.")
    if recommendation.get("preliminary"):
        sentences.append("The view is preliminary: a supplier confirmation is still outstanding.")
    if recommendation.get("rule_reason"):
        prefix = "Recommended by rule: " if recommendation.get("recommended_option") else ""
        sentences.append(f"{prefix}{recommendation['rule_reason']}.")
    return [" ".join(sentences)]


def _what_happened(report: Mapping[str, Any]) -> list[str]:
    signal = report["signal"]
    paragraphs = [signal["summary"]]
    if signal.get("severity") is not None:
        paragraphs.append(f"Severity {signal['severity']}.")
    if signal.get("source_urls"):
        paragraphs.append("Sources:\n" + "\n".join(f"- {url}" for url in signal["source_urls"]))
    elif signal.get("source_label"):
        paragraphs.append(f"Source: {signal['source_label']}.")
    else:
        paragraphs.append("No source is recorded for this signal.")
    return paragraphs


def _tariff_line(tariff: Mapping[str, Any]) -> str:
    line = f"- Tariff on {tariff.get('category') or 'goods'} from {tariff['country']}"
    stated = []
    if tariff.get("tariff_pp") is not None:
        stated.append(f"{tariff['tariff_pp']:g} percentage points")
    if tariff.get("effective_date"):
        stated.append(f"effective {_date(tariff['effective_date'])}")
    if stated:
        line += ": " + ", ".join(stated)
    if tariff.get("extra_duty") is not None:
        line += f"; extra duty about {format_idr(tariff['extra_duty'])}"
    names = {"tariff_pp": "tariff rate", "effective_date": "effective date"}
    missing = [names[name] for name in tariff.get("not_stated") or [] if name in names]
    if missing:
        line += f"; the {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not stated in the source"
    return line + "."


def _impact(report: Mapping[str, Any]) -> list[str]:
    kpi, impact = report["kpi"], report["impact"]
    lines = [
        f"- Units at risk: {kpi['units_at_risk']}",
        f"- Delayed output value: {format_idr(kpi['delayed_output_value'])}",
        f"- Affected PO value: {format_idr(kpi['affected_po_value'])}",
        f"- Revenue at risk: {format_idr(kpi['revenue_at_risk'])}",
    ]
    delays = delay_by_part(impact)
    if delays:
        lines.append("- Delayed parts: " + ", ".join(f"{d['part_id']} ({d['days']} days)" for d in delays))
    orders = units_by_order(impact)
    if orders:
        lines.append("- Blocked production orders: " + ", ".join(f"{o['order_id']} ({o['units']} units)" for o in orders))
    for order in impact.get("sales_orders") or []:
        moved = _date(order["new_date"]) if order.get("new_date") else "no date within the horizon"
        lines.append(f"- Sales order {order['so_id']} ({order['customer']}): {_date(order['old_date'])} -> {moved}, "
                     f"{order['units_at_risk']} units at risk")
    if impact.get("tariff"):
        lines.append(_tariff_line(impact["tariff"]))
    if impact.get("exposure"):
        exposure = impact["exposure"]
        lines.append(f"- Open PO exposure in {exposure['country']}: {format_idr(exposure['po_value'])}. {exposure['note']}.")
    return ["\n".join(lines)]


def _site_line(site: Mapping[str, Any]) -> str:
    status = site["verification_status"]
    if status == "CONFIRMED_BY_SUPPLIER":
        if site.get("excluded"):
            text = "confirmed by the supplier: no impact on this site"
        elif site.get("delay_override_days") is not None:
            text = f"confirmed by the supplier, expected delay {site['delay_override_days']} days"
        else:
            text = "confirmed by the supplier"
    elif status == "REQUESTED":
        text = "waiting for the supplier's answer"
    else:
        text = "confirmation not needed"
    scores = [f"{name} {_score(site[key])}" for name, key in (("K_A", "k_a"), ("K_B", "k_b")) if site.get(key) is not None]
    if scores:
        text += "; " + ", ".join(scores)
        if site.get("rpt_status") == "UNAVAILABLE":
            text += " (prediction model unavailable, formula used)"
    if (site.get("reply") or {}).get("simulated"):
        text += " (SIMULATED reply)"
    return f"- {site['site_id']} {site['supplier_name']} (tier {site['tier']}): {text}."


def _supplier_verification(report: Mapping[str, Any]) -> list[str]:
    sites = report.get("verification") or []
    paragraphs = ["\n".join(_site_line(site) for site in sites)] if sites else ["No supplier confirmation was needed or requested."]
    versions = report["charts"]["rar_by_version"]
    if len(versions) > 1:
        paragraphs.append("Revenue at risk by snapshot: "
                          + "; ".join(f"version {p['version']}: {format_idr(p['rar'])}" for p in versions) + ".")
    return paragraphs


def _schedule_status(report: Mapping[str, Any]) -> list[str]:
    schedule = report["schedule"]
    return [f"- PO lines: {schedule['po_lines']}; {schedule['slipped']} slipped and {schedule['on_time']} on time\n"
            f"- Production orders: {schedule['orders_covered']} covered; {schedule['orders_short']} short of parts"]


def _option_line(option: Mapping[str, Any], recommended: bool) -> str:
    head = f"- Option {option['option']} — {option['title']}"
    if not option["available"] or option.get("after") is None:
        return f"{head}: not available ({option.get('unavailable_reason')})."
    before, after = option["before"], option["after"]
    line = (f"{head}: units blocked {before['units_blocked']} -> {after['units_blocked']}; "
            f"revenue at risk {format_idr(before['rar'])} -> {format_idr(after['rar'])}; "
            f"extra cost {format_idr(after['premium'])}.")
    if option.get("label"):
        line += f" {option['label']}."
    if recommended:
        line += " Recommended by rule."
    if option.get("rationale"):
        line += f" {option['rationale']}"
    return line


def _recommended_plan(report: Mapping[str, Any]) -> list[str]:
    recommendation = report["recommendation"]
    options = recommendation.get("options") or []
    paragraphs = []
    if options:
        paragraphs.append("\n".join(_option_line(o, o["option"] == recommendation.get("recommended_option")) for o in options))
    if recommendation.get("rule_reason"):
        paragraphs.append(f"Rule applied: {recommendation['rule_reason']}.")
    if recommendation.get("preliminary"):
        paragraphs.append("This recommendation is preliminary: a supplier confirmation is still outstanding.")
    if recommendation.get("non_erp_steps"):
        paragraphs.append("Steps outside the ERP:\n" + "\n".join(f"- {step}" for step in recommendation["non_erp_steps"]))
    return paragraphs


_TIER_NAMES = {0: "in-app (no meeting)", 1: "Meeting Room", 2: "Executive Meeting Room"}


def _escalation(report: Mapping[str, Any]) -> list[str]:
    """R-22 as the code decided it: tier, reasons, approvers, the meeting and the decision note. Only values of the data."""
    escalation = report.get("escalation")
    if not escalation:
        return ["No escalation data is available."]
    paragraphs = [f"Escalation tier {escalation['tier']}: {_TIER_NAMES[escalation['tier']]}."]
    if escalation["reasons"]:
        paragraphs.append("Reasons:\n" + "\n".join(f"- {r['code']}: {r['text']}" for r in escalation["reasons"]))
    if escalation["required_approvers"]:
        paragraphs.append("Required approvers: " + ", ".join(escalation["required_approvers"]) + ".")
    meeting = escalation.get("meeting")
    if meeting is not None:
        scheduled = "scheduled" if meeting["status"] == "SCHEDULED" else "could not be scheduled"
        paragraphs.append(f"The {_TIER_NAMES[meeting['tier']]} {scheduled} ({meeting['provider']}).")
    decision = escalation.get("decision")
    if decision is not None:
        chosen = f"Option {decision['option']}" if decision.get("option") else "no option"
        budget = "; budget approved" if decision.get("budget_approved") else ""
        paragraphs.append(f"Meeting decision: {chosen}, approved by {', '.join(decision['approved_by']) or 'nobody'}{budget}.")
    elif escalation.get("decision_required"):
        paragraphs.append("A meeting decision note is required before an option can be submitted.")
    return paragraphs


def _action_line(action: Mapping[str, Any]) -> str:
    line = f"- {action['action_id']} {action['type']}: {action['status']}"
    detail = (action.get("erp_result") or {}).get("document") or action.get("error")
    return f"{line} ({detail})" if detail else line


def _action_status(report: Mapping[str, Any]) -> list[str]:
    actions = report.get("actions") or []
    if not actions:
        return [f"{_NOT_EXECUTED}. The options above are proposals; nothing has been changed in the ERP."]
    return ["Actions in the ERP (Mock SAP S/4HANA — SIMULATED):\n" + "\n".join(_action_line(a) for a in actions)]


def _appendix(report: Mapping[str, Any], simulated: bool) -> list[str]:
    lines = [
        f"- The figures come from impact snapshot version {report['version']}, calculated by SENTRA rules; "
        "a language model did not calculate them.",
        "- Revenue at risk is the delayed output value plus the affected PO value, "
        "net of shortages that exist without the event.",
        "- ERP data: Mock SAP S/4HANA — SIMULATED.",
    ]
    if simulated:
        lines.append("- The signal and the supplier replies of this case are SIMULATED.")
    lines.append("- This text is a template: the generated text did not pass the number check, "
                 "so only figures of the data are used.")
    return ["\n".join(lines)]


def template_narrative(report: Mapping[str, Any], *, simulated: bool) -> str:
    """The report text written by rule from `report` (the TL-19 data, with its `version`). It states only values of that data
    in the R-20 formats, so the validator of G-04 accepts it against the same data. `simulated`: a DEMO signal opened the case."""
    blocks: dict[str, Sequence[str]] = {
        "executive_summary": _executive_summary(report, simulated),
        "what_happened": _what_happened(report),
        "impact": _impact(report),
        "supplier_verification": _supplier_verification(report),
        "schedule_status": _schedule_status(report),
        "recommended_plan": _recommended_plan(report),
        "escalation": _escalation(report),
        "action_status": _action_status(report),
        "appendix": _appendix(report, simulated),
    }
    return "\n\n".join(f"## {HEADINGS[name]}\n\n" + "\n\n".join(blocks[name]) for name in SECTIONS)
