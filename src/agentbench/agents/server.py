"""HTTP wrapper that exposes the reference agent over the AgentBench agent protocol.

Useful for testing the ``http_agent`` adapter end to end, for Playwright API tests, and as a
template for wrapping your own agent.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agentbench.agents.reference import PRESETS, ReferenceAgent
from agentbench.config import AgentBenchConfig
from agentbench.core.types import ProviderError
from agentbench.providers import build_provider


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=50_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SeedIn(BaseModel):
    invoices: list[dict[str, Any]] = Field(default_factory=list)
    users: dict[str, str] = Field(default_factory=dict)
    faults: list[dict[str, Any]] | None = None
    enable_write_tools: bool | None = None


def create_agent_app(
    cfg: AgentBenchConfig, preset: str = "secure", model_target: str = "mock-strong"
) -> FastAPI:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset '{preset}'")
    target = cfg.target(model_target)
    provider = build_provider(target)
    state: dict[str, Any] = {"agent": ReferenceAgent(provider, preset)}
    app = FastAPI(title="AgentBench reference agent", version="1.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "preset": preset, "model": model_target}

    @app.post("/chat")
    async def chat(body: ChatIn) -> dict[str, Any]:
        try:
            reply = await state["agent"].chat(body.user_id, body.session_id, body.message, body.metadata)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status or 502, detail=str(exc)) from exc
        return {
            "reply": reply.text,
            "tool_calls": reply.tool_calls,
            "usage": {"input_tokens": reply.usage.input_tokens, "output_tokens": reply.usage.output_tokens},
            "model": reply.model,
        }

    @app.post("/reset")
    async def reset() -> dict[str, str]:
        state["agent"] = ReferenceAgent(provider, preset)
        return {"status": "reset"}

    @app.post("/seed")
    async def seed(body: SeedIn) -> dict[str, Any]:
        state["agent"].seed(
            invoices=body.invoices,
            users=body.users,
            faults=body.faults,
            enable_write_tools=body.enable_write_tools,
        )
        return {"status": "seeded", "invoices": len(body.invoices)}

    return app
