"""The delayed scripted answers of R-12 v2: the only deferred process in the supplier simulation (FR-11).

A background thread, like the run queue. The due time is `supplier_request.due_at` (request time + SIM_REPLY_DELAY_S), read from the
database, so a restart of the app loses no answer and nothing is ever sent twice. When answers are delivered, the same
transaction queues the `resume_case` (the dispatcher is woken after the commit).
"""

import logging
import threading
from datetime import datetime

from fastapi import FastAPI

from sentra_api.db import utc_now
from sentra_api.services.supplier_service import deliver_due_replies

log = logging.getLogger(__name__)

POLL_S = 1.0


class SupplierReplyWorker:
    def __init__(self, app: FastAPI, *, poll_s: float = POLL_S) -> None:
        self.app = app
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Runs only where agent runs run: without a runner (AGENT_RUNNER=off) nothing would process the `resume_case`."""
        if self.app.state.run_queue.runner is None or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="supplier-reply-sim", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def deliver_due(self, now: datetime | None = None) -> list[tuple[str, str]]:
        """Deliver the answers due at `now` (default: the current time); returns the `(case_id, site_id)` pairs answered."""
        app = self.app
        with app.state.database.session_factory() as session, session.begin():
            delivered = deliver_due_replies(session, app.state.seed_data, now=now or utc_now())
        if delivered:
            app.state.run_queue.wake()  # after the commit, so the worker sees the queued run
        return delivered

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.deliver_due()
            except Exception:
                log.exception("supplier reply simulation failed")
