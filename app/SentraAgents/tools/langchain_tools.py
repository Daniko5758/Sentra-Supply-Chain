"""``@tool`` LangChain untuk agen ReAct (Chat dan Recommendation), dibangun di atas ``tools/client.py``.

Daftar tool dibatasi per agen (``CLAUDE.md`` bagian 2 butir 6, G-01):

- Chat (PRD FR-16, A-13): tool baca saja ``get_case``, ``get_po_schedule``, ``get_production_schedule``,
  ``get_supplier_network``, ``get_supplier_messages``, ``compute_impact`` (selalu ``persist=false``),
  ``simulate_plan``, ``get_country_heatmap``, ``get_report_data``, ``get_escalation`` (TL-28, v1.11), dan ``run_what_if`` hanya
  bila F-10 aktif.
- Recommendation (A-11): hanya ``simulate_plan``.

Tool tulis (``save_*``, ``score_case``, ``log_trace``, ...) sengaja TIDAK dibungkus: dipanggil oleh node graph,
bukan oleh LLM. Setiap tool mengembalikan JSON (string) apa adanya dari ``sentra-api``; angka yang dipakai LLM
berasal dari sana (G-04). Teks deskripsi tool ditulis dalam English karena dibaca model.

Error: ``VALIDATION_FAILED`` / ``NOT_FOUND`` / ``FEATURE_DISABLED`` / ``CONFLICT`` dikembalikan sebagai
``{"error": {...}}`` agar model bisa memperbaiki panggilannya; error lain (401, 5xx, jaringan, timeout)
dilempar supaya run berakhir ``ERROR`` dan tidak dikarang ulang oleh model.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from tools.client import Json, ToolClient, ToolError

CHAT_TOOL_NAMES = (
    "get_case", "get_po_schedule", "get_production_schedule", "get_supplier_network", "get_supplier_messages",
    "compute_impact", "simulate_plan", "get_country_heatmap", "get_report_data", "get_escalation",
)
CHAT_WHATIF_TOOL = "run_what_if"  # hanya bila F-10 aktif
RECOMMENDATION_TOOL_NAMES = ("simulate_plan",)

# Error yang berguna bagi model (ia bisa memperbaiki argumen atau menjelaskan ke pengguna).
RECOVERABLE_CODES = frozenset({"VALIDATION_FAILED", "NOT_FOUND", "FEATURE_DISABLED", "CONFLICT"})


class PlanAction(BaseModel):
    """Aksi rencana (G.3) tanpa ``action_id``; dipakai ``simulate_plan``."""

    seq: int | None = Field(None, description="Order of the action within the plan, starting at 1.")
    type: Literal["CHANGE_PO_DATE", "CREATE_PR_PO", "REDUCE_OR_CANCEL_PO", "RESCHEDULE_PRODUCTION_ORDER",
                  "RESCHEDULE_SALES_ORDER"]
    object: str | None = Field(None, description="Human-readable target, e.g. '4500001001/10' or 'BAT-48 @ S12'.")
    payload: dict[str, Any] = Field(
        description="CHANGE_PO_DATE: po_id,item_no,old_date,new_date. CREATE_PR_PO: material_id,supplier_id,qty,"
                    "price,delivery_date. REDUCE_OR_CANCEL_PO: po_id,item_no,old_qty,new_qty. "
                    "RESCHEDULE_PRODUCTION_ORDER: order_id,old_date,new_date. "
                    "RESCHEDULE_SALES_ORDER: so_id,old_date,new_date. Dates are ISO YYYY-MM-DD."
    )


def _dump(result: Json) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _run(call: Callable[[], Json]) -> str:
    try:
        return _dump(call())
    except ToolError as exc:
        if exc.code in RECOVERABLE_CODES:
            return _dump({"error": exc.as_dict()})
        raise


def _all_tools(client: ToolClient) -> dict[str, BaseTool]:
    @tool
    def get_case(case_id: str, version: int | None = None) -> str:
        """Get a case: signal, KPIs (units at risk, revenue at risk, score, level), impact, candidate sites with
        confidence and verification status, and the priority ranking. Omit version for the latest snapshot."""
        return _run(lambda: client.get_case(case_id, version))

    @tool
    def get_po_schedule() -> str:
        """Get the purchase-order schedule: totals and PO lines with requested vs confirmed dates and slip days."""
        return _run(client.get_po_schedule)

    @tool
    def get_production_schedule() -> str:
        """Get production orders, bill of materials, stock and sales orders."""
        return _run(client.get_production_schedule)

    @tool
    def get_supplier_network(case_id: str | None = None) -> str:
        """Get the supplier network graph (sites, parts, products, edges). With case_id, nodes affected by that
        case are highlighted."""
        return _run(lambda: client.get_supplier_network(case_id))

    @tool
    def get_supplier_messages(case_id: str, site_id: str | None = None) -> str:
        """Get the (simulated) supplier messages of a case and the supplier replies already applied to the
        impact calculation. Optionally filter by site_id."""
        return _run(lambda: client.get_supplier_messages(case_id, site_id))

    @tool
    def compute_impact(case_id: str) -> str:
        """Compute the current impact of a case (delayed parts, PO lines, blocked production orders, sales
        orders at risk, totals). Read-only: nothing is saved."""
        return _run(lambda: client.compute_impact(case_id, persist=False))

    @tool
    def simulate_plan(case_id: str, actions: list[PlanAction]) -> str:
        """Simulate a list of actions for a case and return metrics before and after plus the resulting
        impact. Read-only: nothing is changed in the ERP or saved."""
        return _run(lambda: client.simulate_plan(case_id, [a.model_dump(exclude_none=True) for a in actions]))

    @tool
    def get_country_heatmap() -> str:
        """Get PO exposure and the highest risk level per country."""
        return _run(client.get_country_heatmap)

    @tool
    def get_report_data(case_id: str) -> str:
        """Get the data behind the case report: KPIs, impact, verification, schedule, recommendation, actions
        and chart series."""
        return _run(lambda: client.get_report_data(case_id))

    @tool
    def get_escalation(case_id: str) -> str:
        """Get the escalation of a case: tier (0 handled in the app, 1 Meeting Room, 2 Executive Meeting Room), the reasons,
        participants, required approvers, and the latest meeting and decision note if any. Read-only."""
        return _run(lambda: client.get_escalation(case_id))

    @tool
    def run_what_if(location: str) -> str:
        """What-if: compute the impact if a location (e.g. a kabupaten or city) were disrupted. Creates no case,
        sends no email and changes nothing in the ERP."""
        return _run(lambda: client.run_what_if(location))

    tools = (get_case, get_po_schedule, get_production_schedule, get_supplier_network, get_supplier_messages,
             compute_impact, simulate_plan, get_country_heatmap, get_report_data, get_escalation, run_what_if)
    return {t.name: t for t in tools}


def build_chat_tools(client: ToolClient, *, whatif_enabled: bool = False) -> list[BaseTool]:
    """Tool baca untuk Chat Agent (FR-16); ``run_what_if`` hanya bila F-10 aktif."""
    tools = _all_tools(client)
    names = CHAT_TOOL_NAMES + ((CHAT_WHATIF_TOOL,) if whatif_enabled else ())
    return [tools[name] for name in names]


def build_recommendation_tools(client: ToolClient) -> list[BaseTool]:
    """Tool untuk Recommendation Agent: ``simulate_plan`` saja."""
    tools = _all_tools(client)
    return [tools[name] for name in RECOMMENDATION_TOOL_NAMES]
