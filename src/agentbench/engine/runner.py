"""The execution engine: schedules (suite x case x trial x target) work with bounded concurrency,
wraps every call in the execution policy, and produces a reproducible RunRecord."""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import secrets
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentbench import __version__
from agentbench.config import AgentBenchConfig, RunOptions, SuitePlan, TargetConfig
from agentbench.core.types import BudgetExceeded, ProviderError
from agentbench.datasets.loader import DatasetRegistry
from agentbench.engine.policy import (
    Budget,
    ManagedAgent,
    ManagedProvider,
    ResponseCache,
    RetryPolicy,
    start_meter,
    stop_meter,
)
from agentbench.engine.results import CaseResult, RunRecord
from agentbench.engine.tracing import Tracer
from agentbench.evaluators.judge import make_judge
from agentbench.metrics.aggregate import build_summary
from agentbench.providers import build_agent, build_provider
from agentbench.suites import get_suite
from agentbench.suites.base import Filters, Suite, TargetContext, WorkItem

ProgressFn = Callable[[int, int, CaseResult | None], None]


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def git_info(cwd: str) -> dict[str, str | None]:
    sha = os.environ.get("GITHUB_SHA")
    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME")
    if not sha:
        try:
            sha = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5
                ).stdout.strip()
                or None
            )
            branch = (
                branch
                or subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                or None
            )
        except (OSError, subprocess.SubprocessError):
            sha = None
    return {"sha": sha, "branch": branch}


@dataclass
class RunRequest:
    plan: dict[str, SuitePlan]
    label: str = ""
    filters: Filters = field(default_factory=Filters)
    target_filter: list[str] = field(default_factory=list)
    options: RunOptions | None = None


class Runner:
    def __init__(self, config: AgentBenchConfig, progress: ProgressFn | None = None) -> None:
        self.config = config
        self.progress = progress
        self.registry = DatasetRegistry(config.datasets_path)
        self.tracer = Tracer()
        self._managed: dict[str, ManagedProvider] = {}
        self._agents: dict[str, ManagedAgent] = {}
        self.warnings: list[str] = []

    # -- provider plumbing --------------------------------------------------------------------

    def _policy(self, opts: RunOptions, target: TargetConfig) -> RetryPolicy:
        return RetryPolicy(
            timeout_s=target.timeout_s or opts.timeout_s,
            max_retries=opts.max_retries if target.max_retries is None else target.max_retries,
            backoff_base_s=opts.backoff_base_s,
            backoff_max_s=opts.backoff_max_s,
        )

    def managed_model(
        self,
        target_id: str,
        opts: RunOptions,
        budget: Budget,
        cache: ResponseCache | None,
        role: str = "target",
    ) -> ManagedProvider:
        key = f"{role}:{target_id}"
        if key not in self._managed:
            target = self.config.target(target_id)
            self._managed[key] = ManagedProvider(
                build_provider(target),
                target,
                self._policy(opts, target),
                budget,
                self.tracer,
                cache,
                role=role,
            )
        return self._managed[key]

    def _agent_factory(
        self, target: TargetConfig, opts: RunOptions, budget: Budget, cache: ResponseCache | None
    ):
        def model_for(tid: str) -> ManagedProvider:
            return self.managed_model(tid, opts, budget, cache)  # type: ignore[return-value]

        if target.provider == "http_agent":
            shared = ManagedAgent(
                build_agent(target, model_for), target, self._policy(opts, target), budget, self.tracer
            )
            self._agents[target.id] = shared

            async def factory() -> ManagedAgent:
                return shared
        else:

            async def factory() -> ManagedAgent:
                agent = build_agent(target, model_for)
                return ManagedAgent(agent, target, self._policy(opts, target), budget, self.tracer)

        return factory

    # -- run --------------------------------------------------------------------------------

    async def run(self, request: RunRequest) -> RunRecord:
        opts = request.options or self.config.run
        run_id = new_run_id()
        started = time.time()
        created_at = datetime.now(timezone.utc).isoformat()
        budget = Budget(opts.budget.max_usd, opts.budget.max_tokens, opts.budget.max_requests)
        cache = ResponseCache(self.config.resolve_path(".agentbench/cache.sqlite")) if opts.cache else None

        judge_fn = None
        judge_id = self.config.judge.target if self.config.judge.enabled else None
        if judge_id:
            ok, why = self.config.target(judge_id).availability()
            if ok:
                judge_fn = make_judge(
                    self.managed_model(judge_id, opts, budget, cache, role="judge").complete, judge_id
                )
            else:
                self.warnings.append(
                    f"judge {judge_id} unavailable ({why}); semantic judge evaluators skipped"
                )
                judge_id = None

        suites: dict[str, Suite] = {}
        work: list[tuple[Suite, WorkItem, TargetContext, int]] = []
        custom: list[tuple[Suite, list[WorkItem], TargetContext, SuitePlan]] = []
        manifest_suites: dict[str, Any] = {}
        skipped_targets: dict[str, str] = {}
        contexts: dict[str, TargetContext] = {}

        for suite_name, plan in request.plan.items():
            suite = get_suite(suite_name)
            suites[suite_name] = suite
            items, dataset_info = suite.load(self.registry, plan, request.filters)
            target_ids = [t for t in plan.targets if not request.target_filter or t in request.target_filter]
            ready: list[str] = []
            for tid in target_ids:
                target = self.config.target(tid)
                ok, why = target.availability()
                kind = "agent" if target.is_agent else "model"
                if not ok:
                    skipped_targets[tid] = why
                    continue
                if kind not in suite.accepts:
                    skipped_targets[f"{tid}@{suite_name}"] = f"{suite_name} does not accept {kind} targets"
                    continue
                ready.append(tid)
                ctx = contexts.get(tid)
                if ctx is None:
                    ctx = TargetContext(
                        target=target, tracer=self.tracer, seed=opts.seed, run_id=run_id, judge=judge_fn
                    )
                    if target.is_agent:
                        ctx.fresh_agent = self._agent_factory(target, opts, budget, cache)
                    else:
                        ctx.model = self.managed_model(tid, opts, budget, cache)
                    contexts[tid] = ctx
                if suite.mode == "custom":
                    sctx = TargetContext(**{**ctx.__dict__, "options": plan.options})
                    custom.append((suite, items, sctx, plan))
            if suite.mode == "cases":
                for item in items:
                    for trial in range(opts.repeats):
                        for tid in ready:
                            sctx = TargetContext(**{**contexts[tid].__dict__, "options": plan.options})
                            work.append((suite, item, sctx, trial))
            manifest_suites[suite_name] = {
                "dataset": dataset_info,
                "targets": ready,
                "options": plan.options,
                "categories": plan.categories or None,
                "description": suite.description,
            }

        total = len(work) + len(custom)
        done = 0
        results: list[CaseResult] = []
        sem = asyncio.Semaphore(opts.concurrency)

        def report(r: CaseResult | None) -> None:
            nonlocal done
            done += 1
            if self.progress:
                self.progress(done, total, r)

        async def worker(suite: Suite, item: WorkItem, ctx: TargetContext, trial: int) -> CaseResult:
            async with sem:
                r = await self._execute(suite, item, ctx, trial, budget)
            report(r)
            return r

        if self.progress:
            self.progress(0, total, None)
        results.extend(await asyncio.gather(*(worker(*w) for w in work)))
        for suite, items, ctx, plan in custom:
            try:
                custom_results, extra = await suite.run_custom(ctx, items, plan)
            except BudgetExceeded as exc:
                custom_results, extra = [], {"aborted": str(exc)}
            results.extend(custom_results)
            manifest_suites[suite.name].setdefault("runs", {})[ctx.target.id] = extra
            report(None)

        reported: dict[str, list[str]] = {}
        for key, mp in self._managed.items():
            if mp.reported_models:
                reported[key.split(":", 1)[1]] = sorted(mp.reported_models)
        for tid, ma in self._agents.items():
            if ma.reported_models:
                reported[tid] = sorted(ma.reported_models)
        await self.aclose()

        finished = time.time()
        status = "aborted_budget" if budget.exceeded_reason else "completed"
        used_targets = sorted({tid for s in manifest_suites.values() for tid in s["targets"]})
        manifest = {
            "agentbench_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "git": git_info(self.config.base_dir),
            "config_hash": self.config.config_hash(),
            "seed": opts.seed,
            "repeats": opts.repeats,
            "concurrency": opts.concurrency,
            "timeout_s": opts.timeout_s,
            "max_retries": opts.max_retries,
            "cache": opts.cache,
            "suites": manifest_suites,
            "targets": {
                tid: {**self.config.target(tid).describe(), "reported_models": reported.get(tid, [])}
                for tid in used_targets
            },
            "agent_models": {
                tid: self.config.target(tid).agent.model_dump()
                for tid in used_targets
                if self.config.target(tid).agent
            },
            "skipped_targets": skipped_targets,
            "judge": {"target": judge_id, "reported_models": reported.get(judge_id or "", [])},
            "filters": request.filters.__dict__,
            "budget": budget.snapshot(),
            "warnings": self.warnings,
            "started_at": created_at,
            "duration_s": round(finished - started, 2),
        }
        summary = build_summary(results, suites)
        summary["totals"]["duration_s"] = round(finished - started, 2)
        return RunRecord(
            id=run_id,
            label=request.label,
            status=status,
            created_at=created_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            manifest=manifest,
            summary=summary,
            results=results,
        )

    async def _execute(
        self, suite: Suite, item: WorkItem, ctx: TargetContext, trial: int, budget: Budget
    ) -> CaseResult:
        meter, token = start_meter()
        start = time.perf_counter()
        trace_id = ""
        try:
            with self.tracer.span(
                f"case {item.id}",
                new_trace=True,
                **{
                    "agentbench.suite": suite.name,
                    "agentbench.case_id": item.id,
                    "agentbench.target": ctx.target.id,
                    "agentbench.trial": trial,
                    "agentbench.category": item.category,
                },
            ) as root:
                trace_id = root.trace_id
                try:
                    result = await suite.execute(item, ctx, trial)
                except BudgetExceeded as exc:
                    result = self._failure(suite, item, ctx, trial, "skipped", str(exc), "budget")
                except ProviderError as exc:
                    root.error(str(exc))
                    result = self._failure(suite, item, ctx, trial, "error", str(exc), exc.kind)
                except Exception as exc:  # a bug in a target/agent shouldn't kill the run
                    root.error(str(exc))
                    tb = traceback.format_exc(limit=4)
                    result = self._failure(
                        suite, item, ctx, trial, "error", f"{type(exc).__name__}: {exc}\n{tb}", "unknown"
                    )
                root.set(**{"agentbench.status": result.status})
        finally:
            stop_meter(token)
        wall_ms = (time.perf_counter() - start) * 1000
        if result.latency_ms is None:
            result.latency_ms = round(wall_ms, 2)
        result.ttft_ms = result.ttft_ms if result.ttft_ms is not None else meter.first_ttft_ms
        result.model_latency_ms = round(meter.model_latency_ms, 2)
        result.input_tokens = result.input_tokens or meter.input_tokens
        result.output_tokens = result.output_tokens or meter.output_tokens
        result.cost_usd = round(result.cost_usd or meter.cost_usd, 8)
        result.attempts = result.attempts or meter.attempts
        result.retries = result.retries or meter.retries
        result.cached = meter.calls > 0 and meter.cached_calls == meter.calls
        result.models = sorted(meter.models)
        if meter.errors:
            result.metrics.setdefault("transient_errors", len(meter.errors))
        if meter.judge_cost_usd:
            result.metrics["judge_cost_usd"] = round(meter.judge_cost_usd, 8)
        result.trace = self.tracer.collect(trace_id) if trace_id else []
        return result

    @staticmethod
    def _failure(
        suite: Suite, item: WorkItem, ctx: TargetContext, trial: int, status: str, error: str, kind: str
    ) -> CaseResult:
        payload = item.payload
        inp: dict[str, Any] = {}
        expected = None
        if hasattr(payload, "input"):
            inp = {"system": payload.input.system, "prompt": payload.input.prompt}
            expected = payload.expected
        elif isinstance(payload, dict):
            inp = {"scenario": payload.get("id"), "description": payload.get("description")}
        return CaseResult(
            suite=suite.name,
            case_id=item.id,
            category=item.category,
            target_id=ctx.target.id,
            trial=trial,
            status=status,
            passed=False,
            score=0.0,
            error=error,
            error_kind=kind,  # type: ignore[arg-type]
            input=inp,
            expected=expected,
            tags=list(item.tags),
        )

    async def aclose(self) -> None:
        for closable in [*self._managed.values(), *self._agents.values()]:
            with contextlib.suppress(Exception):
                await closable.aclose()
        self._managed.clear()
        self._agents.clear()
