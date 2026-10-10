"""Provider registry: builds a model provider or agent target from a TargetConfig."""

from __future__ import annotations

from collections.abc import Callable

from agentbench.config import TargetConfig
from agentbench.providers.base import AgentTarget, Provider

PROVIDER_KINDS = ["mock", "openai", "openai_compatible", "anthropic", "gemini"]
AGENT_KINDS = ["reference_agent", "http_agent"]


def build_provider(target: TargetConfig) -> Provider:
    kind = target.provider
    if kind == "mock":
        from agentbench.providers.mock import MockProvider

        return MockProvider(target)
    if kind in ("openai", "openai_compatible"):
        from agentbench.providers.openai import OpenAIProvider

        return OpenAIProvider(target)
    if kind == "anthropic":
        from agentbench.providers.anthropic import AnthropicProvider

        return AnthropicProvider(target)
    if kind == "gemini":
        from agentbench.providers.gemini import GeminiProvider

        return GeminiProvider(target)
    if kind in AGENT_KINDS:
        raise ValueError(f"target {target.id} is an agent ({kind}); use build_agent()")
    raise ValueError(
        f"unknown provider '{kind}' for target {target.id}. Known: {PROVIDER_KINDS + AGENT_KINDS}"
    )


def build_agent(target: TargetConfig, model_for: Callable[[str], Provider]) -> AgentTarget:
    """``model_for(target_id)`` returns a (managed) provider for the agent's underlying model."""
    if target.provider == "http_agent":
        from agentbench.providers.http_agent import HttpAgentTarget

        return HttpAgentTarget(target)
    if target.provider == "reference_agent":
        from agentbench.agents.reference import ReferenceAgentTarget

        agent_cfg = target.agent
        if agent_cfg is None:
            raise ValueError(f"target {target.id}: reference_agent requires an 'agent:' block")
        return ReferenceAgentTarget(target, model_for(agent_cfg.model_target))
    raise ValueError(f"target {target.id} is not an agent target")


__all__ = ["AGENT_KINDS", "PROVIDER_KINDS", "AgentTarget", "Provider", "build_agent", "build_provider"]
