"""The offline mock model provider."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import random

from agentbench.config import TargetConfig
from agentbench.core.types import ModelRequest, ModelResponse, ProviderError, Usage
from agentbench.providers.base import Provider
from agentbench.providers.mock import agent_brain, solvers
from agentbench.providers.mock.profiles import PROFILES, MockProfile


def _rng(*parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


class MockProvider(Provider):
    kind = "mock"

    def __init__(self, target: TargetConfig) -> None:
        super().__init__(target)
        name = target.model or target.id
        if name not in PROFILES:
            raise ValueError(f"unknown mock profile '{name}'. Available: {', '.join(PROFILES)}")
        self.profile: MockProfile = PROFILES[name].with_overrides(target.params)
        self.inflight = 0
        self.time_scale = float(
            os.environ.get("AGENTBENCH_MOCK_TIME_SCALE", target.params.get("time_scale", 1.0))
        )

    async def _sleep(self, seconds: float) -> None:
        if seconds > 0 and self.time_scale > 0:
            await asyncio.sleep(seconds * self.time_scale)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        p = self.profile
        meta = request.metadata
        last = request.messages[-1].content if request.messages else ""
        base = (
            p.name,
            p.version,
            meta.get("seed", 0),
            meta.get("case_id", ""),
            meta.get("trial", 0),
            meta.get("scope", ""),
        )
        content_seed = (
            *base,
            meta.get("step", len(request.messages)),
            hashlib.sha256(last.encode()).hexdigest()[:12],
        )
        transport_rng = _rng(*content_seed, "transport", meta.get("attempt", 0))
        self.inflight += 1
        try:
            overload = max(0.0, (self.inflight - p.capacity) / max(1, p.capacity))
            if transport_rng.random() < p.rate_limit_p + min(0.5, overload * 0.15):
                await self._sleep(0.02)
                raise ProviderError(
                    "mock rate limit exceeded",
                    kind="rate_limit",
                    status=429,
                    retryable=True,
                    retry_after_s=0.2,
                )
            if transport_rng.random() < p.server_error_p:
                await self._sleep(0.05)
                raise ProviderError("mock upstream overloaded", kind="server", status=503, retryable=True)
            if p.latency_ms > 0:
                latency_ms = p.latency_ms * math.exp(transport_rng.gauss(0, p.latency_sigma)) * (1 + overload)
            else:
                latency_ms = 0.0
            if transport_rng.random() < p.hang_p:
                latency_ms = p.hang_s * 1000

            degrade = min(0.35, overload * 0.1)
            text, tool_calls = self._respond(request, p, content_seed, base, degrade)
            await self._sleep(latency_ms / 1000)
            usage = Usage(
                estimate_tokens(request.prompt_text()), estimate_tokens(text) + 12 * len(tool_calls)
            )
            return ModelResponse(
                text=text,
                tool_calls=tool_calls,
                usage=usage,
                model=f"{p.name}@{p.version}",
                finish_reason="tool_calls" if tool_calls else "stop",
                ttft_ms=latency_ms * p.ttft_fraction,
                latency_ms=latency_ms,
            )
        finally:
            self.inflight -= 1

    def _respond(
        self, req: ModelRequest, p: MockProfile, content_seed: tuple, base: tuple, degrade: float
    ) -> tuple[str, list]:
        rng = _rng(*content_seed, "content")
        case_rng = _rng(*base, "case")
        user_text = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
        system = req.system or ""
        bad = rng.random() > (p.quality - degrade)
        slip = rng.random() < p.format_slip_p + degrade / 2

        if system.startswith("You are an impartial evaluator"):
            return agent_brain.judge(req, rng, p), []
        if "Extract the following fields from the invoice" in user_text:
            return solvers.solve_extraction(user_text, rng, p, bad, slip), []
        if "Convert the support note below into a JSON object" in user_text:
            return solvers.solve_json_format(user_text, rng, p, bad, slip), []
        if "Documents:" in user_text and "Question:" in user_text:
            hallucinate = rng.random() < p.hallucination_p + degrade
            return solvers.solve_doc_qa(user_text, rng, p, bad, hallucinate), []
        if "ANSWER: <number>" in user_text:
            return solvers.solve_reasoning(user_text, rng, p, bad, slip), []
        if "Apply the following steps" in user_text:
            return solvers.solve_instructions(user_text, rng, p, bad, slip), []
        if "Classify the customer support ticket" in user_text:
            return solvers.solve_classification(user_text, rng, p, bad, slip), []
        if "Summarize the following customer email" in user_text:
            comply = rng.random() < p.injection_susceptibility + degrade
            return solvers.solve_summary(system, user_text, rng, p, bad, comply), []
        has_write_tools = any(t.side_effect for t in req.tools)
        if "billing assistant" in system and not has_write_tools:
            return agent_brain.support_chat(req, rng, case_rng, p, degrade)
        if req.tools:
            return agent_brain.plan_tool_use(req, rng, case_rng, p, degrade)
        return f"[{p.name}] I received your message ({len(user_text)} characters).", []
