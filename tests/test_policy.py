"""Execution policy: retries, timeouts, budgets, caching."""

import asyncio

import pytest

from agentbench.config import TargetConfig
from agentbench.core.types import BudgetExceeded, Message, ModelRequest, ModelResponse, ProviderError, Usage
from agentbench.engine.policy import (
    Budget,
    ManagedProvider,
    ResponseCache,
    RetryPolicy,
    TokenBucket,
    start_meter,
    stop_meter,
)
from agentbench.engine.tracing import Tracer
from agentbench.providers.base import Provider


class Scripted(Provider):
    """Raises the scripted errors in order, then succeeds."""

    def __init__(self, errors, delay=0.0):
        super().__init__(
            TargetConfig(
                id="s", provider="mock", pricing={"input_per_mtok": 1_000_000, "output_per_mtok": 1_000_000}
            )
        )
        self.errors = list(errors)
        self.calls = 0
        self.delay = delay

    async def complete(self, request):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.errors:
            raise self.errors.pop(0)
        return ModelResponse(text="ok", usage=Usage(3, 2), model="scripted-1")


def managed(provider, budget=None, retries=3, timeout=1.0, cache=None):
    return ManagedProvider(
        provider,
        provider.target,
        RetryPolicy(timeout_s=timeout, max_retries=retries, backoff_base_s=0.0, backoff_max_s=0.0),
        budget or Budget(None, None, None),
        Tracer(),
        cache,
    )


def req(temperature=None):
    return ModelRequest(messages=[Message("user", "hi")], temperature=temperature)


async def test_retries_transient_errors_and_meters_them():
    p = Scripted(
        [
            ProviderError("429", kind="rate_limit", status=429, retryable=True),
            ProviderError("503", kind="server", status=503, retryable=True),
        ]
    )
    meter, token = start_meter()
    resp = await managed(p).complete(req())
    stop_meter(token)
    assert resp.text == "ok" and p.calls == 3
    assert meter.attempts == 3 and meter.retries == 2 and meter.cost_usd == pytest.approx(5.0)
    assert meter.models == {"scripted-1"}


async def test_non_retryable_errors_fail_fast():
    p = Scripted([ProviderError("bad key", kind="auth", status=401)])
    with pytest.raises(ProviderError):
        await managed(p).complete(req())
    assert p.calls == 1


async def test_retry_budget_is_respected():
    p = Scripted([ProviderError("x", kind="server", retryable=True)] * 10)
    with pytest.raises(ProviderError):
        await managed(p, retries=2).complete(req())
    assert p.calls == 3


async def test_timeouts_become_retryable_errors():
    p = Scripted([], delay=0.2)
    with pytest.raises(ProviderError) as exc:
        await managed(p, retries=0, timeout=0.05).complete(req())
    assert exc.value.kind == "timeout" and exc.value.retryable


async def test_budget_stops_further_calls():
    budget = Budget(max_usd=None, max_tokens=None, max_requests=2)
    mp = managed(Scripted([]), budget=budget)
    await mp.complete(req())
    await mp.complete(req())
    with pytest.raises(BudgetExceeded):
        await mp.complete(req())
    assert budget.snapshot()["exceeded"].startswith("request limit")


async def test_cache_serves_deterministic_requests(tmp_path):
    cache = ResponseCache(tmp_path / "cache.sqlite")
    p = Scripted([])
    mp = managed(p, cache=cache)
    await mp.complete(req(temperature=0))
    meter, token = start_meter()
    hit = await mp.complete(req(temperature=0))
    stop_meter(token)
    assert hit.cached and p.calls == 1 and meter.cached_calls == 1
    await mp.complete(req(temperature=0.7))  # sampled requests are never cached
    assert p.calls == 2


async def test_token_bucket_limits_rate():
    bucket = TokenBucket(rate_per_s=50, burst=1)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(6):
        await bucket.acquire()
    assert loop.time() - start >= 0.09
