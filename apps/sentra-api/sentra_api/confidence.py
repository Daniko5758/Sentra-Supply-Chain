"""R-10 (K_A), R-11 (K_B) and the RPT-1.6 client (B-14).

The formulas, the request rows and the reading of the answer are plain functions over values. `assess_sites` runs the two
RPT requests in the order the rules need (K_A first: `rumus_B` and the `SITE_EVENT_STATUS` of K_B come from it) and
measures them; it touches no database. The SDK client sits behind `SapRptGateway`, created once per process.

K = 0.5 * rumus + 0.5 * 100 * P, to one decimal, half-up (R-20). The result of every step is rounded before the next step
uses it. Without P (RPT down, no prediction for the site, no confidence): K = rumus and `rpt_status = UNAVAILABLE` (ERR-01).
"""

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sentra_api.config import Settings
from sentra_api.priority import round1

PLACEHOLDER = "[PREDICT]"
INDEX_COLUMN = "ID"
PROBE_ID = "CHECK"  # the ID of the one query row of the System status check
AFFECTED_TARGET = "AFFECTED"
ON_TIME_TARGET = "ON_TIME"
RPT_TIMEOUT_S = 30
# R-11 (SITE_EVENT_STATUS is AFFECTED from this K_A) and R-12 (a K below it asks the supplier).
K_THRESHOLD = 50
# R-10: event types that have zone data about the site; any other type uses Z = 0.5 and sends HAZARD_ZONE = NO.
ZONE_DATA_EVENT_TYPES = frozenset({"FLOOD"})

D = Decimal


class ConfidenceError(Exception):
    """The case cannot be assessed (missing event data)."""


class RptUnavailable(Exception):
    """The gateway has no settings to call RPT-1.6. The text names settings only, never their values."""


# --- R-10, R-11 ----------------------------------------------------------------------------------------------------

def rumus_a(*, kecamatan_mentioned: bool, hazard_zone: bool, zone_data: bool, severity: int) -> float:
    """100 * (0.4 L + 0.3 Z + 0.3 S). L: district named 1.0, only the kabupaten 0.4. Z: in the zone 1.0, not 0.3, 0.5 when
    the event type has no zone data. S: severity / 5."""
    location = D("1.0") if kecamatan_mentioned else D("0.4")
    zone = (D("1.0") if hazard_zone else D("0.3")) if zone_data else D("0.5")
    return round1(100 * (D("0.4") * location + D("0.3") * zone + D("0.3") * (D(severity) / D(5))))


def rumus_b(*, k_a: float, otd_12m: float, slip_days: int) -> float:
    """100 * (0.5 (1 - K_A/100) + 0.3 OTD_12M + 0.2 (1 - min(1, slip/14))), with the stored (rounded) K_A."""
    slip_term = 1 - min(D(1), D(slip_days) / D(14))
    return round1(100 * (D("0.5") * (1 - D(str(k_a)) / 100) + D("0.3") * D(str(otd_12m)) + D("0.2") * slip_term))


def probability(prediction: str, confidence: float | None) -> float | None:
    """P of YES. `confidence` is the chance of the label RPT predicted: YES -> confidence, any other label -> 1 - confidence."""
    if confidence is None:
        return None
    chance = D(str(confidence))  # decimal arithmetic: 1 - 0.85 is 0.15, not 0.15000000000000002
    if not 0 <= chance <= 1:
        return None
    return float(chance if prediction == "YES" else 1 - chance)


def k_value(rumus: float, p: float | None) -> tuple[float, str]:
    """(K, rpt_status). Without P: (rumus, UNAVAILABLE)."""
    if p is None:
        return rumus, "UNAVAILABLE"
    return round1(D("0.5") * D(str(rumus)) + 50 * D(str(p))), "OK"


# --- what is known about the event and the sites -------------------------------------------------------------------

@dataclass(frozen=True)
class EventFacts:
    event_type: str
    severity: int | None


@dataclass(frozen=True)
class SiteFacts:
    site_id: str
    tier: str
    kecamatan_mentioned: bool
    hazard_zone: bool
    has_backup_site: bool
    otd_12m: float | None
    slip_days: int | None  # largest slip of its open PO lines, at least 0; None when the site has no open PO line

    @property
    def has_k_b(self) -> bool:
        """R-11: tier-1 sites with an open PO line."""
        return self.tier == "1" and self.slip_days is not None and self.otd_12m is not None


# --- requests to RPT-1.6 -------------------------------------------------------------------------------------------

def _yes_no(flag: bool) -> str:
    return "YES" if flag else "NO"


def affected_query_row(event: EventFacts, site: SiteFacts) -> dict[str, Any]:
    return {
        INDEX_COLUMN: site.site_id,
        "EVENT_TYPE": event.event_type,
        "SEVERITY": event.severity,
        "SUBDISTRICT_MENTIONED": _yes_no(site.kecamatan_mentioned),
        "HAZARD_ZONE": _yes_no(site.hazard_zone and event.event_type in ZONE_DATA_EVENT_TYPES),
        "HAS_BACKUP_SITE": _yes_no(site.has_backup_site),
        AFFECTED_TARGET: PLACEHOLDER,
    }


def delivery_query_row(site: SiteFacts, k_a: float) -> dict[str, Any]:
    return {
        INDEX_COLUMN: site.site_id,
        "SITE_EVENT_STATUS": "AFFECTED" if k_a >= K_THRESHOLD else "NEAR_EVENT",
        "HAS_BACKUP_SITE": _yes_no(site.has_backup_site),
        "OTD_12M": site.otd_12m,
        "CURRENT_SLIP_DAYS": site.slip_days,
        ON_TIME_TARGET: PLACEHOLDER,
    }


def build_request(history: Sequence[dict[str, Any]], queries: Sequence[dict[str, Any]], target: str) -> dict[str, Any]:
    """The body for `RPTClient.predict`: the labelled history first, then the query rows. No `data_schema` (not needed,
    SP-02) and no `parse_data_types` (the SDK adds it)."""
    return {
        "prediction_config": {"target_columns": [
            {"name": target, "prediction_placeholder": PLACEHOLDER, "task_type": "classification"}]},
        "index_column": INDEX_COLUMN,
        "rows": [dict(row) for row in history] + [dict(row) for row in queries],
    }


def read_predictions(response: Any, target: str) -> dict[str, float]:
    """P of YES per row ID from `RPTResponse.model_dump()`. Rows are matched by their `ID`, never by position; a row
    without a prediction or without a confidence is left out."""
    predictions = response.get("predictions") if isinstance(response, dict) else None
    found: dict[str, float] = {}
    for item in predictions if isinstance(predictions, list) else []:
        if not isinstance(item, dict):
            continue
        entries = item.get(target)
        if item.get(INDEX_COLUMN) is None or not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            continue
        confidence = entries[0].get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            continue
        p = probability(str(entries[0].get("prediction")), confidence)
        if p is not None:
            found[str(item[INDEX_COLUMN])] = p
    return found


# --- the assessment ------------------------------------------------------------------------------------------------

class Rpt(Protocol):
    configured: bool

    def predict(self, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RptCall:
    """One request to RPT-1.6, for `integration_call`. `message` never holds more than the kind of failure."""

    target: str
    status: str  # OK | FAILED
    duration_ms: int
    message: str | None


@dataclass(frozen=True)
class SiteConfidence:
    site_id: str
    k_a_formula: float
    p_rpt_a: float | None
    k_a: float
    k_b_formula: float | None
    p_rpt_b: float | None
    k_b: float | None
    rpt_status: str

    @property
    def reasons(self) -> list[str]:
        """R-12: why the supplier is asked."""
        reasons = []
        if self.k_a < K_THRESHOLD:
            reasons.append(f"K_A({self.site_id})={self.k_a:.1f} < {K_THRESHOLD}")
        if self.k_b is not None and self.k_b < K_THRESHOLD:
            reasons.append(f"K_B({self.site_id})={self.k_b:.1f} < {K_THRESHOLD}")
        return reasons

    def to_tl10(self) -> dict[str, Any]:
        reasons = self.reasons
        return {
            "site_id": self.site_id, "k_a_formula": self.k_a_formula, "p_rpt_a": self.p_rpt_a, "k_a": self.k_a,
            "k_b_formula": self.k_b_formula, "p_rpt_b": self.p_rpt_b, "k_b": self.k_b,
            "needs_verification": bool(reasons), "reason": "; ".join(reasons) or None,
        }


@dataclass(frozen=True)
class Assessment:
    sites: tuple[SiteConfidence, ...]
    calls: tuple[RptCall, ...]

    @property
    def rpt_status(self) -> str:
        return "OK" if all(site.rpt_status == "OK" for site in self.sites) else "UNAVAILABLE"


def _failure(error: Exception) -> str:
    return str(error)[:200] if isinstance(error, RptUnavailable) else type(error).__name__


def _ask(rpt: Rpt, target: str, body: dict[str, Any], calls: list[RptCall]) -> dict[str, float] | None:
    """P of YES per queried ID, or None when the request failed or the answer holds no usable prediction. A gateway
    without settings is not asked: nothing is called, so nothing is recorded."""
    if not getattr(rpt, "configured", True):
        return None
    asked = sum(1 for row in body["rows"] if row.get(target) == PLACEHOLDER)
    started = time.perf_counter()
    try:
        response = rpt.predict(body)
    except Exception as error:  # the SDK raises its own and httpx's errors; every one means "unavailable" (ERR-01)
        calls.append(RptCall(target, "FAILED", _ms(started), _failure(error)))
        return None
    found = read_predictions(response, target)
    if not found:
        calls.append(RptCall(target, "FAILED", _ms(started), "no usable prediction in the answer"))
        return None
    missing = asked - len(found)
    calls.append(RptCall(target, "OK", _ms(started), f"no usable prediction for {missing} of {asked} rows" if missing else None))
    return found


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def probe_request(history_affected: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The body of the light check (EP-26): the labelled history and ONE query row, a copy of the first history row whose label
    is to be predicted. Needs at least one history row."""
    probe = {**history_affected[0], INDEX_COLUMN: PROBE_ID, AFFECTED_TARGET: PLACEHOLDER}
    return build_request(history_affected, [probe], AFFECTED_TARGET)


def check_rpt(rpt: Rpt, history_affected: Sequence[dict[str, Any]]) -> RptCall:
    """One request with one query row. Unlike `_ask`, a gateway without settings is asked too: it fails with the names of the
    missing settings, so the button `Run checks` can say why RPT-1.6 is red. Blocks up to 30 s: call it from a thread."""
    if not history_affected:
        return RptCall(AFFECTED_TARGET, "FAILED", 0, "no labelled history rows to send")
    started = time.perf_counter()
    try:
        response = rpt.predict(probe_request(history_affected))
    except Exception as error:
        return RptCall(AFFECTED_TARGET, "FAILED", _ms(started), _failure(error))
    if PROBE_ID not in read_predictions(response, AFFECTED_TARGET):
        return RptCall(AFFECTED_TARGET, "FAILED", _ms(started), "no usable prediction in the answer")
    return RptCall(AFFECTED_TARGET, "OK", _ms(started), None)


def assess_sites(
    event: EventFacts, sites: Sequence[SiteFacts], *, history_affected: Sequence[dict[str, Any]],
    history_delivery: Sequence[dict[str, Any]], rpt: Rpt,
) -> Assessment:
    """K_A for every candidate site (one request), then K_B for the tier-1 sites with an open PO line (one request).

    The two requests are sequential. When the K_A request fails, RPT is down and the K_B request is not sent: a second wait
    of up to 30 s would pass the 30 s an agent gives a tool call. K_B is then `rumus_B` of the K_A of the formula.
    """
    if event.severity is None:
        raise ConfidenceError("The case has no event severity, so K_A cannot be computed.")
    calls: list[RptCall] = []
    formula_a = {
        s.site_id: rumus_a(kecamatan_mentioned=s.kecamatan_mentioned, hazard_zone=s.hazard_zone,
                           zone_data=event.event_type in ZONE_DATA_EVENT_TYPES, severity=event.severity)
        for s in sites
    }
    p_a = _ask(rpt, AFFECTED_TARGET,
               build_request(history_affected, [affected_query_row(event, s) for s in sites], AFFECTED_TARGET),
               calls) if sites else {}
    k_a = {s.site_id: k_value(formula_a[s.site_id], (p_a or {}).get(s.site_id)) for s in sites}

    with_k_b = [s for s in sites if s.has_k_b]
    p_b: dict[str, float] | None = {}
    if with_k_b and p_a is not None:
        p_b = _ask(rpt, ON_TIME_TARGET,
                   build_request(history_delivery, [delivery_query_row(s, k_a[s.site_id][0]) for s in with_k_b], ON_TIME_TARGET),
                   calls)

    results = []
    for s in sites:
        ka, status = k_a[s.site_id]
        formula_b = k_b = p_rpt_b = None
        if s.has_k_b:
            formula_b = rumus_b(k_a=ka, otd_12m=s.otd_12m, slip_days=s.slip_days)
            p_rpt_b = (p_b or {}).get(s.site_id)
            k_b, status_b = k_value(formula_b, p_rpt_b)
            status = status if status_b == "OK" else "UNAVAILABLE"
        results.append(SiteConfidence(
            site_id=s.site_id, k_a_formula=formula_a[s.site_id], p_rpt_a=(p_a or {}).get(s.site_id), k_a=ka,
            k_b_formula=formula_b, p_rpt_b=p_rpt_b, k_b=k_b, rpt_status=status,
        ))
    return Assessment(tuple(results), tuple(calls))


# --- the SDK client ------------------------------------------------------------------------------------------------

class SapRptGateway:
    """RPT-1.6 through the SDK's `RPTClient` (SAP Cloud SDK for AI, DEC-013).

    One `GenAIHubProxyClient` and one `RPTClient` per process, created at the first request (nothing is imported or called
    at start). The SDK manages the OAuth token. The URL is `AICORE_BASE_URL/v2/inference/deployments/DEPLOYMENT_RPT`;
    `RPTClient` adds `/predict` itself and the deployment is never looked up by model name.
    """

    def __init__(
        self, *, auth_url: str | None, base_url: str | None, client_id: str | None, client_secret: str | None,
        deployment_id: str | None, resource_group: str = "default", sdk_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._auth_url, self._base_url, self._resource_group = auth_url, base_url, resource_group
        self._client_id, self._client_secret, self._deployment_id = client_id, client_secret, deployment_id
        self._sdk_client_factory = sdk_client_factory or self._build_sdk_client
        self._sdk_client: Any = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "SapRptGateway":
        def value(secret):
            return secret.get_secret_value() if secret is not None else None

        return cls(
            auth_url=settings.aicore_auth_url, base_url=settings.aicore_base_url,
            client_id=value(settings.aicore_client_id), client_secret=value(settings.aicore_client_secret),
            deployment_id=settings.deployment_rpt, resource_group=settings.aicore_resource_group,
        )

    def _missing(self) -> list[str]:
        required = {"AICORE_AUTH_URL": self._auth_url, "AICORE_BASE_URL": self._base_url,
                    "AICORE_CLIENT_ID": self._client_id, "AICORE_CLIENT_SECRET": self._client_secret,
                    "DEPLOYMENT_RPT": self._deployment_id}
        return [name for name, value in required.items() if not value]

    @property
    def configured(self) -> bool:
        return not self._missing()

    @property
    def deployment_url(self) -> str:
        base = (self._base_url or "").rstrip("/")
        base = base if base.endswith("/v2") else base + "/v2"
        return f"{base}/inference/deployments/{self._deployment_id}"

    def _build_sdk_client(self) -> Any:
        from gen_ai_hub.proxy.gen_ai_hub_proxy import GenAIHubProxyClient
        from gen_ai_hub.proxy.native.sap.client import RPTClient

        proxy = GenAIHubProxyClient(
            base_url=self._base_url, auth_url=self._auth_url, client_id=self._client_id,
            client_secret=self._client_secret, resource_group=self._resource_group,
        )
        return RPTClient(proxy_client=proxy, timeout=RPT_TIMEOUT_S)

    def _client(self) -> Any:
        with self._lock:
            if self._sdk_client is None:
                self._sdk_client = self._sdk_client_factory()
            return self._sdk_client

    def predict(self, body: dict[str, Any]) -> dict[str, Any]:
        """The answer of RPT-1.6 as a plain dict (`RPTResponse.model_dump()`). Blocks up to 30 s: call it from a thread."""
        missing = self._missing()
        if missing:
            raise RptUnavailable("Missing settings for RPT-1.6: " + ", ".join(missing) + ".")
        response = self._client().predict(body, deployment_url=self.deployment_url, timeout=RPT_TIMEOUT_S)
        return response.model_dump()
