"""EP-26 `POST /api/system/checks`: the light checks behind the button `Run checks` (FR-27, B-19).

Two checks run side by side: RPT-1.6 with one query row, and the `ping` task of the agent (one short model call, no tool), which
also shows whether AgentCore and the model answer. With FEATURE_MEETING_ROOM on two more run: Zoom (a token request only: no meeting
is created, which keeps the 100 creations a day) and Gmail (SMTP login and a test e-mail to ROLE_EMAIL_SCM_MANAGER, IT-05).
Each result is recorded in `integration_call` like any other call.
Sonar and Brave have no light check: only the agent can reach them and it has no task for that, so they show the last call of a
scan. (GDELT and Currents are no longer called or listed, DEC-032.)
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi import FastAPI

from sentra_api.confidence import check_rpt
from sentra_api.db import utc_now
from sentra_api.integrations import timed_call
from sentra_api.integrations.mailer import addresses_for, send_mail
from sentra_api.models import RptHistoryAffected
from sentra_api.services.confidence_service import INTEGRATION as RPT_INTEGRATION
from sentra_api.services.confidence_service import history_rows
from sentra_api.services.system_status import record_integration_call

log = logging.getLogger(__name__)


class SystemChecks:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._running = threading.Lock()

    def run(self) -> None:
        """Run both checks and return when they are done (the agent call may take up to 150 s). A request that comes while a
        check is running waits for it and shares its result, so a double click does not call the model twice."""
        if not self._running.acquire(blocking=False):
            with self._running:
                return
        try:
            checks = [self._check_rpt, self._check_agent]
            if self.app.state.settings.feature_meeting_room:  # v1.11: Zoom and Gmail are integrations of F-07
                checks += [self._check_zoom, self._check_gmail]
            with ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="system-check") as pool:
                futures = [pool.submit(check) for check in checks]
            for future in futures:
                if future.exception() is not None:
                    log.error("system check failed", exc_info=future.exception())
        finally:
            self._running.release()

    def _check_rpt(self) -> None:
        database = self.app.state.database
        with database.session_factory() as session:
            history = history_rows(session, RptHistoryAffected)
        call = check_rpt(self.app.state.rpt_gateway, history)  # the network call; no database session is open meanwhile
        with database.session_factory() as session, session.begin():
            record_integration_call(session, RPT_INTEGRATION, call.status, call.duration_ms, call.message, utc_now())

    def _check_agent(self) -> None:
        """`ping` on a new session id, so no session of the warm pool or of a case is disturbed. Without a runner
        (AGENT_RUNNER=off) there is no agent to check; the fake runner answers but calls nothing outside, so records nothing."""
        self.app.state.session_pool.ping(str(uuid4()), report_unreached=True)

    def _record(self, name: str, millis: int, error: str | None) -> None:
        with self.app.state.database.session_factory() as session, session.begin():
            record_integration_call(session, name, "OK" if error is None else "FAILED", millis, error, utc_now())

    def _check_zoom(self) -> None:
        _, millis, error = timed_call(self.app.state.zoom.token, force=True)
        self._record("zoom", millis, error)

    def _check_gmail(self) -> None:
        settings = self.app.state.settings
        _, millis, error = timed_call(
            send_mail, settings, recipients=addresses_for(settings, ["SCM_MANAGER"]),
            subject="[SENTRA] Gmail check", text="This is the test message of Run checks in SENTRA (System status).")
        self._record("gmail", millis, error)
