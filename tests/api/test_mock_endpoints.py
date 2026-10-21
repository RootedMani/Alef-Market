"""ALEF_MOCK_ENDPOINTS: example data for the listed unbuilt endpoints only, the rest stay real."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts.analysis import ChartAnnotations
from app.contracts.briefing import Briefing
from app.main import create_app
from app.services.demo import ensure_demo_user
from app.settings import Settings
from tests.conftest import make_settings

ANNOTATIONS = {"symbol": "BTC/USDT", "start_ts": 0, "as_of_ts": 1}


def test_mocks_are_off_by_default(settings: Settings) -> None:
    assert settings.mock_endpoints == ()
    assert not any(settings.mock_for(name) for name in ("annotations", "why", "briefing"))


def test_mock_api_still_means_every_endpoint(tmp_path: Path) -> None:
    s = make_settings(tmp_path, mock_api=True)
    assert all(s.mock_for(name) for name in ("annotations", "why", "briefing"))


def test_env_var_is_a_comma_separated_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALEF_MOCK_ENDPOINTS", "annotations, why,,")
    s = make_settings(tmp_path)
    assert s.mock_endpoints == ("annotations", "why")
    assert s.mock_for("why")
    assert not s.mock_for("briefing")


def test_unknown_endpoint_name_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALEF_MOCK_ENDPOINTS", "annotation")  # typo
    with pytest.raises(ValidationError):
        make_settings(tmp_path)


@pytest.fixture
def briefing_mock_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(make_settings(tmp_path, mock_endpoints=("briefing",)))
    with TestClient(app) as c:
        with app.state.session_factory() as db:  # /api/briefing needs the demo user
            ensure_demo_user(db)
            db.commit()
        yield c


def test_only_listed_endpoints_serve_mocks(briefing_mock_client: TestClient) -> None:
    res = briefing_mock_client.get("/api/briefing")
    assert res.status_code == 200
    Briefing.model_validate(res.json())

    unlisted = briefing_mock_client.get("/api/market/annotations", params=ANNOTATIONS)
    assert unlisted.status_code == 501
    assert unlisted.json()["code"] == "not_implemented"
    health = briefing_mock_client.get("/api/health").json()
    assert health["mock_api"] is False  # the all-endpoints switch is untouched


def test_listed_annotations_match_the_contract(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, mock_endpoints=("annotations",)))
    with TestClient(app) as c:
        res = c.get("/api/market/annotations", params=ANNOTATIONS)
    assert res.status_code == 200
    ChartAnnotations.model_validate(res.json())
