"""Clients of the outside services the F-07 meeting room needs: Zoom (meetings) and Gmail SMTP (e-mail). Each failure becomes a
short error without secrets; the callers record it in `integration_call` (EP-26)."""

import time
from typing import Any

from sentra_api.integrations.mailer import MailError
from sentra_api.integrations.zoom import ZoomError


def timed_call(call, *args: Any, **kwargs: Any) -> tuple[Any, int, str | None]:
    """Run a blocking client call: (result or None, milliseconds, short error or None). Only `ZoomError` and `MailError` are caught."""
    started = time.perf_counter()
    try:
        result, error = call(*args, **kwargs), None
    except (ZoomError, MailError) as failure:
        result, error = None, str(failure)
    return result, round((time.perf_counter() - started) * 1000), error
