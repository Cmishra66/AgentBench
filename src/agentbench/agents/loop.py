"""A minimal, provider-agnostic tool-calling loop (the "harness" used to test a model's tool use)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentbench.agents.tools import ToolContext, ToolRuntime
from agentbench.core.types import Message, ModelRequest, ModelResponse, ToolCall, ToolSpec


class SupportsComplete(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


@dataclass
class LoopPolicy:
    max_steps: int = 8
    # Agent-level (not model-level) retry of failed tool calls. "naive" strips idempotency keys,
    # reproducing a classic double-write bug in agent code.
    auto_retry_tools: int = 0
    strip_idempotency_on_retry: bool = False


@dataclass
class LoopResult:
    final_text: str
    steps: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[Message] = field(default_factory=list)
    hit_step_limit: bool = False


async def run_tool_loop(
    model: SupportsComplete,
    system: str,
    messages: list[Message],
    runtime: ToolRuntime,
    ctx: ToolContext,
    tools: list[ToolSpec],
    metadata: dict[str, Any],
    policy: LoopPolicy | None = None,
) -> LoopResult:
    policy = policy or LoopPolicy()
    transcript = list(messages)
    executed: list[dict[str, Any]] = []
    for step in range(policy.max_steps):
        req = ModelRequest(
            messages=list(transcript), system=system, tools=tools, metadata={**metadata, "step": step}
        )
        resp = await model.complete(req)
        if not resp.tool_calls:
            return LoopResult(resp.text, step + 1, executed, transcript)
        transcript.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))
        for tc in resp.tool_calls:
            result = await runtime.execute(tc, ctx)
            retries = 0
            while not result.get("ok") and result.get("retryable") and retries < policy.auto_retry_tools:
                retries += 1
                args = dict(tc.arguments)
                if policy.strip_idempotency_on_retry:
                    args.pop("idempotency_key", None)
                result = await runtime.execute(ToolCall(id=tc.id, name=tc.name, arguments=args), ctx)
            executed.append(
                {
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "ok": bool(result.get("ok")),
                    "error": result.get("error"),
                    "agent_retries": retries,
                }
            )
            transcript.append(
                Message("tool", json.dumps(result, default=str), tool_call_id=tc.id, name=tc.name)
            )
    return LoopResult(
        "I couldn't finish this request within the step limit.",
        policy.max_steps,
        executed,
        transcript,
        hit_step_limit=True,
    )
