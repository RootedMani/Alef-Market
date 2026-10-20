"""App factory. Run with ``make dev`` (uvicorn --factory app.main:create_app --reload)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.errors import install_error_handlers
from app.api.routes import ROUTERS
from app.db.engine import create_schema, make_engine, make_session_factory, session_scope
from app.llm.cache import load_pinned_safely
from app.llm.gateway import DB_BUSY_TIMEOUT_MS, build_gateway
from app.logging_setup import setup_logging
from app.settings import Settings, get_settings
from app.web.pages import STATIC_DIR
from app.web.pages import router as pages_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own ``Settings`` (temp DB, mock mode)."""
    settings = settings or get_settings()
    setup_logging(settings)

    engine = make_engine(settings.db_path)
    # The gateway gets its own engine (short busy timeout) for llm_calls and the cache.
    llm_engine = make_engine(settings.db_path, busy_timeout_ms=DB_BUSY_TIMEOUT_MS)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        create_schema(engine)  # no network calls at startup: the demo must boot offline
        with session_scope(app.state.session_factory) as session:
            load_pinned_safely(session, settings)  # reset-demo deletes the DB; the file doesn't
        logger.info(
            "Alef Market %s started (env=%s, market=%s, mock_api=%s, db=%s)",
            __version__,
            settings.env,
            settings.market_mode,
            settings.mock_api,
            settings.db_path,
        )
        yield
        engine.dispose()
        llm_engine.dispose()

    app = FastAPI(title="Alef Market API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.llm_engine = llm_engine
    app.state.gateway = build_gateway(settings, make_session_factory(llm_engine))  # no network

    install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router, prefix="/api")
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
