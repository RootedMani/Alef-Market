"""Live evals share one gateway built from ``.env`` (skipped when no key is configured)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.db.engine import create_schema, make_engine, make_session_factory
from app.llm.gateway import LLMGateway, build_gateway
from app.settings import ROOT_DIR, Settings


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LLMGateway]:
    tmp = tmp_path_factory.mktemp("evals")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=ROOT_DIR / ".env", db_path=tmp / "evals.db", llm_cache_mode="fallback"
    )
    if not any(settings.api_keys(name) for name in settings.llm_provider_order):
        pytest.skip("no LLM keys in .env (ALEF_GROQ_KEYS / ALEF_GEMINI_KEYS / ...)")
    engine = make_engine(settings.db_path)
    create_schema(engine)
    yield build_gateway(settings, make_session_factory(engine))
    engine.dispose()
