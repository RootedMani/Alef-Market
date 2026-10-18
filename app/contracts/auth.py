"""Account contracts. Passwords are only ever in requests, never in responses or logs."""

from __future__ import annotations

from pydantic import Field, SecretStr

from app.contracts.common import Contract


class RegisterRequest(Contract):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: SecretStr = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)


class LoginRequest(Contract):
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)


class UserPublic(Contract):
    id: int
    username: str
    display_name: str
    is_demo: bool = False
