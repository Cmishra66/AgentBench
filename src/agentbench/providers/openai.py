"""OpenAI Chat Completions adapter. Also used for any OpenAI-compatible server
(Ollama, vLLM, LM Studio, OpenRouter, Azure-style gateways) via ``base_url``."""

from __future__ import annotations

import json
from typing import Any

from agentbench.core.types import Message, ModelRequest, ModelResponse, ToolCall, Usage
from agentbench.providers.base import HttpProvider, parse_json_arguments


class OpenAIProvider(HttpProvider):
    kind = "openai"
    default_base_url = "https://api.openai.com/v1"

    def build_payload(self, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": self.param("system_role", "system"), "content": request.system})
        for m in request.messages:
            messages.append(self._convert(m))
        payload: dict[str, Any] = {"model": self.target.model, "messages": messages}
        temperature = self.resolve_temperature(request)
        if temperature is not None:
            payload["temperature"] = temperature
        payload[self.param("max_tokens_param", "max_completion_tokens")] = self.resolve_max_tokens(request)
        if self.param("seed") is not None:
            payload["seed"] = self.param("seed")
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        if request.response_format == "json" and self.param("json_mode", False):
            payload["response_format"] = {"type": "json_object"}
        payload.update(self.param("extra_body", {}) or {})
        return payload

    @staticmethod
    def _convert(m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ],
            }
        return {"role": m.role, "content": m.content}

    @staticmethod
    def parse_response(data: dict[str, Any]) -> ModelResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = [
            ToolCall(
                id=tc.get("id", f"call_{i}"),
                name=(tc.get("function") or {}).get("name", ""),
                arguments=parse_json_arguments((tc.get("function") or {}).get("arguments")),
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        usage = data.get("usage") or {}
        return ModelResponse(
            text=msg.get("content") or "",
            tool_calls=tool_calls,
            usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        headers = {}
        key = self.api_key()
        if key:
            headers["authorization"] = f"Bearer {key}"
        data, elapsed = await self.post_json(
            f"{self.base_url}/chat/completions", self.build_payload(request), headers
        )
        resp = self.parse_response(data)
        resp.latency_ms = elapsed
        return resp
