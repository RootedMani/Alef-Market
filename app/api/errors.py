"""Turn every error into the same ``ApiError`` JSON shape with a Persian message.

The frontend only ever handles one error format, and users never see a stack trace.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts.common import ApiError
from app.services.errors import InvalidRequest

logger = logging.getLogger(__name__)

_STATUS_MESSAGES_FA: dict[int, tuple[str, str]] = {
    400: ("bad_request", "درخواست نامعتبر است."),
    401: ("unauthorized", "لطفاً وارد حساب کاربری شوید."),
    403: ("forbidden", "اجازهٔ دسترسی به این بخش را ندارید."),
    404: ("not_found", "پیدا نشد."),
    409: ("conflict", "این مورد قبلاً ثبت شده است."),
    422: ("invalid_request", "اطلاعات ارسال‌شده کامل یا درست نیست."),
    501: ("not_implemented", "این بخش هنوز ساخته نشده است."),
}


def _error(status: int, detail: str | None = None, message_fa: str | None = None) -> JSONResponse:
    code, default_fa = _STATUS_MESSAGES_FA.get(status, ("server_error", "خطایی رخ داد."))
    body = ApiError(code=code, message_fa=message_fa or default_fa, detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail) if exc.detail else None)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _error(422, problems)

    @app.exception_handler(InvalidRequest)
    async def _invalid(_: Request, exc: InvalidRequest) -> JSONResponse:
        return _error(400, exc.detail, exc.message_fa)

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
        return _error(501, str(exc) or None)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error(500)
