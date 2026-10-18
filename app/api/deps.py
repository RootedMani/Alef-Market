"""FastAPI dependencies: settings, a DB session per request, the current user."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import mocks
from app.contracts.auth import UserPublic
from app.services.demo import demo_user
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
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> UserPublic:
    """The logged-in user. Raises 401 if there is none.

    TEMPORARY (Chat 5, until the auth chat builds sessions): outside ``prod`` every request
    acts as the demo user (Sara), so the journal and paper trades work end to end. The auth
    chat replaces this body with the cookie lookup (``auth.sessions.user_for_token``) and
    keeps the 401.
    """
    if settings.mock_api:
        return mocks.demo_user()
    if settings.env == "prod":
        raise HTTPException(status_code=401)
    user = demo_user(db)
    if user is None:
        raise HTTPException(status_code=401, detail="no demo user; run make reset-demo")
    return user


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[UserPublic, Depends(get_current_user)]
