"""Feature flags (contract A.5). Periodic scans have no flag: they follow LIVE mode (DEC-024, EP-29)."""

from collections.abc import Callable

from fastapi import Request

from sentra_api.config import Settings
from sentra_api.errors import ApiError

# env name -> key in EP-03 / EP-26 `feature_flags`; the Settings attribute is the lower-cased env name.
FLAG_ATTRS = {
    "FEATURE_ERP_EXECUTION": "erp_execution",
    "FEATURE_MEETING_ROOM": "meeting_room",
    "FEATURE_WHATIF": "whatif",
}


def flag_enabled(settings: Settings, name: str) -> bool:
    if name not in FLAG_ATTRS:
        raise ValueError(f"unknown feature flag {name!r}")
    return bool(getattr(settings, name.lower()))


def feature_flags(settings: Settings) -> dict[str, bool]:
    return {key: flag_enabled(settings, name) for name, key in FLAG_ATTRS.items()}


def require_flag(name: str) -> Callable[[Request], None]:
    """Route dependency: `404 FEATURE_DISABLED` while the flag is off. Put it before require_role on a route.

    TL-13 is the exception (it answers `SKIPPED_FEATURE_OFF`); use flag_enabled there.
    """
    if name not in FLAG_ATTRS:
        raise ValueError(f"unknown feature flag {name!r}")

    def dependency(request: Request) -> None:
        if not flag_enabled(request.app.state.settings, name):
            raise ApiError(404, "FEATURE_DISABLED", "This feature is disabled.", {"flag": name})

    return dependency
