"""Execution policy: budgets, rate limiting, retries, timeouts, caching and per-case metering."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import random
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentbench.config import TargetConfig
from agentbench.core.types import (
    AgentReply,
    BudgetExceeded,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ToolCall,
    Usage,
)
from agentbench.engine.tracing import Tracer
from agentbench.providers.base import AgentTarget, Provider

# ------------------------------------------------------------------------------------------------
# metering
# ------------------------------------------------------------------------------------------------


@dataclass
class Meter:
    """Accumulates everything a single case consumed, across all model calls it made."""

    calls: int = 0
    attempts: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_latency_ms: float = 0.0
    first_ttft_ms: float | None = None
    cached_calls: int = 0
    models: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    attempt_error_counts: dict[str, int] = field(default_factory=dict)
    queue_wait_ms: float = 0.0
    api_latency_ms: list[float] = field(default_factory=list)
    peak_api_concurrency: int = 0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_cost_usd: float = 0.0


_meter: contextvars.ContextVar[Meter | None] = contextvars.ContextVar("agentbench_meter", default=None)


def start_meter() -> tuple[Meter, contextvars.Token]:
    m = Meter()
    return m, _meter.set(m)


def stop_meter(token: contextvars.Token) -> None:
    _meter.reset(token)


def current_meter() -> Meter | None:
    return _meter.get()


# ------------------------------------------------------------------------------------------------
# budget & rate limits
# ------------------------------------------------------------------------------------------------


class Budget:
    def __init__(self, max_usd: float | None, max_tokens: int | None, max_requests: int | None) -> None:
        self.max_usd, self.max_tokens, self.max_requests = max_usd, max_tokens, max_requests
        self.spent_usd = 0.0
        self.tokens = 0
        self.requests = 0
        self.exceeded_reason: str | None = None

    def check(self) -> None:
        if self.exceeded_reason:
            raise BudgetExceeded(self.exceeded_reason)
        if self.max_usd is not None and self.spent_usd >= self.max_usd:
            self.exceeded_reason = f"spend limit reached (${self.spent_usd:.4f} >= ${self.max_usd})"
        elif self.max_tokens is not None and self.tokens >= self.max_tokens:
            self.exceeded_reason = f"token limit reached ({self.tokens} >= {self.max_tokens})"
        elif self.max_requests is not None and self.requests >= self.max_requests:
            self.exceeded_reason = f"request limit reached ({self.requests} >= {self.max_requests})"
        if self.exceeded_reason:
            raise BudgetExceeded(self.exceeded_reason)

    def record(self, usage: Usage, cost: float) -> None:
        self.requests += 1
        self.tokens += usage.input_tokens + usage.output_tokens
        self.spent_usd += cost

    def snapshot(self) -> dict[str, Any]:
        return {
            "spent_usd": round(self.spent_usd, 6),
            "tokens": self.tokens,
            "requests": self.requests,
            "limits": {
                "max_usd": self.max_usd,
                "max_tokens": self.max_tokens,
                "max_requests": self.max_requests,
            },
            "exceeded": self.exceeded_reason,
        }


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float | None = None) -> None:
        self.rate = rate_per_s
        self.capacity = burst or max(1.0, rate_per_s)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


# ------------------------------------------------------------------------------------------------
# response cache
# ------------------------------------------------------------------------------------------------


class ResponseCache:
    """SQLite cache for deterministic requests (temperature 0) to save money during development.

    Cached responses are flagged and excluded from latency statistics.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT, created REAL)"
        )

    @staticmethod
    def key(target: TargetConfig, req: ModelRequest) -> str:
        payload = {
            "provider": target.provider,
            "model": target.model,
            "params": target.params,
            "system": req.system,
            "messages": [asdict(m) for m in req.messages],
            "tools": [asdict(t) for t in req.tools],
            "temperature": req.temperature,
            "max_output_tokens": req.max_output_tokens,
            "format": req.response_format,
            "meta": {k: v for k, v in req.metadata.items() if k != "attempt"},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def get(self, key: str) -> ModelResponse | None:
        row = self._db.execute("SELECT body FROM responses WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        d = json.loads(row[0])
        return ModelResponse(
            text=d["text"],
            tool_calls=[ToolCall(**tc) for tc in d["tool_calls"]],
            usage=Usage(**d["usage"]),
            model=d["model"],
            finish_reason=d.get("finish_reason"),
            cached=True,
        )

    def put(self, key: str, resp: ModelResponse) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
            (key, json.dumps(resp.to_dict(), default=str), time.time()),
        )
        self._db.commit()


# ------------------------------------------------------------------------------------------------
# managed provider
# ------------------------------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    timeout_s: float = 30.0
    max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0

    def delay(self, attempt: int, err: ProviderError) -> float:
        d = min(self.backoff_max_s, self.backoff_base_s * (2 ** (attempt - 1)))
        d *= random.uniform(0.5, 1.0)
        if err.retry_after_s:
            d = max(d, min(err.retry_after_s, self.backoff_max_s))
        return d


class ManagedProvider:
    """Wraps a Provider with concurrency limits, rate limiting, timeout, retries, budget, cost,
    caching and tracing. Every model call made by any suite goes through one of these."""

    def __init__(
        self,
        provider: Provider,
        target: TargetConfig,
        policy: RetryPolicy,
        budget: Budget,
        tracer: Tracer,
        cache: ResponseCache | None = None,
        role: str = "target",
    ) -> None:
        self.provider = provider
        self.target = target
        self.policy = policy
        self.budget = budget
        self.tracer = tracer
        self.cache = cache
        self.role = role
        self.semaphore = asyncio.Semaphore(target.max_concurrency) if target.max_concurrency else None
        self.limiter = TokenBucket(target.rate_limit_rps) if target.rate_limit_rps else None
        self.reported_models: set[str] = set()
        self.inflight = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        meter = current_meter()
        cache_key = None
        cacheable = self.cache is not None and (
            request.temperature == 0 or self.target.params.get("temperature") == 0
        )
        if cacheable:
            cache_key = ResponseCache.key(self.target, request)
            hit = self.cache.get(cache_key)  # type: ignore[union-attr]
            if hit:
                if meter:
                    meter.calls += 1
                    meter.cached_calls += 1
                return hit
        if meter and self.role == "target":
            meter.calls += 1
        attempt = 0
        while True:
            attempt += 1
            self.budget.check()
            if meter and self.role == "target":
                meter.attempts += 1
                if attempt > 1:
                    meter.retries += 1
            request.metadata["attempt"] = attempt - 1
            with self.tracer.span(
                "gen_ai.chat",
                kind="CLIENT",
                **{
                    "gen_ai.system": self.target.provider,
                    "gen_ai.request.model": self.target.model or self.target.id,
                    "agentbench.target": self.target.id,
                    "agentbench.role": self.role,
                    "agentbench.attempt": attempt,
                    "gen_ai.request.temperature": request.temperature,
                    "gen_ai.request.tool_count": len(request.tools),
                },
            ) as span:
                try:
                    resp = await self._call(request)
                except ProviderError as err:
                    span.error(str(err))
                    span.set(**{"error.type": err.kind, "http.status_code": err.status})
                    self.budget.record(Usage(), 0.0)
                    if meter and self.role == "target":
                        meter.errors.append(f"attempt {attempt}: {err.kind} {err.status or ''}".strip())
                        meter.attempt_error_counts[err.kind] = meter.attempt_error_counts.get(err.kind, 0) + 1
                    if err.retryable and attempt <= self.policy.max_retries:
                        delay = self.policy.delay(attempt, err)
                        span.event("retry_scheduled", delay_s=round(delay, 3))
                        await asyncio.sleep(delay)
                        continue
                    raise
                cost = self.target.pricing.cost(resp.usage.input_tokens, resp.usage.output_tokens)
                self.budget.record(resp.usage, cost)
                if resp.model:
                    self.reported_models.add(resp.model)
                span.set(
                    **{
                        "gen_ai.response.model": resp.model,
                        "gen_ai.usage.input_tokens": resp.usage.input_tokens,
                        "gen_ai.usage.output_tokens": resp.usage.output_tokens,
                        "gen_ai.response.finish_reasons": resp.finish_reason,
                        "agentbench.cost_usd": round(cost, 8),
                        "agentbench.ttft_ms": round(resp.ttft_ms, 2) if resp.ttft_ms is not None else None,
                        "agentbench.tool_calls": ",".join(tc.name for tc in resp.tool_calls) or None,
                    }
                )
                if meter:
                    if self.role == "judge":
                        meter.judge_input_tokens += resp.usage.input_tokens
                        meter.judge_output_tokens += resp.usage.output_tokens
                        meter.judge_cost_usd += cost
                    else:
                        meter.input_tokens += resp.usage.input_tokens
                        meter.output_tokens += resp.usage.output_tokens
                        meter.cost_usd += cost
                        meter.model_latency_ms += resp.latency_ms
                        if meter.first_ttft_ms is None and resp.ttft_ms is not None:
                            meter.first_ttft_ms = resp.ttft_ms
                        if resp.model:
                            meter.models.add(resp.model)
                if cache_key:
                    self.cache.put(cache_key, resp)  # type: ignore[union-attr]
                return resp

    async def _call(self, request: ModelRequest) -> ModelResponse:
        queued_at = time.perf_counter()
        if self.limiter:
            await self.limiter.acquire()
        timeout = self.target.timeout_s or self.policy.timeout_s

        async def run() -> ModelResponse:
            start = time.perf_counter()
            meter = current_meter() if self.role == "target" else None
            self.inflight += 1
            if meter:
                meter.queue_wait_ms += (start - queued_at) * 1000
                meter.peak_api_concurrency = max(meter.peak_api_concurrency, self.inflight)
            try:
                resp = await self.provider.complete(request)
                if not resp.latency_ms:
                    resp.latency_ms = (time.perf_counter() - start) * 1000
                return resp
            finally:
                self.inflight -= 1
                if meter:
                    meter.api_latency_ms.append((time.perf_counter() - start) * 1000)

        try:
            if self.semaphore:
                async with self.semaphore:
                    return await asyncio.wait_for(run(), timeout)
            return await asyncio.wait_for(run(), timeout)
        except asyncio.TimeoutError as exc:
            raise ProviderError(f"no response within {timeout}s", kind="timeout", retryable=True) from exc

    async def aclose(self) -> None:
        await self.provider.aclose()


class ManagedAgent:
    """Applies timeout/retry/tracing/budget to an AgentTarget (HTTP agents bill via reported usage)."""

    def __init__(
        self, agent: AgentTarget, target: TargetConfig, policy: RetryPolicy, budget: Budget, tracer: Tracer
    ) -> None:
        self.agent = agent
        self.target = target
        self.policy = policy
        self.budget = budget
        self.tracer = tracer
        self.reported_models: set[str] = set()

    async def chat(
        self, user_id: str, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> AgentReply:
        attempt = 0
        timeout = self.target.timeout_s or self.policy.timeout_s * 3
        while True:
            attempt += 1
            self.budget.check()
            with self.tracer.span(
                "agent.chat",
                kind="CLIENT",
                **{
                    "agentbench.target": self.target.id,
                    "enduser.id": user_id,
                    "session.id": session_id,
                    "agentbench.attempt": attempt,
                },
            ) as span:
                start = time.perf_counter()
                try:
                    reply = await asyncio.wait_for(
                        self.agent.chat(user_id, session_id, message, metadata), timeout
                    )
                except asyncio.TimeoutError:
                    err = ProviderError(
                        f"agent did not reply within {timeout}s", kind="timeout", retryable=True
                    )
                    span.error(str(err))
                    if attempt <= self.policy.max_retries:
                        await asyncio.sleep(self.policy.delay(attempt, err))
                        continue
                    raise err from None
                except ProviderError as err:
                    span.error(str(err))
                    if err.retryable and attempt <= self.policy.max_retries:
                        await asyncio.sleep(self.policy.delay(attempt, err))
                        continue
                    raise
                reply.latency_ms = reply.latency_ms or (time.perf_counter() - start) * 1000
                span.set(
                    **{
                        "agentbench.tool_calls": ",".join(str(t.get("name")) for t in reply.tool_calls)
                        or None
                    }
                )
                meter = current_meter()
                if self.target.provider == "http_agent":
                    cost = self.target.pricing.cost(reply.usage.input_tokens, reply.usage.output_tokens)
                    self.budget.record(reply.usage, cost)
                    if meter:
                        meter.calls += 1
                        meter.attempts += attempt
                        meter.retries += attempt - 1
                        meter.input_tokens += reply.usage.input_tokens
                        meter.output_tokens += reply.usage.output_tokens
                        meter.cost_usd += cost
                if reply.model:
                    self.reported_models.add(reply.model)
                return reply

    async def reset(self) -> None:
        await self.agent.reset()

    async def aclose(self) -> None:
        await self.agent.aclose()
