"""Committed seed files ``data/market/<exchange>_<BASE>-<QUOTE>_1m.csv.gz`` (A7).

Columns ``ts,open,high,low,close,volume``; ``ts`` = bar open time, Unix ms UTC. Every file
is strictly valid (aligned, no gaps) because the recorder applies the gap policy before
writing. Floats are written with full precision and gzip runs with ``mtime=0``, so
recording the same data twice gives byte-identical files (clean Git diffs).
"""

from __future__ import annotations

import gzip
import io
import logging
import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.contracts.common import Symbol
from app.contracts.frames import CANDLE_COLUMNS, validate_frame
from app.data import store

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^(?P<ex>[a-z0-9]+)_(?P<base>[A-Z0-9]+)-(?P<quote>[A-Z0-9]+)_1m\.csv\.gz$")


def seed_name(exchange: str, symbol: str) -> str:
    base, quote = symbol.split("/")
    return f"{exchange}_{base}-{quote}_1m.csv.gz"


def parse_seed_name(path: Path) -> tuple[str, Symbol]:
    """``binance_BTC-USDT_1m.csv.gz`` -> ``("binance", "BTC/USDT")``."""
    m = _NAME.match(path.name)
    if not m:
        raise ValueError(f"not a seed file name: {path.name}")
    return m["ex"], f"{m['base']}/{m['quote']}"  # type: ignore[return-value]


def write_seed(directory: Path, exchange: str, symbol: str, frame: pd.DataFrame) -> Path:
    """Write a strictly valid 1m frame as a deterministic csv.gz; returns the path."""
    validate_frame(frame, timeframe="1m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / seed_name(exchange, symbol)
    text = frame[list(CANDLE_COLUMNS)].reset_index().to_csv(index=False, float_format="%.17g")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(text.encode("utf-8"))
    path.write_bytes(buf.getvalue())
    return path


def read_seed(path: Path) -> pd.DataFrame:
    """Read a seed file as a strictly validated 1m frame (raises on gaps or bad bars)."""
    frame = pd.read_csv(path, compression="gzip", dtype={"ts": "int64"})
    frame = frame.set_index("ts").astype("float64")
    return validate_frame(frame[list(CANDLE_COLUMNS)], timeframe="1m")


def load_seed_dir(session: Session, directory: Path) -> dict[Path, int]:
    """Upsert every seed file in ``directory``; returns rows written per file."""
    loaded: dict[Path, int] = {}
    for path in sorted(directory.glob("*_1m.csv.gz")):
        exchange, symbol = parse_seed_name(path)
        frame = read_seed(path)
        loaded[path] = store.upsert_candles(session, exchange, symbol, "1m", frame)
        logger.info("loaded %s: %d bars (%s %s)", path.name, loaded[path], exchange, symbol)
    return loaded
