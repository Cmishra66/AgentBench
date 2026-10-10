"""Accuracy suites: single-turn labelled cases scored by deterministic oracles.

``core`` covers extraction, strict JSON, grounded document QA, reasoning, multi-step
instructions and classification. ``injection`` reuses the same machinery for prompt-injection
attacks embedded in untrusted content.
"""

from __future__ import annotations

from typing import Any

from agentbench.config import SuitePlan
from agentbench.core.types import Message, ModelRequest
from agentbench.datasets.loader import DatasetRegistry
from agentbench.datasets.schema import TestCase
from agentbench.engine.results import CaseResult
from agentbench.engine.tracing import current_span
from agentbench.evaluators import case_passed, run_evaluators
from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem


class CaseSuite(Suite):
    dataset_folder = "core"

    def load(
        self, registry: DatasetRegistry, plan: SuitePlan, filters: Filters
    ) -> tuple[list[WorkItem], dict]:
        categories = filters.categories or plan.categories or None
        selection = registry.load_cases(
            self.dataset_folder,
            categories=categories,
            case_ids=filters.case_ids or None,
            tags=filters.tags or None,
            limit=filters.limit,
        )
        items = [WorkItem(id=c.id, category=c.category, payload=c, tags=c.tags) for c in selection.cases]
        return items, selection.describe()

    async def execute(self, item: WorkItem, ctx: TargetContext, trial: int) -> CaseResult:
        case: TestCase = item.payload
        meta = {"case_id": case.id, "trial": trial, "seed": ctx.seed}
        text = ""
        if ctx.model is not None:
            req = ModelRequest(
                messages=[Message("user", case.input.prompt)],
                system=case.input.system,
                response_format=case.input.response_format,
                metadata=meta,
            )
            resp = await ctx.model.complete(req)
            text = resp.text
        else:
            assert ctx.fresh_agent is not None
            agent = await ctx.fresh_agent()
            message = (
                f"{case.input.system}\n\n{case.input.prompt}" if case.input.system else case.input.prompt
            )
            reply = await agent.chat(f"bench-{ctx.run_id}", f"{ctx.run_id}-{case.id}-{trial}", message, meta)
            text = reply.text
        outcomes = await run_evaluators(case, text, ctx.judge)
        passed, score = case_passed(outcomes)
        span = current_span()
        if span:
            span.set(**{"agentbench.passed": passed, "agentbench.score": score})
        metrics: dict[str, Any] = {}
        for o in outcomes:
            if not o.skipped:
                metrics.update(o.metrics)
        return CaseResult(
            suite=self.name,
            case_id=case.id,
            category=case.category,
            target_id=ctx.target.id,
            trial=trial,
            status="passed" if passed else "failed",
            passed=passed,
            score=score,
            output=text,
            input={"system": case.input.system, "prompt": case.input.prompt},
            expected=case.expected,
            evals=[o.to_dict() for o in outcomes],
            metrics=metrics,
            tags=list(case.tags),
        )


class CoreSuite(CaseSuite):
    name = "core"
    description = (
        "Labelled accuracy cases: extraction, JSON, grounded QA, reasoning, instructions, classification"
    )
    dataset_folder = "core"


class InjectionSuite(CaseSuite):
    name = "injection"
    description = (
        "Prompt-injection attacks hidden in untrusted content (secret exfiltration, instruction override)"
    )
    dataset_folder = "injection"

    def aggregate(self, results: list[CaseResult]) -> dict[str, Any]:
        executed = [r for r in results if r.status in ("passed", "failed")]
        if not executed:
            return {}
        leaked = sum(1 for r in executed if r.metrics.get("secret_leaked"))
        followed = sum(1 for r in executed if r.metrics.get("injection_followed"))
        attacked = sum(
            1 for r in executed if r.metrics.get("secret_leaked") or r.metrics.get("injection_followed")
        )
        completed = sum(1 for r in executed if r.metrics.get("task_completed"))
        by_attack: dict[str, dict[str, Any]] = {}
        for r in executed:
            a = by_attack.setdefault(r.category.replace("injection_", ""), {"n": 0, "successful_attacks": 0})
            a["n"] += 1
            a["successful_attacks"] += int(not r.passed)
        for a in by_attack.values():
            a["attack_success_rate"] = round(a["successful_attacks"] / a["n"], 4)
        n = len(executed)
        return {
            "attack_success_rate": round(attacked / n, 4),
            "secret_leak_rate": round(leaked / n, 4),
            "instruction_follow_rate": round(followed / n, 4),
            "task_completion_rate": round(completed / n, 4),
            "by_attack": by_attack,
        }
