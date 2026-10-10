"""Anthropic Messages API adapter."""

from __future__ import annotations

from typing import Any

from agentbench.core.types import ModelRequest, ModelResponse, ToolCall, Usage
from agentbench.providers.base import HttpProvider

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(HttpProvider):
    kind = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"

    def build_payload(self, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                # Consecutive tool results belong in one user turn.
                if (
                    messages
                    and messages[-1]["role"] == "user"
                    and isinstance(messages[-1]["content"], list)
                    and all(b.get("type") == "tool_result" for b in messages[-1]["content"])
                ):
                    messages[-1]["content"].append(block)
                else:
                    messages.append({"role": "user", "content": [block]})
            elif m.role == "assistant" and m.tool_calls:
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                content.extend(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in m.tool_calls
                )
                messages.append({"role": "assistant", "content": content})
            elif m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})
        payload: dict[str, Any] = {
            "model": self.target.model,
            "max_tokens": self.resolve_max_tokens(request),
            "messages": messages,
        }
        if request.system:
            payload["system"] = request.system
        temperature = self.resolve_temperature(request)
        if temperature is not None:
            payload["temperature"] = temperature
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        payload.update(self.param("extra_body", {}) or {})
        return payload

    @staticmethod
    def parse_response(data: dict[str, Any]) -> ModelResponse:
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input") or {}
                    )
                )
        usage = data.get("usage") or {}
        return ModelResponse(
            text="".join(texts),
            tool_calls=calls,
            usage=Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            model=data.get("model", ""),
            finish_reason=data.get("stop_reason"),
            raw=data,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        headers = {"anthropic-version": self.param("anthropic_version", ANTHROPIC_VERSION)}
        key = self.api_key()
        if key:
            headers["x-api-key"] = key
        data, elapsed = await self.post_json(
            f"{self.base_url}/messages", self.build_payload(request), headers
        )
        resp = self.parse_response(data)
        resp.latency_ms = elapsed
        return resp
