"""Common provider interfaces.

Two kinds of systems can be put under test:

* ``Provider`` — a stateless model endpoint (OpenAI, Anthropic, Gemini, any OpenAI-compatible
  server, or the offline mock models). One request in, one response out.
* ``AgentTarget`` — a stateful agent with users, sessions, memory and tools (the bundled
  reference agent or any HTTP agent that implements the AgentBench agent protocol).
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from agentbench.config import TargetConfig
from agentbench.core.types import AgentReply, ModelRequest, ModelResponse, ProviderError


class Provider(ABC):
    kind = "abstract"

    def __init__(self, target: TargetConfig) -> None:
        self.target = target

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None

    def param(self, name: str, default: Any = None) -> Any:
        return self.target.params.get(name, default)

    def resolve_temperature(self, request: ModelRequest) -> float | None:
        if request.temperature is not None:
            return request.temperature
        return self.target.params.get("temperature")

    def resolve_max_tokens(self, request: ModelRequest, default: int = 1024) -> int:
        return int(request.max_output_tokens or self.target.params.get("max_output_tokens", default))


class HttpProvider(Provider):
    """Shared plumbing for HTTP JSON providers: client lifecycle and error normalisation."""

    default_base_url = ""

    def __init__(self, target: TargetConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(target)
        timeout = httpx.Timeout(target.timeout_s or 120.0, connect=10.0)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    @property
    def base_url(self) -> str:
        return (self.target.base_url or self.default_base_url).rstrip("/")

    def api_key(self) -> str | None:
        import os

        return os.environ.get(self.target.api_key_env) if self.target.api_key_env else None

    async def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[dict, float]:
        merged = {"content-type": "application/json", **headers, **self.target.headers}
        start = time.perf_counter()
        try:
            resp = await self._client.post(url, json=payload, headers=merged)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"request timed out: {exc}", kind="timeout", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"network error: {exc}", kind="network", retryable=True) from exc
        elapsed_ms = (time.perf_counter() - start) * 1000
        if resp.status_code >= 400:
            raise ProviderError.from_status(resp.status_code, resp.text, resp.headers.get("retry-after"))
        try:
            return resp.json(), elapsed_ms
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned non-JSON body", kind="server", retryable=True) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class AgentTarget(ABC):
    """A stateful system under test that is addressed per user and per session."""

    kind = "agent"

    def __init__(self, target: TargetConfig) -> None:
        self.target = target

    @abstractmethod
    async def chat(
        self, user_id: str, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> AgentReply: ...

    async def reset(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"id": self.target.id, "provider": self.target.provider}


def parse_json_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as dicts or JSON strings; malformed JSON is kept for evaluation."""
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"__value__": parsed}
    except (TypeError, json.JSONDecodeError):
        return {"__malformed__": str(raw)}
