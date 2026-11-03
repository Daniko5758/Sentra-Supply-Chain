import hmac

from pydantic import SecretStr
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mock_s4.errors import error_response


class SimulatedAuthMiddleware:
    """Authenticate all ERP requests and label successes and errors alike."""

    def __init__(self, app: ASGIApp, token: SecretStr | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, supplied_token = authorization.partition(" ")
        expected_token = self.token.get_secret_value() if self.token else ""
        valid = (
            bool(expected_token)
            and scheme.lower() == "bearer"
            and hmac.compare_digest(supplied_token.encode("utf-8"), expected_token.encode("utf-8"))
        )
        if not valid:
            response = error_response(401, "UNAUTHENTICATED", "Authentication is required.")
            await response(scope, receive, send)
            return

        async def simulated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Data-Source"] = "SIMULATED"
            await send(message)

        await self.app(scope, receive, simulated_send)
