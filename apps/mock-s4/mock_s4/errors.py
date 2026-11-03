from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from mock_s4.messages import SapError, SapWriteRoute, odata_error, validation_failed


HTTP_ERRORS = {
    400: ("VALIDATION_FAILED", "Request validation failed."),
    401: ("UNAUTHENTICATED", "Authentication is required."),
    403: ("FORBIDDEN", "Permission denied."),
    404: ("NOT_FOUND", "Resource not found."),
    409: ("CONFLICT", "The current state does not allow this action."),
    500: ("INTERNAL_ERROR", "An internal error occurred."),
    502: ("UPSTREAM_ERROR", "An upstream service failed."),
}
LOCATIONS = {"body", "header", "query", "path", "cookie"}


class MockApiError(Exception):
    """A rejection with its own message (A.3 shape): reads, 401 and `404 FEATURE_DISABLED`. A write that the ERP refuses
    raises `SapError` (OData V2 shape, E.2.2, v1.12)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers={"X-Data-Source": "SIMULATED"},
    )


def sap_error_response(error: SapError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content=odata_error(error, uuid4().hex.upper()),
        headers={"X-Data-Source": "SIMULATED"},
    )


def _target(location: tuple) -> str:
    """The field or header a validation error is about: the last name in `loc` (`("body", "OrderQuantity")` -> `OrderQuantity`)."""
    names = [part for part in location if isinstance(part, str) and part not in LOCATIONS]
    return names[-1] if names else "body"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MockApiError)
    async def mock_error(request: Request, exc: MockApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(SapError)
    async def sap_error(request: Request, exc: SapError) -> JSONResponse:
        return sap_error_response(exc)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = HTTP_ERRORS.get(exc.status_code, ("INTERNAL_ERROR", "Request failed."))
        return error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not return rejected input or validator text: either may contain a secret.
        if isinstance(request.scope.get("route"), SapWriteRoute):  # a write of F-12: ZMOCK_S4/002, one detail per field
            targets = list(dict.fromkeys(_target(error["loc"]) for error in exc.errors()))
            main, *details = (validation_failed(target) for target in targets or [""])
            return sap_error_response(SapError(400, main, details))
        fields = [{"field": ".".join(map(str, error["loc"]))} for error in exc.errors()]
        return error_response(400, "VALIDATION_FAILED", "Request validation failed.", {"fields": fields})

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
