"""Logging: readable lines on the console + a rotating file in ``logs/app.log``.

Use ``logger = logging.getLogger(__name__)`` in every module. Never log API keys,
passwords or session tokens; log key aliases like ``groq#2`` instead.
"""

from __future__ import annotations

import logging
import logging.handlers

from app.settings import Settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(settings: Settings) -> None:
    """Configure the root logger once. Safe to call more than once."""
    root = logging.getLogger()
    if getattr(root, "_alef_configured", False):
        return
    root.setLevel(settings.log_level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    if settings.env != "test":
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            settings.log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)

    # Third-party loggers that are too chatty at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root._alef_configured = True  # type: ignore[attr-defined]
