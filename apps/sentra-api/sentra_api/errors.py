from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


HTTP_ERRORS = {
    400: ("VALIDATION_FAILED", "Request validation failed."),
    401: ("UNAUTHENTICATED", "Authentication is required."),
    403: ("FORBIDDEN", "Permission denied."),
    404: ("NOT_FOUND", "Resource not found."),
    409: ("CONFLICT", "The current state does not allow this action."),
    500: ("INTERNAL_ERROR", "An internal error occurred."),
    502: ("UPSTREAM_ERROR", "An upstream service failed."),
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = HTTP_ERRORS.get(exc.status_code, ("INTERNAL_ERROR", "Request failed."))
        return error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(map(str, error["loc"]))} for error in exc.errors()]
        return error_response(400, "VALIDATION_FAILED", "Request validation failed.", {"fields": fields})

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
