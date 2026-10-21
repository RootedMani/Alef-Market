"""Display formatting for the pages: Tehran wall-clock times (UTC ms underneath), prices, %.

Latin digits everywhere. Registered as Jinja filters in ``app.web.pages``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape


def _local(ts: int, tz_name: str) -> datetime:
    return datetime.fromtimestamp(ts / 1000, UTC).astimezone(ZoneInfo(tz_name))


def tz_offset_ms(tz_name: str, ts: int) -> int:
    """The zone's UTC offset at ``ts`` (Tehran: +03:30 = 12,600,000 ms)."""
    offset = _local(ts, tz_name).utcoffset()
    return int(offset.total_seconds() * 1000) if offset else 0


def fmt_clock(ts: int, tz_name: str) -> str:
    return _local(ts, tz_name).strftime("%H:%M")


def fmt_date(ts: int, tz_name: str) -> str:
    return _local(ts, tz_name).strftime("%Y-%m-%d")


def fmt_price(value: float) -> str:
    """``121,269`` for BTC-sized prices, ``3,710.81`` below 10,000."""
    return f"{value:,.0f}" if value >= 10_000 else f"{value:,.2f}"


def fmt_signed(value: float) -> str:
    """``-1,538.13`` / ``+10.56``: a USDT amount with its sign and thousands separators."""
    return f"{value:+,.2f}"


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


# Latin runs inside Persian text (RSI(14), EMA 200, BTC/USDT): kept LTR so bidi can't scramble.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9_/]*(?:\(\d+\))?(?: \d+(?:\.\d+)?)?")


def ltr_runs(text: str | None) -> Markup:
    """Escape ``text`` and wrap each Latin run in ``<bdi dir="ltr">``."""
    out: list[str] = []
    last = 0
    for m in _LATIN_RUN.finditer(text or ""):
        out.append(str(escape(text[last : m.start()])))  # type: ignore[index]
        out.append(f'<bdi dir="ltr">{escape(m.group(0))}</bdi>')
        last = m.end()
    out.append(str(escape((text or "")[last:])))
    return Markup("".join(out))
