"""Shared fixtures. Tests never read your real .env and never touch the network."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings for tests: temp DB, no .env file, test env."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        **overrides,  # type: ignore[arg-type]
    )


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
