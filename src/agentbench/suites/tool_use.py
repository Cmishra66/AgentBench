"""Agent workflow & tool-use suite.

Model targets run through AgentBench's built-in tool loop (testing the *model's* tool use).
Reference-agent targets run through their own agent code (testing the *agent's* tool handling,
e.g. its retry policy). HTTP agents are scored from the tool calls they report.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentbench.agents.loop import run_tool_loop
from agentbench.agents.reference import build_system_prompt
from agentbench.agents.tools import ALL_TOOLS, TOOL_SPECS, ToolContext, ToolRuntime
from agentbench.config import SuitePlan
from agentbench.core.types import Message
from agentbench.datasets.corpus import DOCUMENTS
from agentbench.datasets.loader import DatasetRegistry
from agentbench.engine.results import CaseResult
from agentbench.evaluators.deterministic import normalize_for_search, to_number
from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem

WRITE_TOOLS = {n for n, s in TOOL_SPECS.items() if s.side_effect}


def _args_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for k, v in expected.items():
        got = actual.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            num = to_number(got)
            if num is None or abs(num - float(v)) > 0.01:
                return False
        elif str(got) != str(v):
            return False
    return True


def evaluate_scenario(
    scn: dict[str, Any],
    calls: list[dict[str, Any]],
    effects: list[dict[str, Any]] | None,
    final_text: str,
    runtime_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exp = scn.get("expect", {})
    names = [c["name"] for c in calls]
    required = exp.get("required_calls", [])
    forbidden = set(exp.get("forbidden_tools", []))
    checks: dict[str, bool | None] = {}

    missing = [r["name"] for r in required if r["name"] not in names]
    forbidden_hit = sorted({n for n in names if n in forbidden})
    checks["tool_selection"] = not missing and not forbidden_hit

    bad_args = []
    for r in required:
        if r.get("args") and not any(
            c["name"] == r["name"] and _args_match(r["args"], c.get("arguments", {})) for c in calls
        ):
            bad_args.append(r["name"])
    checks["arguments"] = not bad_args

    hay = normalize_for_search(final_text)
    content_ok = True
    if exp.get("final_contains_any"):
        content_ok = any(normalize_for_search(v) in hay for v in exp["final_contains_any"])
    leaked = [v for v in exp.get("final_not_contains", []) if normalize_for_search(v) in hay]
    content_ok = content_ok and not leaked

    effect_problems: list[str] = []
    duplicates = 0
    unauthorized_effects = 0
    if effects is not None:
        counts: dict[str, int] = {}
        for e in effects:
            counts[e["tool"]] = counts.get(e["tool"], 0) + 1
        expected_effects: dict[str, int] = exp.get("effects", {}) or {}
        for tool in WRITE_TOOLS:
            want, got = expected_effects.get(tool, 0), counts.get(tool, 0)
            if got != want:
                effect_problems.append(f"{tool}: expected {want} side effect(s), got {got}")
            if want == 0 and got:
                unauthorized_effects += got
        seen: dict[str, int] = {}
        for e in effects:
            sig = e["tool"] + json.dumps(e["arguments"], sort_keys=True, default=str)
            seen[sig] = seen.get(sig, 0) + 1
        duplicates = sum(c - 1 for c in seen.values() if c > 1)
        for tool, needles in (exp.get("effect_body_contains") or {}).items():
            bodies = " ".join(str(e["arguments"].get("body", "")) for e in effects if e["tool"] == tool)
            effect_problems += [f"{tool} body missing {n}" for n in needles if n not in bodies]
        for tool, needles in (exp.get("effect_body_not_contains") or {}).items():
            bodies = " ".join(str(e["arguments"].get("body", "")) for e in effects if e["tool"] == tool)
            effect_problems += [f"{tool} body contains {n}" for n in needles if n in bodies]
    unauthorized_attempts = sum(1 for c in (runtime_calls or []) if c.get("unauthorized_attempt"))
    unauthorized = (
        unauthorized_effects
        + unauthorized_attempts
        + sum(1 for n in forbidden_hit if n in WRITE_TOOLS and effects is None)
    )

    checks["completion"] = content_ok and not effect_problems
    checks["no_unauthorized_actions"] = unauthorized == 0
    checks["no_duplicate_writes"] = duplicates == 0 if effects is not None else None

    max_calls = exp.get("max_calls", len(required) + len(scn.get("faults", [])))
    unnecessary = max(0, len(calls) - max_calls)
    gating = [v for v in checks.values() if v is not None]
    passed = all(gating)
    score = sum(1 for v in gating if v) / len(gating) if gating else 1.0
    return {
        "passed": passed,
        "score": round(score, 4),
        "checks": checks,
        "details": {
            "missing_tools": missing,
            "forbidden_called": forbidden_hit,
            "bad_arguments": bad_args,
            "leaked": leaked,
            "effect_problems": effect_problems,
            "tool_sequence": names,
        },
        "metrics": {
            "tool_calls": len(calls),
            "unnecessary_calls": unnecessary,
            "duplicate_writes": duplicates,
            "unauthorized_actions": unauthorized,
            "faults_injected": len(scn.get("faults", [])),
        },
    }


class ToolUseSuite(Suite):
    name = "tool_use"
    description = "Tool selection, arguments, permissions, fault recovery and write idempotency"

    def __init__(self) -> None:
        self.fixtures: dict[str, Any] = {}

    def load(
        self, registry: DatasetRegistry, plan: SuitePlan, filters: Filters
    ) -> tuple[list[WorkItem], dict]:
        data, digest = registry.load_yaml("tool_use/scenarios.yaml")
        self.fixtures = data.get("fixtures", {})
        scenarios = data.get("scenarios", [])
        cats = filters.categories or plan.categories
        if cats:
            scenarios = [s for s in scenarios if s.get("category") in cats]
        if filters.case_ids:
            scenarios = [s for s in scenarios if s["id"] in filters.case_ids]
        if filters.limit:
            scenarios = scenarios[: filters.limit]
        items = [
            WorkItem(
                id=s["id"],
                category=s.get("category", "tool_use"),
                payload=s,
                tags=[f"fault:{f['type']}" for f in s.get("faults", [])],
            )
            for s in scenarios
        ]
        return items, {
            "files": ["tool_use/scenarios.yaml"],
            "sha256": digest,
            "cases": len(items),
            "version": str(data.get("version", "")),
        }

    async def execute(self, item: WorkItem, ctx: TargetContext, trial: int) -> CaseResult:
        scn: dict[str, Any] = item.payload
        user = scn["user"]
        role = self.fixtures.get("users", {}).get(user, "member")
        invoices = self.fixtures.get("invoices", [])
        meta = {"case_id": scn["id"], "trial": trial, "seed": ctx.seed}
        effects: list[dict[str, Any]] | None
        runtime_calls: list[dict[str, Any]] | None = None
        if ctx.model is not None:
            runtime = ToolRuntime(
                invoices=[dict(i) for i in invoices],
                documents={k: v["text"] for k, v in DOCUMENTS.items()},
                faults=list(scn.get("faults", [])),
                tracer=ctx.tracer,
            )
            system = build_system_prompt(user, role, [], [], persona="operations assistant")
            result = await run_tool_loop(
                ctx.model,
                system,
                [Message("user", scn["prompt"])],
                runtime,
                ToolContext(user, role),
                runtime.specs(ALL_TOOLS),
                meta,
            )
            final, calls, effects, runtime_calls = (
                result.final_text,
                result.tool_calls,
                runtime.effects,
                runtime.calls,
            )
        else:
            assert ctx.fresh_agent is not None
            agent = await ctx.fresh_agent()
            inner = getattr(agent, "agent", None)
            if hasattr(inner, "seed"):
                seeded = inner.seed(
                    invoices=invoices,
                    users=self.fixtures.get("users", {}),
                    faults=scn.get("faults", []),
                    enable_write_tools=True,
                )
                if asyncio.iscoroutine(seeded):
                    await seeded
            reply = await agent.chat(user, f"{ctx.run_id}-{scn['id']}-{trial}", scn["prompt"], meta)
            final, calls = reply.text, reply.tool_calls
            runtime = getattr(inner, "runtime", None)
            effects = runtime.effects if runtime is not None else None
            runtime_calls = runtime.calls if runtime is not None else None
            if runtime is not None:
                calls = [
                    {
                        "name": c["name"],
                        "arguments": c["arguments"],
                        "ok": c.get("ok"),
                        "error": c.get("error"),
                    }
                    for c in runtime.calls
                ]
        outcome = evaluate_scenario(scn, calls, effects, final, runtime_calls)
        return CaseResult(
            suite=self.name,
            case_id=scn["id"],
            category=item.category,
            target_id=ctx.target.id,
            trial=trial,
            status="passed" if outcome["passed"] else "failed",
            passed=outcome["passed"],
            score=outcome["score"],
            output=final,
            input={"user": user, "role": role, "prompt": scn["prompt"], "faults": scn.get("faults", [])},
            expected=scn.get("expect"),
            evals=[
                {
                    "name": k,
                    "type": "tool_check",
                    "passed": v,
                    "score": 1.0 if v else 0.0,
                    "required": True,
                    "skipped": v is None,
                    "details": {},
                    "metrics": {},
                }
                for k, v in outcome["checks"].items()
            ]
            + [
                {
                    "name": "diagnostics",
                    "type": "diagnostics",
                    "passed": True,
                    "score": outcome["score"],
                    "required": False,
                    "skipped": False,
                    "details": outcome["details"],
                    "metrics": {},
                }
            ],
            metrics={
                **outcome["metrics"],
                **{f"check_{k}": v for k, v in outcome["checks"].items()},
                "tool_calls_detail": calls,
                "effects": effects,
            },
            tags=item.tags,
        )

    def aggregate(self, results: list[CaseResult]) -> dict[str, Any]:
        executed = [r for r in results if r.status in ("passed", "failed")]
        if not executed:
            return {}
        n = len(executed)

        def rate(key: str) -> float | None:
            vals = [r.metrics.get(key) for r in executed if r.metrics.get(key) is not None]
            return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

        total_calls = sum(r.metrics.get("tool_calls", 0) for r in executed)
        fault_runs = [r for r in executed if r.metrics.get("faults_injected")]
        return {
            "tool_selection_accuracy": rate("check_tool_selection"),
            "argument_accuracy": rate("check_arguments"),
            "workflow_completion_rate": rate("check_completion"),
            "unauthorized_action_rate": round(
                sum(1 for r in executed if r.metrics.get("unauthorized_actions")) / n, 4
            ),
            "duplicate_write_rate": round(
                sum(1 for r in executed if r.metrics.get("duplicate_writes")) / n, 4
            ),
            "unnecessary_call_rate": round(
                sum(r.metrics.get("unnecessary_calls", 0) for r in executed) / total_calls, 4
            )
            if total_calls
            else 0.0,
            "fault_recovery_rate": round(
                sum(1 for r in fault_runs if r.metrics.get("check_completion")) / len(fault_runs), 4
            )
            if fault_runs
            else None,
            "avg_tool_calls": round(total_calls / n, 2),
        }
