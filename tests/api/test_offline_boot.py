"""The offline rehearsal: no network at all, ALEF_LLM_CACHE_MODE=only, keys configured.

Sockets are disabled BEFORE the app is built, so boot is covered too. The app must start,
serve the journal, a backtest and a pinned demo parse, refuse other texts politely
(``offline``), and never raise or log an ERROR.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.backtest import BacktestResult
from app.contracts.journal import JournalSummary
from app.contracts.strategy import ParseResult
from app.data.store import upsert_candles
from app.data.synthetic import synthetic_1m
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.demo_prompts import DEMO_PROMPTS
from app.llm.normalize import normalize_text
from app.llm.tasks import TASKS
from app.main import create_app
from app.services.demo import restore_demo
from app.settings import ROOT_DIR
from tests.conftest import make_settings

FA01 = next(
    r
    for r in map(
        json.loads,
        (ROOT_DIR / "tests" / "evals" / "strategy_prompts.jsonl").read_text("utf-8").splitlines(),
    )
    if r["id"] == "fa-01"
)


def test_the_app_boots_and_serves_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    opened: list[Any] = []

    def no_network(*args: Any, **_: Any) -> Any:
        opened.append(args)
        raise OSError("network disabled for the offline rehearsal")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setenv("ALEF_LLM_CACHE_MODE", "only")  # the real env-variable path
    monkeypatch.setenv("ALEF_GROQ_KEYS", "gsk_offline_1,gsk_offline_2")  # real providers built

    data = tmp_path / "data"
    (data / "persona").mkdir(parents=True)
    shutil.copy(ROOT_DIR / "data" / "persona" / "sara_trades.json", data / "persona")
    (data / "llm").mkdir()
    pin = {
        "prompt_id": TASKS["strategy_parse"].prompt_id,
        "facts": {"text": normalize_text(DEMO_PROMPTS[0])},
        "output": json.dumps({"status": "ok", "strategy": FA01["strategy"], "unsupported": []}),
        "note": "demo prompt 1 (test)",
    }
    (data / "llm" / "pinned.jsonl").write_text(json.dumps(pin, ensure_ascii=False) + "\n")

    settings = make_settings(tmp_path, data_dir=data, exchange="binance")
    assert settings.llm_cache_mode == "only" and settings.market_mode == "replay"
    engine = make_engine(settings.db_path)
    create_schema(engine)
    with session_scope(make_session_factory(engine)) as db:
        t0 = 1_790_812_800_000
        upsert_candles(
            db, "binance", "BTC/USDT", "1m", synthetic_1m("BTC/USDT", t0, 3 * 1440, seed=3)
        )
        assert restore_demo(db, settings) > 100
    engine.dispose()

    with caplog.at_level(logging.WARNING), TestClient(create_app(settings)) as c:
        journal = JournalSummary.model_validate(c.get("/api/journal").json())
        assert len(journal.insights) == 3 and journal.latest_review is not None
        assert {i.text.meta.source for i in journal.insights} == {"template"}

        res = c.post("/api/backtest/run", json={"strategy": FA01["strategy"]})
        assert res.status_code == 200, res.text
        run = BacktestResult.model_validate(res.json())
        assert run.summary is not None and run.summary.meta.source == "template"

        retyped = DEMO_PROMPTS[0].replace("30", "۳۰") + "."
        parsed = ParseResult.model_validate(
            c.post("/api/backtest/parse", json={"text": retyped}).json()
        )
        assert parsed.status == "ok" and parsed.meta is not None
        assert parsed.meta.provider == "pinned" and parsed.readback_fa

        other = ParseResult.model_validate(
            c.post("/api/backtest/parse", json={"text": "buy when RSI < 25"}).json()
        )
        assert other.status == "offline" and other.message_fa is not None
        assert DEMO_PROMPTS[0] in other.message_fa  # pinned, so suggested
        assert DEMO_PROMPTS[1] not in other.message_fa  # not pinned, not suggested

        body = {"strategy": parsed.strategy.model_dump(mode="json"), "text": "حد ضرر رو 3 درصد کن"}  # type: ignore[union-attr]
        assert c.post("/api/backtest/edit", json=body).json()["status"] == "offline"

    assert opened == []  # nothing even tried to connect
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
