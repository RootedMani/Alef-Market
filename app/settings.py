"""Application settings, loaded once from environment variables and `.env`.

Every setting has the prefix ``ALEF_`` in the environment, e.g. ``ALEF_MARKET_MODE=live``.
Secrets (API keys) are ``SecretStr`` so they never show up in logs, reprs or tracebacks.

Usage::

    from app.settings import get_settings
    settings = get_settings()
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

ProviderName = Literal["groq", "gemini", "openrouter"]
ProxyClient = Literal["llm", "exchange", "news"]
# Endpoints that can serve mocks/example data on their own (ALEF_MOCK_ENDPOINTS): the unbuilt ones.
MockEndpoint = Literal["annotations", "why", "briefing"]
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


def describe_proxy(proxy: str | None) -> str:
    """``scheme://host`` of a proxy URL for logs: never its credentials, port or path.

    ``"none"`` when unset, ``"invalid"`` when the scheme isn't one we know (a URL without a
    scheme, like ``user:pw@host:1``, would otherwise parse ``user`` as the scheme).
    """
    if not proxy:
        return "none"
    try:
        parts = urlsplit(proxy)
        host = parts.hostname
    except ValueError:
        return "invalid"
    scheme = parts.scheme.lower()
    if scheme not in PROXY_SCHEMES or not host:
        return "invalid"
    return f"{scheme}://{host}"


@dataclass(frozen=True)
class ApiKey:
    """One API key plus a safe alias (e.g. ``groq#2``) used in logs instead of the key."""

    provider: ProviderName
    alias: str
    value: str

    def __repr__(self) -> str:  # never print the secret
        return f"ApiKey(provider={self.provider!r}, alias={self.alias!r})"


def _split_keys(provider: ProviderName, raw: SecretStr) -> list[ApiKey]:
    """Turn ``"k1, k2,,k3"`` into ApiKey objects, skipping blanks."""
    parts = [p.strip() for p in raw.get_secret_value().split(",")]
    return [
        ApiKey(provider=provider, alias=f"{provider}#{i}", value=p)
        for i, p in enumerate((p for p in parts if p), start=1)
    ]


class Settings(BaseSettings):
    """All configuration in one typed object. Defaults are safe for the demo laptop."""

    model_config = SettingsConfigDict(
        env_prefix="ALEF_",
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- app ---
    env: Literal["dev", "demo", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path = ROOT_DIR / "logs"
    display_tz: str = "Asia/Tehran"
    mock_api: bool = Field(
        default=False,
        description="Serve example data from app/api/mocks.py for every endpoint.",
    )
    # Per endpoint, e.g. ALEF_MOCK_ENDPOINTS=annotations,why: only these serve example data.
    mock_endpoints: Annotated[tuple[MockEndpoint, ...], NoDecode] = ()

    # --- storage ---
    db_path: Path = ROOT_DIR / "var" / "alef.db"
    data_dir: Path = ROOT_DIR / "data"

    # --- market data ---
    market_mode: Literal["replay", "live"] = "replay"
    exchange: str = "binance"  # chosen in Week 0 by the connectivity test (D2)
    exchange_timeout_ms: int = 10_000
    exchange_proxy: str | None = None  # e.g. http://127.0.0.1:10809 if the exchange needs one
    replay_exchange: str | None = None  # whose seed files replay reads; None = same as exchange
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")

    # --- news ---
    news_proxy: str | None = None  # e.g. http://127.0.0.1:10809 if the RSS feeds need one

    # --- auth ---
    session_cookie_name: str = "alef_session"
    session_days: int = 14
    cookie_secure: bool = False  # True once served over HTTPS

    # --- LLM gateway ---
    llm_cache_mode: Literal["prefer", "fallback", "only"] = "fallback"
    llm_timeout_s: float = 8.0
    llm_proxy: str | None = None  # e.g. http://127.0.0.1:10808; separate from exchange_proxy
    llm_provider_order: tuple[ProviderName, ...] = ("groq", "gemini", "openrouter")
    # Per-task override, e.g. {"strategy_parse": ["gemini", "groq"]}: those providers first,
    # the rest of llm_provider_order after them. Set from the probe/eval results.
    llm_task_providers: dict[str, tuple[ProviderName, ...]] = Field(default_factory=dict)
    groq_keys: SecretStr = SecretStr("")
    gemini_keys: SecretStr = SecretStr("")
    openrouter_keys: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-2.5-flash"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    @field_validator("mock_endpoints", mode="before")
    @classmethod
    def _split_mock_endpoints(cls, value: object) -> object:
        """``"annotations, why"`` -> ``("annotations", "why")``; lists pass through."""
        if isinstance(value, str):
            return tuple(p.strip() for p in value.split(",") if p.strip())
        return value

    def mock_for(self, endpoint: MockEndpoint) -> bool:
        """True if this endpoint serves example data: ``ALEF_MOCK_API`` or listed by name."""
        return self.mock_api or endpoint in self.mock_endpoints

    @property
    def replay_source_exchange(self) -> str:
        """The exchange key replay mode reads from the candles table."""
        return self.replay_exchange or self.exchange

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def api_keys(self, provider: ProviderName) -> list[ApiKey]:
        """All configured keys for one provider, in the order given in .env."""
        raw = {
            "groq": self.groq_keys,
            "gemini": self.gemini_keys,
            "openrouter": self.openrouter_keys,
        }[provider]
        return _split_keys(provider, raw)

    def proxy_for(self, client: ProxyClient) -> str | None:
        """The proxy one HTTP client uses: its own ``ALEF_*_PROXY``, or None.

        Every client is built with this and with the environment lookup off, so the shell's
        ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY`` never reach it.
        """
        proxy = {
            "llm": self.llm_proxy,
            "exchange": self.exchange_proxy,
            "news": self.news_proxy,
        }[client]
        return proxy or None

    def proxy_summary(self) -> str:
        """``llm=http://127.0.0.1 exchange=none news=none``: for the startup log."""
        return " ".join(f"{c}={describe_proxy(self.proxy_for(c))}" for c in get_args(ProxyClient))


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings. Tests call ``get_settings.cache_clear()`` to reload."""
    return Settings()
