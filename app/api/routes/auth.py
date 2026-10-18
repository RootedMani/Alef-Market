"""Account endpoints. The session lives in an HttpOnly cookie. Planned: Week 1."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import DbDep, SettingsDep, UserDep
from app.contracts.auth import LoginRequest, RegisterRequest, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=201)
def register(req: RegisterRequest, db: DbDep, settings: SettingsDep) -> UserPublic:
    """409 if the username is taken."""
    raise NotImplementedError("Week 1")


@router.post("/login", response_model=UserPublic)
def login(req: LoginRequest, response: Response, db: DbDep, settings: SettingsDep) -> UserPublic:
    """Sets the session cookie. 401 with a generic message on any mismatch."""
    raise NotImplementedError("Week 1")


@router.post("/logout", status_code=204)
def logout(response: Response, db: DbDep) -> None:
    raise NotImplementedError("Week 1")


@router.get("/me", response_model=UserPublic)
def me(user: UserDep) -> UserPublic:
    return user
