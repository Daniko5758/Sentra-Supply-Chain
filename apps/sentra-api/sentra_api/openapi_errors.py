"""Makes `openapi.json` describe the errors the API really sends (contract A.3), not FastAPI's default 422.

Every request that does not match its schema answers `400 VALIDATION_FAILED` (errors.py), and every `/api` path except
login answers `401 UNAUTHENTICATED` without a session. Both carry `{"error": {"code", "message", "details"}}`.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

LOGIN_PATH = "/api/auth/login"
ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    error: ErrorBody


def _error(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": ERROR_REF}}}


def install_error_schema(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        model = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(model.pop("$defs"))
        schemas["ErrorResponse"] = model
        for path, methods in schema["paths"].items():
            for operation in methods.values():
                responses = operation.setdefault("responses", {})
                if responses.pop("422", None) is not None:
                    responses["400"] = _error("VALIDATION_FAILED: the request does not match the contract")
                if path.startswith("/api/") and path != LOGIN_PATH:
                    responses["401"] = _error("UNAUTHENTICATED: no valid session")
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
