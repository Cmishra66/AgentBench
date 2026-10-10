"""Suite registry."""

from __future__ import annotations

from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem


def _registry() -> dict[str, type[Suite]]:
    from agentbench.suites.core import CoreSuite, InjectionSuite
    from agentbench.suites.isolation import IsolationSuite
    from agentbench.suites.load import LoadSuite
    from agentbench.suites.tool_use import ToolUseSuite

    return {s.name: s for s in (CoreSuite, InjectionSuite, ToolUseSuite, IsolationSuite, LoadSuite)}


def available_suites() -> dict[str, str]:
    return {name: cls.description for name, cls in _registry().items()}


def get_suite(name: str) -> Suite:
    reg = _registry()
    if name not in reg:
        raise KeyError(f"unknown suite '{name}'. Available: {', '.join(reg)}")
    return reg[name]()


__all__ = ["Filters", "Suite", "TargetContext", "WorkItem", "available_suites", "get_suite"]
