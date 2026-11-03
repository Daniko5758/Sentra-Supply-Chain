"""Demo-account login (FR-20, EP-01..EP-03): PBKDF2 password hashes, server-side sessions, role dependencies.

Generate a DEMO_USERS hash locally (the password is read from the terminal, never from argv):
    python -m sentra_api.auth
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Receive, Scope, Send

from sentra_api.config import Settings
from sentra_api.errors import ApiError, error_response

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sentra_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
LOGIN_PATH = "/api/auth/login"

_ALGORITHM = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16


class Role(StrEnum):
    BUYER = "BUYER"
    PPIC = "PPIC"
    SALES = "SALES"
    SCM_MANAGER = "SCM_MANAGER"


# --- passwords --------------------------------------------------------------------------------------------------

def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{_ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = stored.split("$")
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
    except (ValueError, binascii.Error):
        return False
    if algorithm != _ALGORITHM or iterations < 1 or not salt or not expected:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- accounts and sessions --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: Role


@dataclass(frozen=True)
class _DemoUser:
    role: Role
    password_hash: str


def load_demo_users(settings: Settings) -> dict[str, _DemoUser]:
    """Parse DEMO_USERS (`{username: {role, password_hash}}`). Fails closed: bad input yields fewer accounts, never a crash.

    Log lines name the account but never print the raw value or a hash.
    """
    raw = settings.demo_users.get_secret_value() if settings.demo_users is not None else ""
    if not raw.strip():
        logger.warning("DEMO_USERS is empty; nobody can log in.")
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.error("DEMO_USERS is not valid JSON; nobody can log in.")
        return {}
    if not isinstance(parsed, dict):
        logger.error("DEMO_USERS must be a JSON object of accounts; nobody can log in.")
        return {}
    users: dict[str, _DemoUser] = {}
    for username, entry in parsed.items():
        try:
            users[username] = _DemoUser(Role(entry["role"]), str(entry["password_hash"]))
        except (KeyError, TypeError, ValueError):
            logger.error("DEMO_USERS account %r is invalid and was skipped.", username)
    return users


class SessionStore:
    """In-memory sessions: revocable on logout, lost on restart (single CF instance)."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, tuple[CurrentUser, float]] = {}

    def create(self, username: str, role: Role | str) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (CurrentUser(username, Role(role)), self._clock() + self._ttl)
        return token

    def get(self, token: str | None) -> CurrentUser | None:
        entry = self._sessions.get(token) if token else None
        if entry is None:
            return None
        user, expires_at = entry
        if expires_at <= self._clock():
            self._sessions.pop(token, None)
            return None
        return user

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, (_, expires_at) in self._sessions.items() if expires_at <= now]:
            del self._sessions[token]


class AuthService:
    def __init__(self, users: dict[str, _DemoUser], sessions: SessionStore | None = None) -> None:
        self._users = users
        self.sessions = sessions or SessionStore()
        # Verified for unknown usernames too, so response time does not reveal which accounts exist.
        self._decoy_hash = hash_password(secrets.token_hex(16), iterations=1_000)

    @classmethod
    def from_settings(cls, settings: Settings) -> "AuthService":
        return cls(load_demo_users(settings))

    def authenticate(self, username: str, password: str) -> CurrentUser | None:
        user = self._users.get(username)
        valid = verify_password(password, user.password_hash if user else self._decoy_hash)
        return CurrentUser(username, user.role) if user is not None and valid else None

    def login(self, username: str, password: str) -> tuple[CurrentUser, str] | None:
        user = self.authenticate(username, password)
        return (user, self.sessions.create(user.username, user.role)) if user else None


# --- dependencies and gate --------------------------------------------------------------------------------------

def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Authentication is required.")


def require_user(request: Request) -> CurrentUser:
    user = request.app.state.auth.sessions.get(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise _unauthenticated()
    return user


def require_role(*roles: Role) -> Callable[[Request], CurrentUser]:
    """Route dependency: 401 without a session, 403 FORBIDDEN for any other role."""
    allowed = frozenset(Role(role) for role in roles)

    def dependency(request: Request) -> CurrentUser:
        user = require_user(request)
        if user.role not in allowed:
            raise ApiError(403, "FORBIDDEN", "Permission denied.")
        return user

    return dependency


def _needs_session(path: str) -> bool:
    return (path == "/api" or path.startswith("/api/")) and path != LOGIN_PATH


class ApiAuthGate:
    """401 for every /api path without a valid session (including paths that do not exist yet), except login."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _needs_session(scope["path"]):
            token = StarletteRequest(scope).cookies.get(SESSION_COOKIE)
            if scope["app"].state.auth.sessions.get(token) is None:
                error = _unauthenticated()
                response = error_response(error.status, error.code, error.message)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _main() -> None:
    import getpass

    password = getpass.getpass("Password: ")
    if not password or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords are empty or do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    _main()
