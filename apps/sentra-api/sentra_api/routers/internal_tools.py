"""POST /internal/tools/{tool_name}: the tools of the agents (API_CONTRACT C), guarded by X-Sentra-Service-Token.

Agents have no tool that writes to the ERP (G-01): there is none here. TL-13 (F-07) creates the Zoom meeting and sends the e-mail
since B-45; TL-18 (F-10) shares the read-only what-if service with EP-25 since B-33.
"""

import hmac
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from sentra_api.db import utc_now
from sentra_api.errors import ApiError
from sentra_api.flags import flag_enabled
from sentra_api.number_validator import check_numbers
from sentra_api.recommendation_rules import ACTION_TYPES
from sentra_api.services import (
    case_tools, confidence_service, meeting_service, recommendation_service, report_service, schedule_service,
    supplier_service, whatif_service,
)
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.heatmap_service import country_heatmap
from sentra_api.services.network import build_network
from sentra_api.services.signal_service import ingest_signals

TOKEN_HEADER = "X-Sentra-Service-Token"

router = APIRouter(prefix="/internal/tools", tags=["internal-tools"], include_in_schema=False)


# --- request bodies (strict types; every tool also accepts the optional `run_id`) ----------------------------------

class Body(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    run_id: str | None = None


class Empty(Body):
    pass


class CaseBody(Body):
    case_id: str


class NetworkBody(Body):
    case_id: str | None = None


class SaveSignalsBody(Body):
    mode: Literal["LIVE", "DEMO"] | None = None  # the origin of each signal follows its engine (DEC-024)
    signals: list[Any]


class ComputeImpactBody(Body):
    case_id: str
    persist: bool = False
    trigger: Literal["initial", "supplier_reply", "erp_execution"] | None = None


class RequestConfirmationBody(Body):
    case_id: str
    site_ids: list[str] = Field(min_length=1)
    request_type: Literal["IMPACT", "BACKUP_ACTIVATION", "PULL_IN"] = "IMPACT"  # v1.11 (DEC-038)


class NotifyMeetingBody(Body):
    case_id: str
    agenda: str | None = None  # v1.11: the agenda the agent wrote (<= 1.500 characters, G-04), else the R-22 template


class SupplierMessagesBody(Body):
    case_id: str
    site_id: str | None = None


class AlternateSourcesBody(Body):
    part_ids: list[str]


class SimulatePlanBody(Body):
    case_id: str
    actions: list[dict[str, Any]]


class SaveRecommendationBody(Body):
    case_id: str
    version: int
    rationales: dict[str, str]


class SaveReportBody(Body):
    case_id: str
    version: int
    narrative_md: str


class GetCaseBody(Body):
    case_id: str
    version: int | None = None


class LogTraceBody(Body):
    trace_id: str | None = None
    case_id: str | None = None
    task: str | None = None
    step: str
    tool: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    decision: str | None = None
    duration_ms: int | None = None
    status: Literal["RUNNING", "OK", "ERROR", "SKIPPED", "AGENT_TIMEOUT", "LLM_OVERRIDDEN"]


class ValidateNumbersBody(Body):
    text: str
    allowed: Any


class LocationBody(Body):
    location: str


@dataclass
class Call:
    """One tool call: the request, a DB session (committed when the tool succeeds) and the ERP read, loaded on demand."""

    request: Request
    session: Session
    body: Any
    _erp: Any = None

    @property
    def d0(self):
        return self.request.app.state.d0_date

    async def erp(self):
        if self._erp is None:
            self._erp = await load_erp_data(self.request.app)
        return self._erp


Handler = Callable[[Call], Awaitable[dict[str, Any]]]


# --- tools ------------------------------------------------------------------------------------------------------

async def get_po_schedule(call: Call) -> dict[str, Any]:  # TL-01
    return schedule_service.po_schedule(call.session, await call.erp(), call.d0)


async def get_production_schedule(call: Call) -> dict[str, Any]:  # TL-02
    return schedule_service.production_schedule(await call.erp(), call.d0)


async def save_signals(call: Call) -> dict[str, Any]:  # TL-05
    if not all(isinstance(item, dict) for item in call.body.signals):
        raise ApiError(400, "VALIDATION_FAILED", "Every signal must be an object.", {"field": "signals"})
    now = utc_now()
    pool = call.request.app.state.session_pool
    result = ingest_signals(call.session, await call.erp(), call.body.signals, d0=call.d0, now=now,
                            new_session_id=lambda: pool.take("new case"))
    # `analyze_case` is queued by agent_runner (B-13) from `data.cases` of the finished scan run (contract TL-05, D.3),
    # not here: the scan run is still in progress while TL-05 is called.
    return result.to_tl05()


async def get_supplier_network(call: Call) -> dict[str, Any]:  # TL-06
    return build_network(call.session, await call.erp(), call.body.case_id)


async def map_event_to_sites(call: Call) -> dict[str, Any]:  # TL-07
    return case_tools.map_event_to_sites(call.session, call.body.case_id)


async def compute_impact(call: Call) -> dict[str, Any]:  # TL-08
    body = call.body
    return case_tools.compute_impact(
        call.session, await call.erp(), body.case_id, call.d0, persist=body.persist, trigger=body.trigger, now=utc_now()
    )


async def score_case(call: Call) -> dict[str, Any]:  # TL-09
    return case_tools.score_case(call.session, call.body.case_id, run_id=call.body.run_id, now=utc_now())


async def assess_confidence(call: Call) -> dict[str, Any]:  # TL-10
    case = case_tools.load_case(call.session, call.body.case_id)
    if case.scope != "LOCATION":
        return {"sites": [], "skipped": True}
    return await confidence_service.assess_case(
        call.session, await call.erp(), case, call.request.app.state.rpt_gateway, utc_now()
    )


async def request_supplier_confirmation(call: Call) -> dict[str, Any]:  # TL-11
    return supplier_service.request_confirmation(
        call.session, await call.erp(), call.body.case_id, call.body.site_ids, request_type=call.body.request_type,
        reply_delay_s=call.request.app.state.settings.sim_reply_delay_s, now=utc_now(), d0=call.d0,
    )


async def get_supplier_messages(call: Call) -> dict[str, Any]:  # TL-12
    return case_tools.supplier_messages(call.session, call.body.case_id, call.body.site_id)


async def notify_meeting_room(call: Call) -> dict[str, Any]:  # TL-13 v2 [F-07]
    case_tools.load_case(call.session, call.body.case_id)
    settings = call.request.app.state.settings
    if not flag_enabled(settings, "FEATURE_MEETING_ROOM"):
        # Not a 404: the agent graph stays simple (contract TL-13). The tier is still reported.
        has_snapshot = case_tools.latest_snapshot(call.session, call.body.case_id) is not None
        tier = 0
        if has_snapshot:
            tier = recommendation_service.build_recommendation(
                call.session, await call.erp(), call.body.case_id, call.d0, settings=settings).escalation["tier"]
        return {"status": "SKIPPED_FEATURE_OFF", "tier": tier, "meeting": None, "notification": None, "agenda_validator": None}
    built = recommendation_service.build_recommendation(call.session, await call.erp(), call.body.case_id, call.d0, settings=settings)
    return await meeting_service.notify(call.request.app, call.session, built, agenda=call.body.agenda, now=utc_now())


async def list_alternate_sources(call: Call) -> dict[str, Any]:  # TL-14
    return case_tools.alternate_sources(call.session, call.body.part_ids)


async def simulate_plan(call: Call) -> dict[str, Any]:  # TL-15
    actions = call.body.actions
    if not actions:
        raise ApiError(400, "VALIDATION_FAILED", "actions must not be empty.", {"field": "actions"})
    for index, action in enumerate(actions):
        if action.get("type") not in ACTION_TYPES:
            raise ApiError(400, "VALIDATION_FAILED", f"actions[{index}].type must be one of {list(ACTION_TYPES)}",
                           {"field": f"actions[{index}].type"})
        if not isinstance(action.get("payload"), dict):
            raise ApiError(400, "VALIDATION_FAILED", f"actions[{index}].payload must be an object",
                           {"field": f"actions[{index}].payload"})
    return case_tools.simulate(call.session, await call.erp(), call.body.case_id, actions, call.d0)


async def save_recommendation(call: Call) -> dict[str, Any]:  # TL-16
    body = call.body
    return recommendation_service.save_rationales(
        call.session, await call.erp(), body.case_id, body.version, body.rationales, call.d0,
        attempts=call.request.app.state.validation_attempts, run_id=body.run_id, settings=call.request.app.state.settings,
    )


async def get_case(call: Call) -> dict[str, Any]:  # TL-17
    detail = recommendation_service.detail_with_escalation(
        call.session, await call.erp(), call.body.case_id, call.body.version, call.d0, call.request.app.state.settings)
    return detail.model_dump()


async def get_report_data(call: Call) -> dict[str, Any]:  # TL-19
    data = report_service.build_report_data(
        call.session, await call.erp(), call.body.case_id, call.d0,
        execution_enabled=flag_enabled(call.request.app.state.settings, "FEATURE_ERP_EXECUTION"),
        settings=call.request.app.state.settings,
    )
    return data.tool_view()


async def save_report(call: Call) -> dict[str, Any]:  # TL-20
    body = call.body
    return report_service.save_narrative(
        call.session, await call.erp(), body.case_id, body.version, body.narrative_md, call.d0,
        attempts=call.request.app.state.validation_attempts, run_id=body.run_id,
        execution_enabled=flag_enabled(call.request.app.state.settings, "FEATURE_ERP_EXECUTION"), now=utc_now(),
        settings=call.request.app.state.settings,
    )


async def run_what_if(call: Call) -> dict[str, Any]:  # TL-18 [F-10]
    if not flag_enabled(call.request.app.state.settings, "FEATURE_WHATIF"):
        raise ApiError(404, "FEATURE_DISABLED", "This feature is disabled.", {"flag": "FEATURE_WHATIF"})
    return whatif_service.run_what_if(call.session, await call.erp(), call.body.location,
                                     whatif_service.simulation_d0(call.request.app))


async def log_trace(call: Call) -> dict[str, Any]:  # TL-21
    fields = call.body.model_dump(exclude={"run_id", "trace_id"})
    fields["run_id"] = call.body.run_id
    return {"trace_id": case_tools.log_trace(call.session, fields, trace_id=call.body.trace_id, now=utc_now())}


async def get_country_heatmap(call: Call) -> dict[str, Any]:  # TL-22
    return country_heatmap(call.session, await call.erp(), call.d0)


async def get_recommendation_options(call: Call) -> dict[str, Any]:  # TL-23
    built = recommendation_service.build_recommendation(
        call.session, await call.erp(), call.body.case_id, call.d0, settings=call.request.app.state.settings)
    return built.tool_view()


async def get_escalation(call: Call) -> dict[str, Any]:  # TL-28
    built = recommendation_service.build_recommendation(
        call.session, await call.erp(), call.body.case_id, call.d0, settings=call.request.app.state.settings)
    return built.escalation


async def validate_numbers(call: Call) -> dict[str, Any]:  # TL-24
    offending = check_numbers(call.body.text, call.body.allowed)
    return {"passed": not offending, "offending_numbers": offending}


@dataclass(frozen=True)
class Tool:
    body: type[Body]
    handler: Handler


TOOLS: dict[str, Tool] = {
    "get_po_schedule": Tool(Empty, get_po_schedule),
    "get_production_schedule": Tool(Empty, get_production_schedule),
    "save_signals": Tool(SaveSignalsBody, save_signals),
    "get_supplier_network": Tool(NetworkBody, get_supplier_network),
    "map_event_to_sites": Tool(CaseBody, map_event_to_sites),
    "compute_impact": Tool(ComputeImpactBody, compute_impact),
    "score_case": Tool(CaseBody, score_case),
    "assess_confidence": Tool(CaseBody, assess_confidence),
    "request_supplier_confirmation": Tool(RequestConfirmationBody, request_supplier_confirmation),
    "get_supplier_messages": Tool(SupplierMessagesBody, get_supplier_messages),
    "notify_meeting_room": Tool(NotifyMeetingBody, notify_meeting_room),
    "list_alternate_sources": Tool(AlternateSourcesBody, list_alternate_sources),
    "simulate_plan": Tool(SimulatePlanBody, simulate_plan),
    "save_recommendation": Tool(SaveRecommendationBody, save_recommendation),
    "get_case": Tool(GetCaseBody, get_case),
    "run_what_if": Tool(LocationBody, run_what_if),
    "get_report_data": Tool(CaseBody, get_report_data),
    "save_report": Tool(SaveReportBody, save_report),
    "log_trace": Tool(LogTraceBody, log_trace),
    "get_country_heatmap": Tool(Empty, get_country_heatmap),
    "get_recommendation_options": Tool(CaseBody, get_recommendation_options),
    "validate_numbers": Tool(ValidateNumbersBody, validate_numbers),
    "get_escalation": Tool(CaseBody, get_escalation),
}


def not_implemented(tool_name: str) -> ApiError:
    return ApiError(501, "NOT_IMPLEMENTED", "not implemented yet", {"tool": tool_name})


# --- dispatch ---------------------------------------------------------------------------------------------------

def _authorized(request: Request) -> bool:
    expected = request.app.state.settings.sentra_service_token
    given = request.headers.get(TOKEN_HEADER)
    if expected is None or not given:
        return False
    return hmac.compare_digest(given.encode(), expected.get_secret_value().encode())


async def _json_object(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ApiError(400, "VALIDATION_FAILED", "The body must be a JSON object.")
    return body


@router.post("/{tool_name}")
async def call_tool(tool_name: str, request: Request) -> JSONResponse:
    if not _authorized(request):
        raise ApiError(401, "UNAUTHENTICATED", "Authentication is required.")
    tool = TOOLS.get(tool_name)
    if tool is None:
        raise ApiError(404, "NOT_FOUND", f"Unknown tool: {tool_name}.", {"tool": tool_name})
    try:
        body = tool.body.model_validate(await _json_object(request))
    except ValidationError as exc:
        fields = [{"field": ".".join(map(str, error["loc"]))} for error in exc.errors()]
        raise ApiError(400, "VALIDATION_FAILED", "Request validation failed.", {"fields": fields}) from None
    # One transaction per call; an error leaves the session uncommitted, so nothing of the call is stored.
    with request.app.state.database.session_factory() as session:
        result = await tool.handler(Call(request, session, body))
        session.commit()
    return JSONResponse(result)
