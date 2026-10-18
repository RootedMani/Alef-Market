"""Session tokens. Planned: Week 1."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.orm import Session

from app.contracts.auth import UserPublic


def new_token() -> str:
    """A URL-safe random token for the session cookie."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """What the DB stores instead of the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user_id: int, *, days: int) -> str:
    """Store a session row and return the raw token for the cookie."""
    raise NotImplementedError("Week 1")


def user_for_token(db: Session, token: str) -> UserPublic | None:
    """The logged-in user, or None if the token is unknown or expired."""
    raise NotImplementedError("Week 1")


def delete_session(db: Session, token: str) -> None:
    raise NotImplementedError("Week 1")
