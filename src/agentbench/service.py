"""Shared orchestration used by the CLI, the API server and the pytest integration."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbench.config import AgentBenchConfig, RunOptions, SuitePlan
from agentbench.datasets.loader import DatasetRegistry
from agentbench.engine.results import RunRecord
from agentbench.engine.runner import ProgressFn, Runner, RunRequest
from agentbench.regression import GateResult, apply_gate, compare_runs, load_gate_rules
from agentbench.reporting.html import write_report
from agentbench.reporting.junit import gate_to_junit, run_to_junit, write_xml
from agentbench.reporting.markdown import gate_markdown, run_summary_markdown
from agentbench.storage import Store, export_run, load_run_file
from agentbench.suites import get_suite
from agentbench.suites.base import Filters

DEFAULT_EXCLUDED = {"load"}


@dataclass
class RunSettings:
    suites: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    case_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    limit: int | None = None
    label: str = ""
    repeats: int | None = None
    concurrency: int | None = None
    seed: int | None = None
    timeout_s: float | None = None
    max_usd: float | None = None
    cache: bool | None = None
    suite_options: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_plan(config: AgentBenchConfig, settings: RunSettings) -> dict[str, SuitePlan]:
    names = settings.suites or [s for s in config.suites if s not in DEFAULT_EXCLUDED]
    plan: dict[str, SuitePlan] = {}
    for name in names:
        get_suite(name)  # validates the name
        base = config.suites.get(name, SuitePlan())
        targets = settings.targets or base.targets
        if not targets:
            raise ValueError(f"suite '{name}' has no targets; pass --target or add targets to the config")
        for t in targets:
            config.target(t)
        options = {**base.options, **settings.suite_options.get(name, {})}
        plan[name] = SuitePlan(targets=list(targets), categories=list(base.categories), options=options)
    return plan


def build_options(config: AgentBenchConfig, s: RunSettings) -> RunOptions:
    opts = config.run.model_copy(deep=True)
    for attr in ("repeats", "concurrency", "seed", "timeout_s", "cache"):
        v = getattr(s, attr)
        if v is not None:
            setattr(opts, attr, v)
    if s.max_usd is not None:
        opts.budget.max_usd = s.max_usd
    return opts


def estimate(config: AgentBenchConfig, plan: dict[str, SuitePlan], settings: RunSettings) -> dict[str, Any]:
    """Rough pre-flight estimate of requests, tokens and spend (prompt chars / 4)."""
    registry = DatasetRegistry(config.datasets_path)
    opts = build_options(config, settings)
    filters = Filters(settings.categories, settings.case_ids, settings.tags, settings.limit)
    rows = []
    for name, sp in plan.items():
        suite = get_suite(name)
        items, _ = suite.load(registry, sp, filters)
        if name == "load":
            rows.append(
                {
                    "suite": name,
                    "items": len(items),
                    "targets": sp.targets,
                    "requests": "time-based",
                    "tokens": None,
                    "usd": None,
                }
            )
            continue
        prompt_tokens = 0
        for it in items:
            payload = it.payload
            text = (
                payload.input.prompt + (payload.input.system or "")
                if hasattr(payload, "input")
                else json.dumps(payload)
            )
            prompt_tokens += math.ceil(len(text) / 4)
        calls_per_item = {"tool_use": 3, "isolation": 6}.get(name, 1)
        for t in sp.targets:
            target = config.target(t)
            if target.is_agent and target.agent:
                target = config.target(target.agent.model_target)
            tin = prompt_tokens * calls_per_item * opts.repeats
            tout = len(items) * 150 * calls_per_item * opts.repeats
            rows.append(
                {
                    "suite": name,
                    "target": t,
                    "items": len(items),
                    "requests": len(items) * calls_per_item * opts.repeats,
                    "tokens": tin + tout,
                    "usd": round(target.pricing.cost(tin, tout), 4),
                }
            )
    return {
        "rows": rows,
        "total_usd": round(sum(r["usd"] or 0 for r in rows), 4),
        "budget_usd": opts.budget.max_usd,
    }


async def execute_run(
    config: AgentBenchConfig,
    settings: RunSettings,
    progress: ProgressFn | None = None,
    store: Store | None = None,
    write_artifacts: bool = True,
) -> tuple[RunRecord, Path | None]:
    plan = build_plan(config, settings)
    runner = Runner(config, progress=progress)
    request = RunRequest(
        plan=plan,
        label=settings.label,
        filters=Filters(settings.categories, settings.case_ids, settings.tags, settings.limit),
        options=build_options(config, settings),
    )
    record = await runner.run(request)
    if store is not None:
        store.save_run(record)
    out_dir = None
    if write_artifacts:
        out_dir = write_run_artifacts(config, record)
    return record, out_dir


def write_run_artifacts(config: AgentBenchConfig, record: RunRecord, gate: GateResult | None = None) -> Path:
    out_dir = config.runs_path / record.id
    out_dir.mkdir(parents=True, exist_ok=True)
    export_run(record, out_dir / "run.json")
    write_report(record, out_dir / "report.html", gate)
    write_xml(run_to_junit(record), out_dir / "junit.xml")
    (out_dir / "summary.md").write_text(run_summary_markdown(record))
    return out_dir


def open_store(config: AgentBenchConfig) -> Store:
    return Store(config.effective_database_url())


def load_run_ref(config: AgentBenchConfig, ref: str, store: Store | None = None) -> RunRecord:
    """A run reference is a JSON file path, a run id/prefix, ``latest``, ``latest~N`` or ``label:<x>``."""
    path = Path(ref)
    if path.suffix == ".json" and path.exists():
        return load_run_file(path)
    run_dir = config.runs_path / ref / "run.json"
    store = store or open_store(config)
    try:
        return store.get_run(ref)
    except KeyError:
        if run_dir.exists():
            return load_run_file(run_dir)
        raise


def gate_runs(
    config: AgentBenchConfig,
    baseline: RunRecord,
    candidate: RunRecord,
    target_map: dict[str, str] | None = None,
    gates_path: Path | None = None,
) -> GateResult:
    comparison = compare_runs(baseline, candidate, target_map, seed=config.run.seed)
    return apply_gate(comparison, load_gate_rules(gates_path or config.gates_path))


def write_gate_artifacts(
    gate: GateResult, out_dir: Path, baseline: RunRecord, candidate: RunRecord
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = gate_markdown(
        gate,
        baseline.label,
        candidate.label,
        repro_hint=f"agentbench repro {candidate.id} {{case}} --target {{target}} --suite {{suite}}",
    )
    paths = {
        "markdown": out_dir / "gate.md",
        "junit": out_dir / "gate-junit.xml",
        "json": out_dir / "gate.json",
        "report": out_dir / "report.html",
    }
    paths["markdown"].write_text(md)
    write_xml(gate_to_junit(gate), paths["junit"])
    paths["json"].write_text(json.dumps(gate.to_dict(), indent=2, default=str))
    write_report(candidate, paths["report"], gate)
    return paths


def append_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
