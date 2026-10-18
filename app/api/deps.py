"""FastAPI dependencies: settings, a DB session per request, the current user."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api import mocks
from app.contracts.auth import UserPublic
from app.settings import Settings


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """One session per request; committed on success, rolled back on error."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> UserPublic:
    """The logged-in user from the session cookie. Raises 401 if not logged in. Week 1."""
    if settings.mock_api:
        return mocks.demo_user()
    raise NotImplementedError("Week 1: read the session cookie, look up the user, else 401")


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[UserPublic, Depends(get_current_user)]
