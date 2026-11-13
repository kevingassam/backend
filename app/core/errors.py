from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.i18n import request_locale, t


class AppError(Exception):
    """Business error rendered as {"error": {code, message, details}}."""

    def __init__(self, code: str, status: int = 400, details: Any = None, **params: Any):
        self.code = code
        self.status = status
        self.details = details
        self.params = params
        super().__init__(code)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        loc = request_locale(request)
        return JSONResponse(
            _body(exc.code, t(exc.code, loc, **exc.params), exc.details), status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        loc = request_locale(request)
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(_body("validation_error", t("validation_error", loc), details), 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        loc = request_locale(request)
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(_body(code, t(code, loc)), exc.status_code, headers=exc.headers)
