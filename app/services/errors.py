"""Errors services raise on purpose. ``app/api/errors.py`` turns them into ``ApiError``."""

from __future__ import annotations


class InvalidRequest(ValueError):
    """The request is well-formed but asks for something we refuse (HTTP 400).

    ``message_fa`` is shown to the user; ``detail`` (English) is for developers.
    """

    def __init__(self, detail: str, message_fa: str = "درخواست نامعتبر است.") -> None:
        super().__init__(detail)
        self.detail = detail
        self.message_fa = message_fa


class NotFound(LookupError):
    """The thing doesn't exist or belongs to another user (HTTP 404, same message for both)."""

    def __init__(self, detail: str, message_fa: str = "پیدا نشد.") -> None:
        super().__init__(detail)
        self.detail = detail
        self.message_fa = message_fa


class Conflict(RuntimeError):
    """The request clashes with the current state, e.g. closing a closed trade (HTTP 409)."""

    def __init__(self, detail: str, message_fa: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.message_fa = message_fa
