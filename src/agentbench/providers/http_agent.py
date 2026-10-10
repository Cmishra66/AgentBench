"""Generic HTTP agent adapter — test agents written in any language.

Agent protocol (see docs/agent-protocol.md):

    POST {base_url}/chat
      {"user_id": "...", "session_id": "...", "message": "...", "metadata": {...}}
    -> {"reply": "...", "tool_calls": [{"name": ..., "arguments": {...}, "result": ...}],
        "usage": {"input_tokens": 0, "output_tokens": 0}, "model": "..."}

    POST {base_url}/reset   (optional; clears agent state between isolation scenarios)
    GET  {base_url}/health  (optional)
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

import httpx

from agentbench.config import TargetConfig
from agentbench.core.types import AgentReply, ProviderError, Usage
from agentbench.providers.base import AgentTarget


class HttpAgentTarget(AgentTarget):
    kind = "http_agent"

    def __init__(self, target: TargetConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(target)
        if not target.base_url:
            raise ValueError(f"target {target.id}: http_agent requires base_url")
        headers = dict(target.headers)
        if target.api_key_env and os.environ.get(target.api_key_env):
            headers.setdefault("authorization", f"Bearer {os.environ[target.api_key_env]}")
        self._client = httpx.AsyncClient(
            base_url=target.base_url.rstrip("/"),
            timeout=httpx.Timeout(target.timeout_s or 120.0, connect=10.0),
            headers=headers,
            transport=transport,
        )

    async def chat(
        self, user_id: str, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> AgentReply:
        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "metadata": metadata or {},
        }
        start = time.perf_counter()
        try:
            resp = await self._client.post("/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"agent timed out: {exc}", kind="timeout", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"agent unreachable: {exc}", kind="network", retryable=True) from exc
        latency = (time.perf_counter() - start) * 1000
        if resp.status_code >= 400:
            raise ProviderError.from_status(resp.status_code, resp.text, resp.headers.get("retry-after"))
        data = resp.json()
        usage = data.get("usage") or {}
        return AgentReply(
            text=data.get("reply", ""),
            tool_calls=data.get("tool_calls") or [],
            usage=Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            model=data.get("model", ""),
            latency_ms=latency,
            ttft_ms=data.get("ttft_ms"),
        )

    async def reset(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post("/reset")

    async def seed(self, **kwargs: Any) -> bool:
        """Optional protocol extension: seed fixtures (invoices, users, faults). False if unsupported."""
        try:
            resp = await self._client.post("/seed", json=kwargs)
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    async def aclose(self) -> None:
        await self._client.aclose()

    def describe(self) -> dict[str, Any]:
        return {"id": self.target.id, "provider": "http_agent", "base_url": self.target.base_url}
