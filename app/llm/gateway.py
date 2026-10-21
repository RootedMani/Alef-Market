"""The LLM gateway: the ONLY way the app talks to an LLM (D5, D6, §8). It never raises.

``run_json`` / ``run_text`` walk one ladder and always return an ``LLMOutcome``::

    key = cache_key(task.prompt_id, facts)
    cached = the cache row for key, re-validated (an invalid row is never served)
    if cached is pinned, or cache_mode is prefer/only and cached:   return cached   (source=cache)
    if cache_mode == "only":                                         return fallback (no network)
    for provider in the task's order (llm_task_providers, then llm_provider_order):
        skip it while it cools down (a timeout, a 5xx or a network error: PROVIDER_COOLDOWN_S)
        for key in its keys, round-robin (each call starts at the next key):
            skip disabled keys (401/403: for the whole process) and cooling keys (429: Retry-After)
            stop the whole ladder if less than MIN_ATTEMPT_S of task.deadline_s is left
            raw = complete(...)        attempt timeout = min(time left, llm_timeout_s)
              429 / auth -> next key;  timeout, 5xx, network -> cool provider, next provider
              any other 4xx (e.g. 400) -> next provider, no cooldown
            validate raw (validate.validate_output: the same check cache hits and pins pass)
              invalid -> retry ONCE on this key with the answer and the problem list
                         appended (plus the task's retry hint), then next provider
              valid   -> store in the cache (never over a pinned row); return  (source=llm)
    cached (fallback mode)   -> return cached                                  (source=cache)
    else                     -> return fallback()                              (source=template)

A call may override the cache mode (``mode=``), except that a global "only" always wins: the
journal reads texts with "only" during a request and ``fill``s misses afterwards with "prefer"
(one fill per cache key at a time); the backtest summary uses "prefer".

One gateway lives per process (``app.state.gateway``) and is called from FastAPI's thread
pool, so key/provider state and the usage counters sit behind one lock. Every attempt becomes
an ``llm_calls`` row (key alias, outcome, latency), written in the gateway's own short session
after the ladder; a failed DB read or write is a WARNING, never an exception. Logs and rows
carry key aliases like ``groq#2``, never keys.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.contracts.common import TextMeta
from app.db.tables import LLMCallRow
from app.llm.cache import CacheHit, cache_key, canonical_json, get_cached, put_cached
from app.llm.providers import DEFAULT_RETRY_AFTER_S, LLMProvider, ProviderError, build_providers
from app.llm.tasks import TASKS, PromptTask, system_prompt
from app.llm.validate import OutputInvalid, validate_output
from app.settings import ProviderName, Settings

logger = logging.getLogger(__name__)

CacheMode = Literal["prefer", "fallback", "only"]
Outcome = Literal["ok", "rate_limited", "timeout", "http_error", "auth", "invalid_output"]
Facts = BaseModel | dict[str, Any]

# The gateway's sessions use their own engine with this busy timeout (the app's is 5 s): if a
# request session holds the write lock, an llm_calls/cache write gives up fast (a WARNING)
# instead of stalling the answer.
DB_BUSY_TIMEOUT_MS = 200
MIN_ATTEMPT_S = 1.0  # don't start an attempt with less time than this left: use the fallback
PROVIDER_COOLDOWN_S = 60.0  # after a timeout, a 5xx or a network error
_MAX_ECHO_CHARS = 1500  # how much of a rejected answer is shown back on the retry


@dataclass(frozen=True)
class Attempt:
    key_alias: str
    outcome: Outcome
    latency_ms: int


@dataclass(frozen=True)
class LLMOutcome[T]:
    """``value`` is the validated model (JSON tasks) or the text (prose tasks); never None."""

    value: T
    meta: TextMeta
    attempts: list[Attempt] = field(default_factory=list)


def user_message(facts: Facts) -> str:
    """The facts as JSON between ``<facts>`` tags (the system prompt says: data, not orders).

    Only ``</`` is escaped, as ``<\\/`` (a legal JSON escape for the same string), so text a
    user typed can't close the block, while ``RSI < 30`` and ``"op": "<"`` stay as written.
    """
    data = facts.model_dump(mode="json") if isinstance(facts, BaseModel) else facts
    body = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f"<facts>\n{body}\n</facts>"


def retry_message(first: str, raw: str, problems: list[str], hint: str) -> str:
    """The original message plus the rejected answer, what was wrong with it and the hint."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{first}\n\nYour previous answer was rejected.\n"
        f"Previous answer:\n{raw[:_MAX_ECHO_CHARS]}\n\nProblems:\n{listed}\n\n{hint}"
    ).rstrip()


class _Next(Enum):
    KEY = auto()
    PROVIDER = auto()
    STOP = auto()  # out of time


@dataclass(frozen=True)
class _Answer:
    value: Any
    raw: str
    alias: str


class LLMGateway:
    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        session_factory: Callable[[], Session],
        cache_mode: CacheMode,
        attempt_timeout_s: float = 8.0,
        task_providers: dict[str, tuple[ProviderName, ...]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.providers = list(providers)
        self.cache_mode = cache_mode
        self.attempt_timeout_s = attempt_timeout_s
        self.task_providers = dict(task_providers or {})
        self._session_factory = session_factory
        self._clock = clock
        self._wall_ms = wall_ms
        self._groups: dict[str, list[LLMProvider]] = {}
        for p in self.providers:
            self._groups.setdefault(p.provider, []).append(p)
        self._lock = threading.Lock()
        self._disabled: set[str] = set()  # aliases with a 401/403
        self._key_until: dict[str, float] = {}  # alias -> clock time a 429 cooldown ends
        self._provider_until: dict[str, float] = {}  # provider -> clock time its cooldown ends
        self._next_key: dict[str, int] = {}  # provider -> round-robin counter
        self._usage: Counter[tuple[str, str]] = Counter()
        self._in_flight: set[str] = set()  # cache keys being filled in the background

    # ---------- public ----------

    def run_json[M: BaseModel](
        self,
        task: PromptTask,
        facts: Facts,
        *,
        fallback: Callable[[], M],
        mode: CacheMode | None = None,
    ) -> LLMOutcome[M]:
        """Run a JSON task; ``fallback`` builds the refusal value. Never raises.

        ``mode`` overrides the cache mode for this call, except that a global "only" always
        wins (an offline rehearsal never touches the network).
        """
        return cast(LLMOutcome[M], self._run(task, facts, fallback, mode))

    def run_text(
        self,
        task: PromptTask,
        facts: Facts,
        *,
        fallback: Callable[[], str],
        mode: CacheMode | None = None,
    ) -> LLMOutcome[str]:
        """Run a prose task; ``fallback`` renders the Persian template. Never raises."""
        return cast(LLMOutcome[str], self._run(task, facts, fallback, mode))

    def fill(self, task: PromptTask, facts: Facts, *, fallback: Callable[[], Any]) -> bool:
        """Get a cached answer for these facts, calling the LLM only on a miss ("prefer").

        For background work after a response (journal texts). A text already being filled by
        another thread is skipped: returns False. Never raises.
        """
        try:
            key = cache_key(task.prompt_id, facts)
        except Exception:
            logger.exception("llm %s: facts can't be keyed; nothing to fill", task.prompt_id)
            return False
        with self._lock:
            if key in self._in_flight:
                return False
            self._in_flight.add(key)
        try:
            self._run(task, facts, fallback, "prefer")
        finally:
            with self._lock:
                self._in_flight.discard(key)
        return True

    def order_for(self, task: PromptTask) -> list[LLMProvider]:
        """Every key in the order this task tries them (before round-robin rotation)."""
        return [p for name in self._provider_names(task) for p in self._groups[name]]

    def is_pinned(self, task: PromptTask, facts: Facts) -> bool:
        """True if a valid pinned answer exists for these facts (served in every mode)."""
        cached = self._read_cache(task, cache_key(task.prompt_id, facts), facts)
        return cached is not None and cached[1].pinned

    def usage(self) -> dict[tuple[str, str], int]:
        """Attempts so far in this process, by (key alias, outcome)."""
        with self._lock:
            return dict(self._usage)

    # ---------- the ladder ----------

    def _run(
        self,
        task: PromptTask,
        facts: Facts,
        fallback: Callable[[], Any],
        mode: CacheMode | None = None,
    ) -> LLMOutcome[Any]:
        mode = "only" if self.cache_mode == "only" else (mode or self.cache_mode)
        try:
            return self._ladder(task, facts, fallback, mode)
        except Exception:
            logger.exception("llm %s: gateway error; using the template", task.prompt_id)
            return self._template(task, fallback, [])

    def _ladder(
        self, task: PromptTask, facts: Facts, fallback: Callable[[], Any], mode: CacheMode
    ) -> LLMOutcome[Any]:
        deadline = self._clock() + task.deadline_s
        key = cache_key(task.prompt_id, facts)
        cached = self._read_cache(task, key, facts)
        if cached is not None and (cached[1].pinned or mode in ("prefer", "only")):
            return self._from_cache(task, cached, [])
        if mode == "only":
            return self._template(task, fallback, [])

        attempts: list[Attempt] = []
        answer = self._walk(task, facts, deadline, attempts)
        self._write_calls(task, attempts)
        if answer is not None:
            self._write_cache(task, key, facts, answer)
            logger.info(
                "llm %s answered by %s (%d attempts, %d ms)",
                task.prompt_id,
                answer.alias,
                len(attempts),
                sum(a.latency_ms for a in attempts),
            )
            meta = TextMeta(source="llm", prompt=task.prompt_id, provider=answer.alias)
            return LLMOutcome(answer.value, meta, attempts)
        if cached is not None:
            logger.warning("llm %s: no live answer; serving the cache", task.prompt_id)
            return self._from_cache(task, cached, attempts)
        return self._template(task, fallback, attempts)

    def _walk(
        self, task: PromptTask, facts: Facts, deadline: float, attempts: list[Attempt]
    ) -> _Answer | None:
        system, user = system_prompt(task.name), user_message(facts)
        for name in self._provider_names(task):
            if self._provider_cooling(name):
                continue
            for p in self._rotation(name):
                if not self._key_usable(p.alias):
                    continue
                result = self._try_key(task, p, system, user, facts, deadline, attempts)
                if isinstance(result, _Answer):
                    return result
                if result is _Next.STOP:
                    return None
                if result is _Next.PROVIDER:
                    break
        return None

    def _try_key(
        self,
        task: PromptTask,
        p: LLMProvider,
        system: str,
        user: str,
        facts: Facts,
        deadline: float,
        attempts: list[Attempt],
    ) -> _Answer | _Next:
        message, retried = user, False
        while True:
            left = deadline - self._clock()
            if left < MIN_ATTEMPT_S:
                logger.warning("llm %s: %.1f s left, no new attempt", task.prompt_id, left)
                return _Next.STOP
            started = self._clock()
            try:
                raw = p.complete(
                    system,
                    message,
                    json_mode=task.output == "json",
                    timeout_s=min(left, self.attempt_timeout_s),
                    max_tokens=task.max_tokens,
                )
            except ProviderError as exc:
                self._record(attempts, p.alias, exc.kind, started)
                logger.warning("llm %s: %s %s", task.prompt_id, p.alias, exc)
                return self._after_failure(p, exc)
            except Exception as exc:  # a provider bug must not escape either
                self._record(attempts, p.alias, "http_error", started)
                logger.warning("llm %s: %s raised %r", task.prompt_id, p.alias, exc)
                return _Next.PROVIDER
            try:
                value = validate_output(task, raw, facts)
            except OutputInvalid as exc:
                self._record(attempts, p.alias, "invalid_output", started)
                logger.warning(
                    "llm %s: %s invalid output: %s", task.prompt_id, p.alias, exc.problems
                )
                if retried:
                    return _Next.PROVIDER
                retried = True
                message = retry_message(user, raw, exc.problems, task.retry_hint)
                continue
            self._record(attempts, p.alias, "ok", started)
            return _Answer(value=value, raw=raw, alias=p.alias)

    # ---------- key and provider state (shared across threads) ----------

    def _provider_names(self, task: PromptTask) -> list[str]:
        first = [n for n in self.task_providers.get(task.name, ()) if n in self._groups]
        return first + [n for n in self._groups if n not in first]

    def _rotation(self, name: str) -> list[LLMProvider]:
        keys = self._groups[name]
        with self._lock:
            start = self._next_key.get(name, 0) % len(keys)
            self._next_key[name] = start + 1
        return keys[start:] + keys[:start]

    def _provider_cooling(self, name: str) -> bool:
        with self._lock:
            return self._provider_until.get(name, 0.0) > self._clock()

    def _key_usable(self, alias: str) -> bool:
        with self._lock:
            return alias not in self._disabled and self._key_until.get(alias, 0.0) <= self._clock()

    def _after_failure(self, p: LLMProvider, exc: ProviderError) -> _Next:
        now = self._clock()
        with self._lock:
            if exc.kind == "auth":
                self._disabled.add(p.alias)
                logger.warning("llm key %s rejected (auth): disabled until restart", p.alias)
                return _Next.KEY
            if exc.kind == "rate_limited":
                wait = exc.retry_after_s if exc.retry_after_s is not None else DEFAULT_RETRY_AFTER_S
                self._key_until[p.alias] = now + wait
                return _Next.KEY
            if exc.kind == "timeout" or exc.status is None or exc.status >= 500:
                self._provider_until[p.provider] = now + PROVIDER_COOLDOWN_S
                logger.warning(
                    "llm provider %s cooling down %.0f s", p.provider, PROVIDER_COOLDOWN_S
                )
            return _Next.PROVIDER  # a 400/404 is the same request and model on every key

    def _record(
        self, attempts: list[Attempt], alias: str, outcome: Outcome, started: float
    ) -> None:
        latency_ms = max(0, int((self._clock() - started) * 1000))
        attempts.append(Attempt(key_alias=alias, outcome=outcome, latency_ms=latency_ms))
        with self._lock:
            self._usage[(alias, outcome)] += 1

    # ---------- results ----------

    def _from_cache(
        self, task: PromptTask, cached: tuple[Any, CacheHit], attempts: list[Attempt]
    ) -> LLMOutcome[Any]:
        value, hit = cached
        meta = TextMeta(source="cache", prompt=task.prompt_id, provider=hit.provider)
        return LLMOutcome(value, meta, attempts)

    def _template(
        self, task: PromptTask, fallback: Callable[[], Any], attempts: list[Attempt]
    ) -> LLMOutcome[Any]:
        if attempts:
            logger.warning(
                "llm %s: no valid answer after %s; using the template",
                task.prompt_id,
                [(a.key_alias, a.outcome) for a in attempts],
            )
        meta = TextMeta(source="template", prompt=task.prompt_id, provider=None)
        return LLMOutcome(fallback(), meta, attempts)

    # ---------- DB (own short sessions; failures are warnings) ----------

    def _read_cache(self, task: PromptTask, key: str, facts: Facts) -> tuple[Any, CacheHit] | None:
        try:
            with self._session_factory() as session:
                hit = get_cached(session, key)
        except Exception as exc:
            logger.warning("llm %s: cache read failed: %s", task.prompt_id, exc)
            return None
        if hit is None:
            return None
        try:
            return validate_output(task, hit.output_text, facts), hit
        except OutputInvalid as exc:
            logger.warning(
                "llm %s: cached answer (%s) fails validation, not served: %s",
                task.prompt_id,
                hit.provider,
                exc.problems,
            )
            return None

    def _write_calls(self, task: PromptTask, attempts: list[Attempt]) -> None:
        if not attempts:
            return
        ts = self._wall_ms()
        try:
            with self._session_factory() as session:
                session.add_all(
                    LLMCallRow(
                        ts=ts,
                        task=task.prompt_id,
                        key_alias=a.key_alias,
                        outcome=a.outcome,
                        latency_ms=a.latency_ms,
                    )
                    for a in attempts
                )
                session.commit()
        except Exception as exc:
            logger.warning("llm %s: could not write llm_calls: %s", task.prompt_id, exc)

    def _write_cache(self, task: PromptTask, key: str, facts: Facts, answer: _Answer) -> None:
        try:
            with self._session_factory() as session:
                put_cached(
                    session,
                    key=key,
                    task=task.prompt_id,
                    input_json=canonical_json(facts),
                    output_text=answer.raw.strip(),
                    provider=answer.alias,
                    created_ts=self._wall_ms(),
                )
                session.commit()
        except Exception as exc:
            logger.warning("llm %s: could not write the cache: %s", task.prompt_id, exc)


def build_gateway(
    settings: Settings,
    session_factory: Callable[[], Session],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> LLMGateway:
    """The process-wide gateway from settings (keys, order, cache mode, timeouts)."""
    unknown = set(settings.llm_task_providers) - set(TASKS)
    if unknown:
        logger.warning("ALEF_LLM_TASK_PROVIDERS names unknown tasks: %s", sorted(unknown))
    providers = build_providers(settings)
    logger.info(
        "llm gateway: %d keys (%s), cache mode %s",
        len(providers),
        ", ".join(p.alias for p in providers) or "none",
        settings.llm_cache_mode,
    )
    return LLMGateway(
        list(providers),
        session_factory=session_factory,
        cache_mode=settings.llm_cache_mode,
        attempt_timeout_s=settings.llm_timeout_s,
        task_providers=settings.llm_task_providers,
        clock=clock,
    )
