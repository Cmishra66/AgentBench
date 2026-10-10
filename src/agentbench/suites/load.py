"""Closed-loop load tests with graded responses, queue measurements and stage-level objectives."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentbench.config import SuitePlan
from agentbench.core.types import BudgetExceeded, Message, ModelRequest, ProviderError
from agentbench.datasets.loader import DatasetRegistry
from agentbench.datasets.schema import TestCase
from agentbench.engine.policy import start_meter, stop_meter
from agentbench.engine.results import CaseResult
from agentbench.evaluators import case_passed, run_evaluators
from agentbench.metrics.aggregate import latency_block
from agentbench.metrics.stats import wilson_interval
from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem

PROFILES: dict[str, list[dict[str, float]]] = {
    "smoke": [{"users": 2, "duration_s": 5}],
    "steady": [{"users": 8, "duration_s": 20}],
    "ramp": [{"users": n, "duration_s": 8} for n in (2, 8, 16, 32)],
    "spike": [{"users": 4, "duration_s": 10}, {"users": 48, "duration_s": 8}, {"users": 4, "duration_s": 10}],
    "stress": [{"users": n, "duration_s": 15} for n in (2, 8, 16, 32, 64)],
    "soak": [{"users": 8, "duration_s": 300}],
}
FILLER = (
    "Appendix: archived operational notes. Pallet audits, dock schedules, and carrier scorecards are "
    "reviewed weekly by the regional operations team. "
)


class LoadStage(BaseModel):
    users: int = Field(ge=1, le=256)
    duration_s: float = Field(gt=0, le=3600, allow_inf_nan=False)


class LoadOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "steady"
    stages: list[LoadStage] | None = Field(default=None, min_length=1, max_length=20)
    users: int | None = Field(default=None, ge=1, le=256)
    duration_s: float | None = Field(default=None, gt=0, le=3600, allow_inf_nan=False)
    think_time_s: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)
    pad_context_tokens: int = Field(default=0, ge=0, le=100_000)
    max_requests: int = Field(default=20_000, ge=1, le=100_000)
    max_inflight: int | None = Field(default=None, ge=1, le=256)
    latency_slo_ms: float = Field(default=5000, gt=0, allow_inf_nan=False)
    min_pass_rate: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    max_error_rate: float = Field(default=0.01, ge=0, le=1, allow_inf_nan=False)
    min_samples: int = Field(default=20, ge=1)


class LoadSuite(Suite):
    name = "load"
    description = "Concurrent virtual users with latency, throughput, queueing, errors and quality under load"
    mode = "custom"

    def load(
        self, registry: DatasetRegistry, plan: SuitePlan, filters: Filters
    ) -> tuple[list[WorkItem], dict]:
        categories = filters.categories or plan.categories or ["classification", "extraction", "reasoning"]
        selection = registry.load_cases(
            "core", categories=categories, case_ids=filters.case_ids, tags=filters.tags, limit=filters.limit
        )
        return [
            WorkItem(id=c.id, category=c.category, payload=c) for c in selection.cases
        ], selection.describe()

    @staticmethod
    def stages(options: dict[str, Any]) -> list[dict[str, float]]:
        opts = LoadOptions.model_validate(options)
        if opts.stages:
            return [stage.model_dump() for stage in opts.stages]
        if opts.profile not in PROFILES:
            raise ValueError(
                f"unknown load profile '{opts.profile}'. Use one of {list(PROFILES)} or define stages"
            )
        stages = [dict(stage) for stage in PROFILES[opts.profile]]
        if len(stages) == 1:
            if opts.users is not None:
                stages[0]["users"] = opts.users
            if opts.duration_s is not None:
                stages[0]["duration_s"] = opts.duration_s
        return stages

    async def run_custom(
        self, ctx: TargetContext, items: list[WorkItem], plan: SuitePlan
    ) -> tuple[list[CaseResult], dict]:
        options = {**plan.options, **ctx.options}
        opts = LoadOptions.model_validate(options)
        stages = self.stages(options)
        if not items:
            raise ValueError("load test has no cases after filtering")
        padding = (FILLER * (opts.pad_context_tokens * 4 // len(FILLER) + 1))[: opts.pad_context_tokens * 4]
        cases = itertools.cycle([item.payload for item in items])
        results: list[CaseResult] = []
        timings: list[dict[str, float]] = []
        issued, stop_all = 0, False
        t0 = time.perf_counter()
        original_semaphore = ctx.model.semaphore if ctx.model else None
        original_cache = ctx.model.cache if ctx.model else None
        agent_limit = asyncio.Semaphore(opts.max_inflight) if opts.max_inflight and not ctx.model else None
        if ctx.model:
            ctx.model.cache = None
            if opts.max_inflight is not None:
                ctx.model.semaphore = asyncio.Semaphore(opts.max_inflight)

        async def one_request(case: TestCase, vu: int, stage: int, seq: int) -> None:
            nonlocal stop_all
            meter, token = start_meter()
            start = time.perf_counter()
            prompt = case.input.prompt + (f"\n\n<appendix>\n{padding}\n</appendix>" if padding else "")
            status, error, kind, text = "failed", None, None, ""
            passed, score, outcomes = False, 0.0, []
            try:
                metadata = {"case_id": case.id, "trial": seq, "seed": ctx.seed}
                if ctx.model:
                    response = await ctx.model.complete(
                        ModelRequest(
                            messages=[Message("user", prompt)], system=case.input.system, metadata=metadata
                        )
                    )
                    text = response.text
                else:
                    assert ctx.fresh_agent is not None
                    agent = await ctx.fresh_agent()
                    if agent_limit:
                        async with agent_limit:
                            meter.queue_wait_ms += (time.perf_counter() - start) * 1000
                            reply = await agent.chat(
                                f"load-vu{vu}", f"load-{ctx.run_id}-{vu}", prompt, metadata
                            )
                    else:
                        reply = await agent.chat(f"load-vu{vu}", f"load-{ctx.run_id}-{vu}", prompt, metadata)
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
                    latency_ms=round((end - start) * 1000, 2),
                    ttft_ms=meter.first_ttft_ms,
                    model_latency_ms=round(meter.model_latency_ms, 2),
                    input_tokens=meter.input_tokens,
                    output_tokens=meter.output_tokens,
                    cost_usd=round(meter.cost_usd, 8),
                    attempts=meter.attempts,
                    retries=meter.retries,
                    error=error,
                    error_kind=kind,
                    output=text,
                    input={"case": case.id, "vu": vu, "prompt": prompt, "system": case.input.system},
                    expected=case.expected,
                    evals=[outcome.to_dict() for outcome in outcomes],
                    metrics={
                        "stage": stage + 1,
                        "vus": stages[stage]["users"],
                        "t_start_s": round(start - t0, 6),
                        "t_end_s": round(end - t0, 6),
                        "case_category": case.category,
                        "queue_wait_ms": round(meter.queue_wait_ms, 2),
                        "api_latency_ms": [round(value, 2) for value in meter.api_latency_ms],
                        "peak_api_concurrency": meter.peak_api_concurrency,
                        "attempt_error_counts": meter.attempt_error_counts,
                    },
                    models=sorted(meter.models),
                )
            )

        async def virtual_user(vu: int, stage: int, deadline: float) -> None:
            nonlocal issued
            while not stop_all and issued < opts.max_requests and time.perf_counter() < deadline:
                seq = issued
                issued += 1
                await one_request(next(cases), vu, stage, seq)
                if opts.think_time_s:
                    remaining = deadline - time.perf_counter()
                    if remaining > 0:
                        await asyncio.sleep(min(opts.think_time_s, remaining))

        try:
            for index, stage in enumerate(stages):
                if stop_all or issued >= opts.max_requests:
                    break
                start = time.perf_counter()
                await asyncio.gather(
                    *(
                        virtual_user(vu, index, start + stage["duration_s"])
                        for vu in range(int(stage["users"]))
                    )
                )
                end = time.perf_counter()
                timings.append({"start_s": start - t0, "duration_s": end - start})
        finally:
            if ctx.model:
                ctx.model.semaphore, ctx.model.cache = original_semaphore, original_cache
        reason = "budget" if stop_all else "max_requests" if issued >= opts.max_requests else "duration"
        report = self.load_report(results, stages, time.perf_counter() - t0, options=options, timings=timings)
        report.update(
            stop_reason=reason,
            configured_max_inflight=ctx.target.max_concurrency,
            effective_max_inflight=opts.max_inflight or ctx.target.max_concurrency,
            rate_limit_rps=ctx.target.rate_limit_rps,
            profile=opts.profile if not opts.stages else "custom",
        )
        return results, report

    @staticmethod
    def measurements(results: list[CaseResult], duration: float, opts: LoadOptions) -> dict[str, Any]:
        executed = [result for result in results if result.status != "skipped"]
        count = len(executed)
        passed = sum(result.passed for result in executed)
        errors = Counter(result.error_kind or "unknown" for result in executed if result.status == "error")
        attempt_errors: Counter = Counter()
        for result in executed:
            attempt_errors.update(result.metrics.get("attempt_error_counts", {}))
        latency = latency_block([result.latency_ms for result in executed if result.latency_ms is not None])
        within = sum(
            result.status != "error"
            and result.latency_ms is not None
            and result.latency_ms <= opts.latency_slo_ms
            for result in executed
        )
        good = sum(
            result.passed and result.latency_ms is not None and result.latency_ms <= opts.latency_slo_ms
            for result in executed
        )
        accuracy = passed / count if count else None
        error_rate = sum(errors.values()) / count if count else None
        verdict = "insufficient_samples"
        if count >= opts.min_samples:
            verdict = (
                "pass"
                if (
                    accuracy >= opts.min_pass_rate
                    and error_rate <= opts.max_error_rate
                    and latency["p95"] is not None
                    and latency["p95"] <= opts.latency_slo_ms
                )
                else "fail"
            )
        return {
            "requests": count,
            "passed": passed,
            "accuracy": round(accuracy, 4) if count else None,
            "accuracy_ci95": list(wilson_interval(passed, count)),
            "throughput_rps": round(count / duration, 2) if duration else None,
            "goodput_rps": round(passed / duration, 2) if duration else None,
            "slo_goodput_rps": round(good / duration, 2) if duration else None,
            "latency_ms": latency,
            "api_latency_ms": latency_block(
                [value for result in executed for value in result.metrics.get("api_latency_ms", [])]
            ),
            "queue_wait_ms": latency_block(
                [result.metrics["queue_wait_ms"] for result in executed if "queue_wait_ms" in result.metrics]
            ),
            "peak_api_concurrency": max(
                (r.metrics.get("peak_api_concurrency", 0) for r in executed), default=0
            )
            or None,
            "ttft_ms": latency_block([r.ttft_ms for r in executed if r.ttft_ms is not None]),
            "error_rate": round(error_rate, 4) if count else None,
            "timeout_rate": round(errors.get("timeout", 0) / count, 4) if count else None,
            "error_counts": dict(errors),
            "attempt_error_counts": dict(attempt_errors),
            "attempts": sum(r.attempts for r in executed),
            "retries": sum(r.retries for r in executed),
            "latency_slo_rate": round(within / count, 4) if count else None,
            "slo_pass_rate": round(good / count, 4) if count else None,
            "slo_verdict": verdict,
            "slo_sample_count": count,
            "tail_sample_warning": count < 100,
        }

    @classmethod
    def load_report(
        cls,
        results: list[CaseResult],
        stages: list[dict[str, float]],
        wall_s: float,
        pad_tokens: int = 0,
        *,
        options: dict[str, Any] | None = None,
        timings: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        opts = LoadOptions.model_validate(options or {"pad_context_tokens": pad_tokens})
        per_stage = []
        for index, stage in enumerate(stages):
            rs = [r for r in results if r.metrics.get("stage") == index + 1]
            timing = timings[index] if timings and index < len(timings) else None
            duration = timing["duration_s"] if timing else float(stage["duration_s"])
            data = cls.measurements(rs, duration, opts)
            if not rs:
                data["slo_verdict"] = "not_run"
                duration = 0
            per_stage.append(
                {
                    "stage": index + 1,
                    "users": stage["users"],
                    "duration_s": round(duration, 3),
                    "planned_duration_s": stage.get("planned_duration_s", stage["duration_s"]),
                    "drain_s": round(max(0, duration - stage["duration_s"]), 3),
                    **data,
                }
            )
        buckets: dict[int, list[CaseResult]] = defaultdict(list)
        for result in results:
            if result.status != "skipped":
                buckets[int(result.metrics.get("t_end_s", 0))].append(result)
        timeline = []
        for second in range(int(wall_s) + 1):
            rs = buckets.get(second, [])
            timeline.append(
                {
                    "t": second,
                    "requests": len(rs),
                    "errors": sum(r.status == "error" for r in rs),
                    "p95_ms": latency_block([r.latency_ms for r in rs if r.latency_ms is not None])["p95"],
                    "passed": sum(r.passed for r in rs),
                }
            )
        base = per_stage[0] if per_stage else {}
        observed = [stage for stage in per_stage if stage["requests"]]
        worst = min((stage["accuracy"] for stage in observed), default=None)
        base_accuracy = base.get("accuracy")
        aggregate = cls.measurements(results, wall_s, opts)
        verdicts = {stage["slo_verdict"] for stage in per_stage}
        overall = "fail" if "fail" in verdicts else "pass" if verdicts == {"pass"} else "insufficient_samples"
        return {
            **aggregate,
            "slo_verdict": overall,
            "stages": per_stage,
            "timeline": timeline,
            "wall_time_s": round(wall_s, 3),
            "total_requests": aggregate["requests"],
            "pad_context_tokens": opts.pad_context_tokens,
            "achieved_rps": aggregate["throughput_rps"],
            "peak_rps": max((point["requests"] for point in timeline), default=0),
            "accuracy_drop_under_load_pp": round((base_accuracy - worst) * 100, 2)
            if base_accuracy is not None and worst is not None
            else None,
            "p95_slowdown": round(
                max(
                    stage["latency_ms"]["p95"] for stage in observed if stage["latency_ms"]["p95"] is not None
                )
                / base["latency_ms"]["p95"],
                2,
            )
            if base.get("latency_ms", {}).get("p95")
            else None,
            "slo": {
                "latency_ms": opts.latency_slo_ms,
                "min_pass_rate": opts.min_pass_rate,
                "max_error_rate": opts.max_error_rate,
                "min_samples": opts.min_samples,
            },
        }

    def aggregate(self, results: list[CaseResult]) -> dict[str, Any]:
        if not results:
            return {}
        stages = []
        for number in range(1, max(r.metrics.get("stage", 1) for r in results) + 1):
            rs = [r for r in results if r.metrics.get("stage") == number]
            start = min((r.metrics.get("t_start_s", 0) for r in rs), default=0)
            end = max((r.metrics.get("t_end_s", 0) for r in rs), default=start)
            stages.append(
                {"users": rs[0].metrics.get("vus", 0) if rs else 0, "duration_s": max(end - start, 0.001)}
            )
        wall = max((r.metrics.get("t_end_s", 0) for r in results), default=0)
        return self.load_report(results, stages, wall)
