"""Shared fixtures. Tests never read your real .env and never touch the network."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.engine import create_schema, make_engine, make_session_factory
from app.main import create_app
from app.settings import Settings

PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


@pytest.fixture(autouse=True)
def _no_developer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell's ALEF_* and proxy variables must not reach Settings or httpx in tests."""
    for name in list(os.environ):
        if name.upper().startswith("ALEF_") or name.upper() in PROXY_ENV:
            monkeypatch.delenv(name)


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings for tests: temp DB, no .env file, test env."""
    values: dict[str, object] = {
        "env": "test",
        "db_path": tmp_path / "test.db",
        "log_dir": tmp_path / "logs",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """The real app (startup included) with mocks OFF."""
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def mock_client(tmp_path: Path) -> Iterator[TestClient]:
    """The real app with ALEF_MOCK_API=true."""
    with TestClient(create_app(make_settings(tmp_path, mock_api=True))) as c:
        yield c


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    """A session on a fresh temp SQLite DB with the full schema."""
    engine = make_engine(tmp_path / "unit.db")
    create_schema(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()
    engine.dispose()
