"""Evaluator registry and execution.

Deterministic oracles run first and decide pass/fail wherever possible. The LLM judge is an
optional, non-blocking semantic signal (see ``judge.py``) and is skipped when no judge is set.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from agentbench.datasets.schema import TestCase


@dataclass
class EvalOutcome:
    name: str
    type: str
    passed: bool
    score: float
    required: bool = True
    skipped: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SyncEvaluator = Callable[[dict[str, Any], str, TestCase], EvalOutcome]
JudgeFn = Callable[[dict[str, Any], str, TestCase], Awaitable[EvalOutcome]]

_REGISTRY: dict[str, SyncEvaluator] = {}


def register(name: str) -> Callable[[SyncEvaluator], SyncEvaluator]:
    def deco(fn: SyncEvaluator) -> SyncEvaluator:
        _REGISTRY[name] = fn
        return fn

    return deco


def available_evaluators() -> list[str]:
    from agentbench.evaluators import deterministic  # noqa: F401  (populate registry)

    return sorted([*list(_REGISTRY), "llm_judge"])


async def run_evaluators(case: TestCase, text: str, judge: JudgeFn | None = None) -> list[EvalOutcome]:
    from agentbench.evaluators import deterministic  # noqa: F401  (populate registry)

    outcomes: list[EvalOutcome] = []
    for spec in case.evaluators:
        etype = spec["type"]
        required = bool(spec.get("required", True))
        name = spec.get("name", etype)
        if etype == "llm_judge":
            if judge is None:
                outcomes.append(
                    EvalOutcome(
                        name=name,
                        type=etype,
                        passed=True,
                        score=0.0,
                        required=required,
                        skipped=True,
                        details={"reason": "no judge configured"},
                    )
                )
                continue
            outcome = await judge(spec, text, case)
        else:
            fn = _REGISTRY.get(etype)
            if fn is None:
                raise KeyError(f"case {case.id}: unknown evaluator type '{etype}'")
            outcome = fn(spec, text, case)
        outcome.name = name
        outcome.required = required
        outcomes.append(outcome)
    return outcomes


def case_passed(outcomes: list[EvalOutcome]) -> tuple[bool, float]:
    gating = [o for o in outcomes if o.required and not o.skipped]
    if not gating:
        return True, 1.0
    passed = all(o.passed for o in gating)
    score = sum(o.score for o in gating) / len(gating)
    return passed, round(score, 4)
