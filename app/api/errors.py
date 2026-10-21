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
from app.services.errors import Conflict, InvalidRequest, NotFound

logger = logging.getLogger(__name__)

_STATUS_MESSAGES_FA: dict[int, tuple[str, str]] = {
    400: ("bad_request", "درخواست نامعتبر است."),
    401: ("unauthorized", "لطفاً وارد حساب کاربری شوید."),
    403: ("forbidden", "اجازهٔ دسترسی به این بخش را ندارید."),
    404: ("not_found", "پیدا نشد."),
    405: ("method_not_allowed", "این نوع درخواست برای این آدرس پشتیبانی نمی‌شود."),
    409: ("conflict", "این مورد قبلاً ثبت شده است."),
    413: ("payload_too_large", "حجم اطلاعات ارسال‌شده بیش از حد مجاز است."),
    422: ("invalid_request", "اطلاعات ارسال‌شده کامل یا درست نیست."),
    429: ("too_many_requests", "تعداد درخواست‌ها زیاد است؛ کمی صبر کنید و دوباره امتحان کنید."),
    501: ("not_implemented", "این بخش هنوز ساخته نشده است."),
}


def api_error(status: int, detail: str | None = None, message_fa: str | None = None) -> ApiError:
    code, default_fa = _STATUS_MESSAGES_FA.get(status, ("server_error", "خطایی رخ داد."))
    return ApiError(code=code, message_fa=message_fa or default_fa, detail=detail)


def api_error_for(exc: Exception) -> tuple[int, ApiError]:
    """``(status, ApiError)`` for any exception: the JSON handlers and the HTML fragments
    (which show ``message_fa`` in a calm card) map errors the same way."""
    if isinstance(exc, StarletteHTTPException):
        return exc.status_code, api_error(exc.status_code, str(exc.detail) if exc.detail else None)
    if isinstance(exc, RequestValidationError):
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return 422, api_error(422, problems)
    for kind, status in ((InvalidRequest, 400), (NotFound, 404), (Conflict, 409)):
        if isinstance(exc, kind):
            return status, api_error(status, exc.detail, exc.message_fa)  # type: ignore[attr-defined]
    if isinstance(exc, NotImplementedError):
        return 501, api_error(501, str(exc) or None)
    return 500, api_error(500)


def _response(exc: Exception) -> JSONResponse:
    status, body = api_error_for(exc)
    return JSONResponse(status_code=status, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(InvalidRequest)
    async def _invalid(_: Request, exc: InvalidRequest) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _response(exc)
