from fastapi.testclient import TestClient

from app.contracts.common import Health


def test_health_is_ok(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    health = Health.model_validate(res.json())
    assert health.status == "ok"
    assert health.db_ok is True
    assert health.market_mode == "replay"
    assert health.mock_api is False


def test_home_page_renders_rtl_persian(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert 'dir="rtl"' in res.text
    assert 'dir="ltr"' in res.text  # the chart container
    assert "توصیه مالی نیست" in res.text  # the permanent disclaimer


def test_vendored_assets_are_served_locally(client: TestClient) -> None:
    for path in (
        "/static/vendor/lightweight-charts-5.2.1.js",
        "/static/vendor/htmx-2.0.11.min.js",
        "/static/fonts/Vazirmatn-Variable.woff2",
        "/static/css/app.css",
        "/static/js/chart.js",
    ):
        assert client.get(path).status_code == 200, path


def test_openapi_schema_builds(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/backtest/run" in schema["paths"]
    assert any(name.startswith("Strategy") for name in schema["components"]["schemas"])
