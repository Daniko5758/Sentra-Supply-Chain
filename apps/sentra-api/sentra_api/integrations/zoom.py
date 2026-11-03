"""Zoom Server-to-Server OAuth client (B-45, R-22): a token, then a scheduled meeting for a Meeting Room.

Only `httpx` (sync; callers run it in a worker thread). A failure raises `ZoomError` with a short message that carries no secret,
no token and no response body. The `start_url` of a created meeting and the token are never returned or stored.
"""

import base64
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from sentra_api.config import Settings

TOKEN_URL = "https://zoom.us/oauth/token"
API_URL = "https://api.zoom.us/v2"
TIMEOUT_S = 10  # per call
TOKEN_MARGIN_S = 60  # a cached token is dropped this long before it expires
TOPIC_LIMIT, AGENDA_LIMIT = 200, 2000


class ZoomError(Exception):
    """A Zoom call failed or Zoom is not configured; `args[0]` is a short message without secrets."""


@dataclass(frozen=True)
class ZoomMeeting:
    zoom_meeting_id: str
    join_url: str


class ZoomClient:
    """One per process (the token is cached in memory). `transport` is for tests (`httpx.MockTransport`)."""

    def __init__(
        self, settings: Settings, *, transport: httpx.BaseTransport | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def configured(self) -> bool:
        s = self._settings
        return bool(s.zoom_account_id and s.zoom_client_id and s.zoom_client_secret and s.zoom_user_id)

    def _http(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=TIMEOUT_S)

    def token(self, *, force: bool = False) -> str:
        """The access token; a new one is requested only when the cached one is about to expire (`expires_in` - 60 s), or when
        `force` is set (the health check of EP-26 must reach Zoom)."""
        if not self.configured:
            raise ZoomError("Zoom not configured")
        with self._lock:
            if not force and self._token is not None and self._clock() < self._expires_at:
                return self._token
            s = self._settings
            basic = base64.b64encode(f"{s.zoom_client_id.get_secret_value()}:{s.zoom_client_secret.get_secret_value()}".encode())
            try:
                with self._http() as http:
                    response = http.post(
                        TOKEN_URL, params={"grant_type": "account_credentials", "account_id": s.zoom_account_id},
                        headers={"Authorization": "Basic " + basic.decode()})
            except httpx.HTTPError:
                raise ZoomError("Zoom token request failed (network error)") from None
            if response.status_code != 200:
                raise ZoomError(f"Zoom token request failed ({response.status_code})")
            try:
                body = response.json()
                token, lifetime = str(body["access_token"]), int(body.get("expires_in", 3600))
            except (ValueError, KeyError, TypeError):
                raise ZoomError("Zoom token request failed (unexpected answer)") from None
            self._token, self._expires_at = token, self._clock() + max(lifetime - TOKEN_MARGIN_S, 0)
            return token

    def create_meeting(self, *, topic: str, start_time: datetime, duration_min: int, agenda: str) -> ZoomMeeting:
        """A scheduled meeting (type 2) of the host `ZOOM_USER_ID`; HTTP 201 gives the id and the join link."""
        token = self.token()
        body = {
            "topic": topic[:TOPIC_LIMIT], "type": 2, "start_time": start_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": duration_min, "timezone": "Asia/Jakarta", "agenda": agenda[:AGENDA_LIMIT],
            "settings": {"join_before_host": True, "waiting_room": False, "mute_upon_entry": True},
        }
        url = f"{API_URL}/users/{quote(self._settings.zoom_user_id, safe='@')}/meetings"
        try:
            with self._http() as http:
                response = http.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            raise ZoomError("Zoom create meeting failed (network error)") from None
        if response.status_code != 201:
            if response.status_code == 401:  # the token was revoked: ask for a new one next time
                with self._lock:
                    self._token = None
            raise ZoomError(f"Zoom create meeting failed ({response.status_code})")
        try:
            answer = response.json()
            return ZoomMeeting(str(answer["id"]), str(answer["join_url"]))  # `start_url` and `password` are left out on purpose
        except (ValueError, KeyError, TypeError):
            raise ZoomError("Zoom create meeting failed (unexpected answer)") from None
