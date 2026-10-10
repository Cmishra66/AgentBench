"""Performance & load suite: treats the model or agent as a service under test.

Closed-loop virtual users replay labelled cases through configurable stages (steady, ramp,
spike). Every response is still graded, so the report shows *quality under load* — a fast wrong
answer is not a performance win.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import defaultdict
from typing import Any

from agentbench.config import SuitePlan
from agentbench.core.types import BudgetExceeded, Message, ModelRequest, ProviderError
from agentbench.datasets.loader import DatasetRegistry
from agentbench.datasets.schema import TestCase
from agentbench.engine.policy import start_meter, stop_meter
from agentbench.engine.results import CaseResult
from agentbench.evaluators import case_passed, run_evaluators
from agentbench.metrics.aggregate import latency_block
from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem

PROFILES: dict[str, list[dict[str, float]]] = {
    "smoke": [{"users": 2, "duration_s": 5}],
    "steady": [{"users": 8, "duration_s": 20}],
    "ramp": [
        {"users": 2, "duration_s": 8},
        {"users": 8, "duration_s": 8},
        {"users": 16, "duration_s": 8},
        {"users": 32, "duration_s": 8},
    ],
    "spike": [{"users": 4, "duration_s": 10}, {"users": 48, "duration_s": 8}, {"users": 4, "duration_s": 10}],
}

FILLER = (
    "Appendix: archived operational notes. Pallet audits, dock schedules, and carrier scorecards are "
    "reviewed weekly by the regional operations team. "
)


class LoadSuite(Suite):
    name = "load"
    description = (
        "Concurrent virtual users in steady/ramp/spike stages with latency percentiles and quality under load"
    )
    mode = "custom"

    def load(
        self, registry: DatasetRegistry, plan: SuitePlan, filters: Filters
    ) -> tuple[list[WorkItem], dict]:
        categories = filters.categories or plan.categories or ["classification", "extraction", "reasoning"]
        selection = registry.load_cases("core", categories=categories)
        items = [WorkItem(id=c.id, category=c.category, payload=c) for c in selection.cases]
        return items, selection.describe()

    @staticmethod
    def stages(options: dict[str, Any]) -> list[dict[str, float]]:
        if options.get("stages"):
            return [dict(s) for s in options["stages"]]
        profile = options.get("profile", "steady")
        if profile not in PROFILES:
            raise ValueError(
                f"unknown load profile '{profile}'. Use one of {list(PROFILES)} or define stages"
            )
        stages = [dict(s) for s in PROFILES[profile]]
        if options.get("users") and len(stages) == 1:
            stages[0]["users"] = options["users"]
        if options.get("duration_s") and len(stages) == 1:
            stages[0]["duration_s"] = options["duration_s"]
        return stages

    async def run_custom(
        self, ctx: TargetContext, items: list[WorkItem], plan: SuitePlan
    ) -> tuple[list[CaseResult], dict]:
        opts = {**plan.options, **ctx.options}
        stages = self.stages(opts)
        think = float(opts.get("think_time_s", 0.0))
        pad_tokens = int(opts.get("pad_context_tokens", 0))
        max_requests = int(opts.get("max_requests", 20_000))
        padding = (FILLER * (pad_tokens * 4 // len(FILLER) + 1))[: pad_tokens * 4] if pad_tokens else ""
        cases = itertools.cycle([i.payload for i in items])
        counter = itertools.count()
        results: list[CaseResult] = []
        t0 = time.perf_counter()
        stop_all = False

        async def one_request(case: TestCase, vu: int, stage: int, seq: int) -> None:
            nonlocal stop_all
            meter, token = start_meter()
            start = time.perf_counter()
            status, error, kind, text = "failed", None, None, ""
            passed, score = False, 0.0
            try:
                prompt = case.input.prompt + (f"\n\n<appendix>\n{padding}\n</appendix>" if padding else "")
                meta = {"case_id": case.id, "trial": seq, "seed": ctx.seed}
                if ctx.model is not None:
                    resp = await ctx.model.complete(
                        ModelRequest(
                            messages=[Message("user", prompt)], system=case.input.system, metadata=meta
                        )
                    )
                    text = resp.text
                else:
                    assert ctx.fresh_agent is not None
                    agent = await ctx.fresh_agent()
                    reply = await agent.chat(f"load-vu{vu}", f"load-{ctx.run_id}-{vu}", prompt, meta)
                    text = reply.text
                outcomes = await run_evaluators(case, text, None)
                passed, score = case_passed(outcomes)
                status = "passed" if passed else "failed"
            except BudgetExceeded as exc:
                status, error, kind, stop_all = "skipped", str(exc), "budget", True
            except ProviderError as exc:
                status, error, kind = "error", str(exc), exc.kind
            finally:
                stop_meter(token)
            end = time.perf_counter()
            results.append(
                CaseResult(
                    suite=self.name,
                    case_id=case.id,
                    category=f"stage-{stage + 1}",
                    target_id=ctx.target.id,
                    trial=seq,
                    status=status,
                    passed=passed,
                    score=score,
                    latency_ms=round((end - start) * 1000, 2),  # type: ignore[arg-type]
                    ttft_ms=meter.first_ttft_ms,
                    model_latency_ms=round(meter.model_latency_ms, 2),
                    input_tokens=meter.input_tokens,
                    output_tokens=meter.output_tokens,
                    cost_usd=round(meter.cost_usd, 8),
                    attempts=meter.attempts,
                    retries=meter.retries,
                    error=error,
                    error_kind=kind,
                    output=text[:500],
                    input={"case": case.id, "vu": vu},
                    metrics={
                        "stage": stage + 1,
                        "vus": stages[stage]["users"],
                        "t_start_s": round(start - t0, 3),
                        "t_end_s": round(end - t0, 3),
                        "case_category": case.category,
                    },
                    models=sorted(meter.models),
                )
            )

        async def virtual_user(vu: int, stage: int, deadline: float) -> None:
            while not stop_all and time.perf_counter() < deadline:
                seq = next(counter)
                if seq >= max_requests:
                    return
                await one_request(next(cases), vu, stage, seq)
                if think:
                    await asyncio.sleep(think)

        for idx, stage in enumerate(stages):
            if stop_all:
                break
            deadline = time.perf_counter() + float(stage["duration_s"])
            await asyncio.gather(*(virtual_user(vu, idx, deadline) for vu in range(int(stage["users"]))))

        return results, self.load_report(results, stages, time.perf_counter() - t0, pad_tokens)

    @staticmethod
    def load_report(
        results: list[CaseResult], stages: list[dict[str, float]], wall_s: float, pad_tokens: int = 0
    ) -> dict[str, Any]:
        per_stage = []
        for idx, stage in enumerate(stages):
            rs = [r for r in results if r.metrics.get("stage") == idx + 1 and r.status != "skipped"]
            ok = [r for r in rs if r.status != "error"]
            dur = float(stage["duration_s"])
            per_stage.append(
                {
                    "stage": idx + 1,
                    "users": stage["users"],
                    "duration_s": dur,
                    "requests": len(rs),
                    "throughput_rps": round(len(rs) / dur, 2) if dur else None,
                    "latency_ms": latency_block([r.latency_ms for r in ok if r.latency_ms is not None]),
                    "ttft_ms": latency_block([r.ttft_ms for r in ok if r.ttft_ms is not None]),
                    "error_rate": round(sum(1 for r in rs if r.status == "error") / len(rs), 4)
                    if rs
                    else None,
                    "timeout_rate": round(sum(1 for r in rs if r.error_kind == "timeout") / len(rs), 4)
                    if rs
                    else None,
                    "accuracy": round(sum(1 for r in rs if r.passed) / len(rs), 4) if rs else None,
                    "retries": sum(r.retries for r in rs),
                }
            )
        buckets: dict[int, list[CaseResult]] = defaultdict(list)
        for r in results:
            buckets[int(r.metrics.get("t_end_s", 0))].append(r)
        timeline = []
        for sec in range(int(wall_s) + 1):
            rs = buckets.get(sec, [])
            lat = [r.latency_ms for r in rs if r.status != "error" and r.latency_ms is not None]
            timeline.append(
                {
                    "t": sec,
                    "requests": len(rs),
                    "errors": sum(1 for r in rs if r.status == "error"),
                    "p95_ms": latency_block(lat)["p95"],
                    "passed": sum(1 for r in rs if r.passed),
                }
            )
        base_acc = per_stage[0]["accuracy"] if per_stage else None
        worst = min((s["accuracy"] for s in per_stage if s["accuracy"] is not None), default=None)
        return {
            "stages": per_stage,
            "timeline": timeline,
            "wall_time_s": round(wall_s, 2),
            "total_requests": len(results),
            "pad_context_tokens": pad_tokens,
            "achieved_rps": round(len(results) / wall_s, 2) if wall_s else None,
            "peak_rps": max((t["requests"] for t in timeline), default=0),
            "accuracy_drop_under_load_pp": round((base_acc - worst) * 100, 2)
            if base_acc is not None and worst is not None
            else None,
        }

    def aggregate(self, results: list[CaseResult]) -> dict[str, Any]:
        if not results:
            return {}
        stage_numbers = sorted({r.metrics.get("stage", 1) for r in results})
        stages = []
        for s in stage_numbers:
            rs = [r for r in results if r.metrics.get("stage") == s]
            span = max((r.metrics.get("t_end_s", 0) for r in rs), default=0) - min(
                (r.metrics.get("t_start_s", 0) for r in rs), default=0
            )
            stages.append({"users": rs[0].metrics.get("vus", 0), "duration_s": max(span, 0.001)})
        wall = max((r.metrics.get("t_end_s", 0) for r in results), default=0.0)
        return self.load_report(results, stages, wall)
