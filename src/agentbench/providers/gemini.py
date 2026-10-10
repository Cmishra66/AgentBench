"""Google Gemini ``generateContent`` REST adapter."""

from __future__ import annotations

import json
from typing import Any

from agentbench.core.types import ModelRequest, ModelResponse, ToolCall, Usage
from agentbench.providers.base import HttpProvider

# Keys Gemini's function-declaration schema subset does not accept.
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "$id", "examples", "default"}


def sanitize_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: sanitize_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [sanitize_schema(v) for v in schema]
    return schema


class GeminiProvider(HttpProvider):
    kind = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def build_payload(self, request: ModelRequest) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "tool":
                try:
                    response: Any = json.loads(m.content)
                except (json.JSONDecodeError, TypeError):
                    response = m.content
                if not isinstance(response, dict):
                    response = {"content": response}
                part = {"functionResponse": {"name": m.name or "tool", "response": response}}
                if (
                    contents
                    and contents[-1]["role"] == "user"
                    and all("functionResponse" in p for p in contents[-1]["parts"])
                ):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif m.role == "assistant":
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                parts.extend({"functionCall": {"name": tc.name, "args": tc.arguments}} for tc in m.tool_calls)
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif m.role == "user":
                contents.append({"role": "user", "parts": [{"text": m.content}]})
        payload: dict[str, Any] = {"contents": contents}
        if request.system:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}
        gen: dict[str, Any] = {"maxOutputTokens": self.resolve_max_tokens(request)}
        temperature = self.resolve_temperature(request)
        if temperature is not None:
            gen["temperature"] = temperature
        if request.response_format == "json" and self.param("json_mode", False):
            gen["responseMimeType"] = "application/json"
        payload["generationConfig"] = gen
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": sanitize_schema(t.parameters),
                        }
                        for t in request.tools
                    ]
                }
            ]
        payload.update(self.param("extra_body", {}) or {})
        return payload

    @staticmethod
    def parse_response(data: dict[str, Any]) -> ModelResponse:
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        texts, calls = [], []
        for i, part in enumerate(parts):
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=fc.get("id") or f"gemini_call_{i}",
                        name=fc.get("name", ""),
                        arguments=fc.get("args") or {},
                    )
                )
        usage = data.get("usageMetadata") or {}
        return ModelResponse(
            text="".join(texts),
            tool_calls=calls,
            usage=Usage(int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))),
            model=data.get("modelVersion", ""),
            finish_reason=candidate.get("finishReason"),
            raw=data,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        headers = {}
        key = self.api_key()
        if key:
            headers["x-goog-api-key"] = key
        url = f"{self.base_url}/models/{self.target.model}:generateContent"
        data, elapsed = await self.post_json(url, self.build_payload(request), headers)
        resp = self.parse_response(data)
        if not resp.model:
            resp.model = self.target.model
        resp.latency_ms = elapsed
        return resp
