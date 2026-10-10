"""Suite interface: a suite loads work items and knows how to execute one against a target."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agentbench.config import SuitePlan, TargetConfig
from agentbench.datasets.loader import DatasetRegistry
from agentbench.engine.policy import ManagedAgent, ManagedProvider
from agentbench.engine.results import CaseResult
from agentbench.engine.tracing import Tracer
from agentbench.evaluators.base import JudgeFn


@dataclass
class Filters:
    categories: list[str] = field(default_factory=list)
    case_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    limit: int | None = None


@dataclass
class WorkItem:
    id: str
    category: str
    payload: Any
    tags: list[str] = field(default_factory=list)


@dataclass
class TargetContext:
    target: TargetConfig
    tracer: Tracer
    seed: int
    run_id: str
    model: ManagedProvider | None = None
    agent: ManagedAgent | None = None
    judge: JudgeFn | None = None
    # Builds a fresh agent instance (clean state). Reference agents get a new instance; HTTP agents
    # are shared and rely on namespaced user/session ids.
    fresh_agent: Callable[[], Awaitable[ManagedAgent]] | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def is_agent(self) -> bool:
        return self.agent is not None or self.fresh_agent is not None


class Suite(ABC):
    name: str = "abstract"
    description: str = ""
    accepts: frozenset[str] = frozenset({"model", "agent"})
    mode: str = "cases"  # "cases" = one work item per case; "custom" = suite drives execution (load)

    @abstractmethod
    def load(
        self, registry: DatasetRegistry, plan: SuitePlan, filters: Filters
    ) -> tuple[list[WorkItem], dict]: ...

    async def execute(self, item: WorkItem, ctx: TargetContext, trial: int) -> CaseResult:
        raise NotImplementedError

    async def run_custom(
        self, ctx: TargetContext, items: list[WorkItem], plan: SuitePlan
    ) -> tuple[list[CaseResult], dict]:
        raise NotImplementedError

    def aggregate(self, results: list[CaseResult]) -> dict[str, Any]:
        """Suite-specific metrics added to the generic summary for one target."""
        return {}
