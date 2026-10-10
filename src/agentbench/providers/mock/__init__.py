"""Offline mock models.

These are *simulators*, not real models: they solve the bundled task families with rule-based
solvers and then inject realistic, seeded failure modes (wrong fields, format slips,
hallucinations, prompt-injection compliance, tool misuse, rate limits, latency tails, overload).
They let the whole platform — runner, evaluators, statistics, gates, reports — be exercised and
tested without API keys or spend. Results from mock targets say nothing about real models.
"""

from agentbench.providers.mock.profiles import PROFILES, MockProfile
from agentbench.providers.mock.provider import MockProvider

__all__ = ["PROFILES", "MockProfile", "MockProvider"]
