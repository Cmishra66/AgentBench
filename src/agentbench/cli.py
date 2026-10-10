"""AgentBench command-line interface."""

from __future__ import annotations

import asyncio
import csv
import json
import random
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from agentbench import __version__
from agentbench.config import AgentBenchConfig, SuitePlan, load_config
from agentbench.reporting.markdown import ms, pct, run_summary_markdown, suite_rows, usd

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="AgentBench: test, benchmark and regression-gate AI models and agents.",
)
datasets_app = typer.Typer(no_args_is_help=True, help="Generate, list and validate datasets.")
runs_app = typer.Typer(no_args_is_help=True, help="Browse stored runs.")
baseline_app = typer.Typer(no_args_is_help=True, help="Export runs as portable baselines for CI.")
traces_app = typer.Typer(no_args_is_help=True, help="Export OpenTelemetry traces.")
judge_app = typer.Typer(no_args_is_help=True, help="Validate the LLM judge against human labels.")
agent_app = typer.Typer(no_args_is_help=True, help="Run the reference agent as an HTTP service.")
app.add_typer(datasets_app, name="datasets")
app.add_typer(runs_app, name="runs")
app.add_typer(baseline_app, name="baseline")
app.add_typer(traces_app, name="traces")
app.add_typer(judge_app, name="judge")
app.add_typer(agent_app, name="agent")

console = Console()
err = Console(stderr=True)

ConfigOpt = Annotated[
    Optional[Path], typer.Option("--config", "-c", help="Config file (default: configs/agentbench.yaml)")
]


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from .env without overriding variables that are already set."""
    import os

    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export "), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _cfg(path: Optional[Path]) -> AgentBenchConfig:
    load_dotenv()
    try:
        return load_config(path)
    except FileNotFoundError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _parse_map(values: list[str] | None) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise typer.BadParameter(f"expected baseline=candidate, got '{v}'")
        a, b = v.split("=", 1)
        out[a.strip()] = b.strip()
    return out


def _parse_suite_opts(values: list[str] | None) -> dict[str, dict[str, Any]]:
    """--set load.profile=ramp --set isolation.same_user_cross_session=allowed"""
    out: dict[str, dict[str, Any]] = {}
    for v in values or []:
        key, _, raw = v.partition("=")
        suite, _, opt = key.partition(".")
        if not opt:
            raise typer.BadParameter(f"expected suite.option=value, got '{v}'")
        try:
            val: Any = json.loads(raw)
        except json.JSONDecodeError:
            val = raw
        out.setdefault(suite, {})[opt] = val
    return out


def print_run_summary(record: Any) -> None:
    by_suite: dict[str, dict[str, Any]] = {}
    for tid, suites in record.summary.get("targets", {}).items():
        for s, block in suites.items():
            by_suite.setdefault(s, {})[tid] = block
    for suite, targets in by_suite.items():
        first = next(iter(targets.values()))
        heads = [h for h, _ in suite_rows(suite, first)]
        table = Table(title=f"{suite}", title_justify="left", header_style="bold")
        table.add_column("Target")
        for h in heads:
            table.add_column(h, justify="right")
        for tid, block in targets.items():
            table.add_row(tid, *[v for _, v in suite_rows(suite, block)])
        console.print(table)
    totals = record.summary.get("totals", {})
    console.print(
        f"Run [bold]{record.id}[/bold] {record.status}: {totals.get('passed')}/{totals.get('executed')} passed, "
        f"{totals.get('errors')} errors, {usd(totals.get('cost_usd'))}, {totals.get('duration_s')}s"
    )
    for w in record.manifest.get("warnings", []):
        console.print(f"[yellow]warning:[/yellow] {w}")
    for t, why in record.manifest.get("skipped_targets", {}).items():
        console.print(f"[dim]skipped {t}: {why}[/dim]")


def _progress_runner(coro_factory):
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    ) as progress:
        task = progress.add_task("Running", total=None)

        def cb(done: int, total: int, result: Any) -> None:
            progress.update(task, completed=done, total=total)

        return asyncio.run(coro_factory(cb))


# ------------------------------------------------------------------------------------------------
# top-level commands
# ------------------------------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the AgentBench version."""
    console.print(f"agentbench {__version__}")


@app.command()
def targets(config: ConfigOpt = None) -> None:
    """List configured targets and whether they are ready to run."""
    cfg = _cfg(config)
    table = Table(header_style="bold")
    for col in ("Target", "Kind", "Provider", "Model / agent", "Status"):
        table.add_column(col)
    for t in cfg.targets:
        ok, why = t.availability()
        model = f"{t.agent.preset} on {t.agent.model_target}" if t.agent else (t.model or "–")
        table.add_row(
            t.id,
            "agent" if t.is_agent else "model",
            t.provider,
            model,
            "[green]ready[/green]" if ok else f"[yellow]{why}[/yellow]",
        )
    console.print(table)


@app.command()
def suites() -> None:
    """List available test suites."""
    from agentbench.suites import available_suites

    for name, desc in available_suites().items():
        console.print(f"[bold]{name}[/bold]  {desc}")


@app.command()
def run(
    config: ConfigOpt = None,
    suite: Annotated[
        Optional[list[str]], typer.Option("--suite", "-s", help="Suite(s) to run (default: all except load)")
    ] = None,
    target: Annotated[
        Optional[list[str]], typer.Option("--target", "-t", help="Target id(s); overrides the suite plan")
    ] = None,
    category: Annotated[Optional[list[str]], typer.Option("--category", help="Only these categories")] = None,
    case: Annotated[Optional[list[str]], typer.Option("--case", help="Only these case ids")] = None,
    tag: Annotated[Optional[list[str]], typer.Option("--tag", help="Only cases with these tags")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Max cases per suite")] = None,
    label: Annotated[str, typer.Option(help="Label stored with the run (e.g. a branch or model name)")] = "",
    repeats: Annotated[Optional[int], typer.Option(help="Run each case N times")] = None,
    concurrency: Annotated[Optional[int], typer.Option(help="Concurrent cases")] = None,
    seed: Annotated[Optional[int], typer.Option()] = None,
    max_usd: Annotated[Optional[float], typer.Option(help="Abort when spend reaches this amount")] = None,
    cache: Annotated[
        Optional[bool], typer.Option("--cache/--no-cache", help="Cache temperature-0 responses")
    ] = None,
    set_opt: Annotated[
        Optional[list[str]], typer.Option("--set", help="Suite option, e.g. load.profile=ramp")
    ] = None,
    baseline: Annotated[
        Optional[str], typer.Option(help="Baseline run/file to gate against after the run")
    ] = None,
    target_map: Annotated[
        Optional[list[str]], typer.Option("--target-map", help="baseline_target=candidate_target")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Estimate requests, tokens and cost; do not run")
    ] = False,
    store: Annotated[bool, typer.Option("--store/--no-store", help="Save the run to the database")] = True,
    json_out: Annotated[
        Optional[Path], typer.Option("--json", help="Also write the full run JSON here")
    ] = None,
) -> None:
    """Run benchmark suites against targets."""
    from agentbench import service

    cfg = _cfg(config)
    settings = service.RunSettings(
        suites=suite or [],
        targets=target or [],
        categories=category or [],
        case_ids=case or [],
        tags=tag or [],
        limit=limit,
        label=label,
        repeats=repeats,
        concurrency=concurrency,
        seed=seed,
        max_usd=max_usd,
        cache=cache,
        suite_options=_parse_suite_opts(set_opt),
    )
    try:
        plan = service.build_plan(cfg, settings)
    except (KeyError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if dry_run:
        est = service.estimate(cfg, plan, settings)
        table = Table(header_style="bold")
        for col in ("Suite", "Target", "Items", "Requests", "Tokens", "Est. cost"):
            table.add_column(col, justify="right" if col not in ("Suite", "Target") else "left")
        for r in est["rows"]:
            table.add_row(
                r["suite"],
                str(r.get("target", ", ".join(r.get("targets", [])))),
                str(r["items"]),
                str(r["requests"]),
                f"{r['tokens']:,}" if r["tokens"] else "–",
                usd(r["usd"]),
            )
        console.print(table)
        console.print(
            f"Estimated spend {usd(est['total_usd'])} (budget {usd(est['budget_usd'])}). "
            "Mock targets use illustrative prices."
        )
        return
    db = service.open_store(cfg) if store else None
    record, out_dir = _progress_runner(lambda cb: service.execute_run(cfg, settings, cb, db))
    print_run_summary(record)
    if out_dir:
        console.print(f"Report: {out_dir / 'report.html'}")
    if json_out:
        from agentbench.storage import export_run

        export_run(record, json_out)
    service.append_step_summary(run_summary_markdown(record))
    if baseline:
        base = service.load_run_ref(cfg, baseline, db)
        gate_result = service.gate_runs(cfg, base, record, _parse_map(target_map))
        paths = service.write_gate_artifacts(gate_result, cfg.runs_path / record.id, base, record)
        _print_gate(gate_result)
        service.append_step_summary(paths["markdown"].read_text())
        raise typer.Exit(gate_result.exit_code)


@app.command()
def load(
    config: ConfigOpt = None,
    target: Annotated[Optional[list[str]], typer.Option("--target", "-t")] = None,
    profile: Annotated[str, typer.Option(help="smoke | steady | ramp | spike")] = "steady",
    users: Annotated[Optional[int], typer.Option(help="Virtual users (steady/smoke)")] = None,
    duration: Annotated[Optional[float], typer.Option(help="Seconds (steady/smoke)")] = None,
    pad_tokens: Annotated[
        int, typer.Option(help="Pad prompts with N filler tokens (long-context stress)")
    ] = 0,
    think_time: Annotated[float, typer.Option(help="Seconds each user waits between requests")] = 0.0,
    category: Annotated[Optional[list[str]], typer.Option("--category")] = None,
    label: Annotated[str, typer.Option()] = "",
) -> None:
    """Run a load test (steady, ramp or spike) and grade responses under load."""
    from agentbench import service

    cfg = _cfg(config)
    opts: dict[str, Any] = {"profile": profile, "pad_context_tokens": pad_tokens, "think_time_s": think_time}
    if users:
        opts["users"] = users
    if duration:
        opts["duration_s"] = duration
    settings = service.RunSettings(
        suites=["load"],
        targets=target or [],
        categories=category or [],
        label=label or f"load:{profile}",
        suite_options={"load": opts},
    )
    db = service.open_store(cfg)
    record, out_dir = _progress_runner(lambda cb: service.execute_run(cfg, settings, cb, db))
    for tid, suites_ in record.summary["targets"].items():
        block = suites_.get("load", {})
        table = Table(title=f"load: {tid}", title_justify="left", header_style="bold")
        for col in ("Stage", "Users", "Requests", "RPS", "p50", "p95", "p99", "Errors", "Pass rate"):
            table.add_column(col, justify="right")
        for st in block.get("stages", []):
            table.add_row(
                str(st["stage"]),
                str(st["users"]),
                str(st["requests"]),
                str(st["throughput_rps"]),
                ms(st["latency_ms"]["p50"]),
                ms(st["latency_ms"]["p95"]),
                ms(st["latency_ms"]["p99"]),
                pct(st["error_rate"]),
                pct(st["accuracy"]),
            )
        console.print(table)
        console.print(
            f"{block.get('total_requests')} requests, {block.get('achieved_rps')} rps average, "
            f"pass-rate drop under load {block.get('accuracy_drop_under_load_pp')}pp"
        )
    if out_dir:
        console.print(f"Report: {out_dir / 'report.html'}")


def _print_gate(gate_result: Any) -> None:
    colors = {"pass": "green", "warn": "yellow", "block": "red"}
    console.print(f"\n[bold {colors[gate_result.verdict]}]Gate verdict: {gate_result.verdict.upper()}[/]")
    for w in gate_result.comparison.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
    table = Table(header_style="bold")
    for col in ("Result", "Check", "Target", "Baseline", "Candidate", "Detail"):
        table.add_column(col)
    from agentbench.reporting.markdown import fmt_value

    for c in gate_result.checks:
        if c.status == "skipped":
            continue
        table.add_row(
            f"[{colors.get(c.status, 'white')}]{c.status}[/]",
            f"{c.suite}.{c.metric}",
            c.pair,
            fmt_value(c.metric, c.baseline),
            fmt_value(c.metric, c.candidate),
            c.detail,
        )
    console.print(table)
    for sc in gate_result.comparison.suites:
        if sc.newly_failing:
            console.print(
                f"[bold]{sc.suite}[/bold] {sc.pair}: {len(sc.newly_failing)} newly failing: "
                + ", ".join(sc.newly_failing[:10])
                + (" ..." if len(sc.newly_failing) > 10 else "")
            )


@app.command()
def compare(
    baseline: Annotated[str, typer.Argument(help="Baseline: run id, latest~1, label:<x> or a run JSON file")],
    candidate: Annotated[
        str, typer.Argument(help="Candidate: run id, latest, label:<x> or a run JSON file")
    ] = "latest",
    config: ConfigOpt = None,
    target_map: Annotated[
        Optional[list[str]], typer.Option("--target-map", help="baseline_target=candidate_target")
    ] = None,
    gates: Annotated[Optional[Path], typer.Option(help="Gate rules YAML (default from config)")] = None,
    out: Annotated[
        Optional[Path], typer.Option(help="Directory for gate.md, gate-junit.xml, gate.json, report.html")
    ] = None,
    fail_on: Annotated[Optional[str], typer.Option(help="block | warn")] = None,
) -> None:
    """Compare two runs and apply the regression gate (exit 1 when blocked)."""
    from agentbench import service

    cfg = _cfg(config)
    try:
        base = service.load_run_ref(cfg, baseline)
        cand = service.load_run_ref(cfg, candidate)
    except KeyError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    result = service.gate_runs(cfg, base, cand, _parse_map(target_map), gates)
    if fail_on:
        result.fail_on = fail_on
    _print_gate(result)
    paths = service.write_gate_artifacts(result, out or (cfg.runs_path / cand.id), base, cand)
    console.print(f"Gate summary: {paths['markdown']}\nReport: {paths['report']}")
    service.append_step_summary(paths["markdown"].read_text())
    raise typer.Exit(result.exit_code)


app.command(name="gate", help="Alias for compare.")(compare)


@app.command()
def report(
    run_ref: Annotated[str, typer.Argument(help="Run id, latest, label:<x> or run JSON file")] = "latest",
    config: ConfigOpt = None,
    out: Annotated[Optional[Path], typer.Option(help="Output HTML path")] = None,
    baseline: Annotated[
        Optional[str], typer.Option(help="Include a regression comparison against this run")
    ] = None,
    target_map: Annotated[Optional[list[str]], typer.Option("--target-map")] = None,
) -> None:
    """Render the self-contained HTML report for a run."""
    from agentbench import service
    from agentbench.reporting.html import write_report

    cfg = _cfg(config)
    rec = service.load_run_ref(cfg, run_ref)
    gate_result = None
    if baseline:
        gate_result = service.gate_runs(cfg, service.load_run_ref(cfg, baseline), rec, _parse_map(target_map))
    path = write_report(rec, out or (cfg.runs_path / rec.id / "report.html"), gate_result)
    console.print(f"Report: {path}")


@app.command()
def repro(
    run_ref: Annotated[str, typer.Argument(help="Run that contains the failure")],
    case_id: Annotated[str, typer.Argument(help="Case or scenario id")],
    target: Annotated[str, typer.Option("--target", "-t")],
    suite: Annotated[Optional[str], typer.Option("--suite", "-s")] = None,
    trial: Annotated[int, typer.Option()] = 0,
    config: ConfigOpt = None,
) -> None:
    """Re-run one stored case with the original seed and show what changed."""
    from agentbench import service

    cfg = _cfg(config)
    stored = service.load_run_ref(cfg, run_ref)
    matches = [
        r
        for r in stored.results
        if r.case_id == case_id
        and r.target_id == target
        and r.trial == trial
        and (suite is None or r.suite == suite)
    ]
    if not matches:
        err.print(f"[red]no result for case {case_id} on {target} in run {stored.id}[/red]")
        raise typer.Exit(2)
    old = matches[0]
    settings = service.RunSettings(
        suites=[old.suite],
        targets=[target],
        case_ids=[case_id],
        seed=stored.manifest.get("seed"),
        label=f"repro:{case_id}",
        repeats=trial + 1,
    )
    record, _ = _progress_runner(
        lambda cb: service.execute_run(cfg, settings, cb, None, write_artifacts=False)
    )
    new = next((r for r in record.results if r.trial == trial), record.results[0])
    console.print(f"[bold]{case_id}[/bold] on {target} ({old.suite})")
    console.print(f"stored: {old.status}  rerun: {new.status}")
    if (old.output or "").strip() == (new.output or "").strip():
        console.print("[green]Output is identical to the stored run.[/green]")
    else:
        console.print(
            "[yellow]Output differs.[/yellow]\n[bold]stored[/bold]\n"
            + (old.output or "")[:1500]
            + "\n[bold]rerun[/bold]\n"
            + (new.output or "")[:1500]
        )
    for e in new.evals:
        if not e.get("passed") and not e.get("skipped"):
            console.print(
                f"[red]failed check[/red] {e['name']}: {json.dumps(e.get('details'), default=str)[:400]}"
            )
    raise typer.Exit(0 if new.passed else 1)


@app.command()
def serve(
    config: ConfigOpt = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
) -> None:
    """Serve the dashboard and REST API."""
    import uvicorn

    from agentbench.server.app import create_app

    cfg = _cfg(config)
    console.print(f"AgentBench dashboard on http://{host}:{port}")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")


@app.command()
def demo(
    config: ConfigOpt = None,
    fast: Annotated[bool, typer.Option(help="Skip the load test")] = False,
    history: Annotated[int, typer.Option(help="Nightly runs to generate for the history view")] = 4,
) -> None:
    """Run the full demo: nightly history, a baseline, a regressed candidate, the gate, an isolation sweep
    and a load test."""
    from agentbench import service

    cfg = _cfg(config)
    db = service.open_store(cfg)
    if history:
        console.rule(f"0. Nightly history: {history} runs of mock-strong with different seeds")
        for i in range(1, history + 1):
            nightly = service.RunSettings(
                suites=["core", "injection", "tool_use"],
                targets=["mock-strong"],
                seed=100 + i,
                label=f"nightly-{i}",
            )
            rec, _ = _progress_runner(lambda cb, s=nightly: service.execute_run(cfg, s, cb, db))
            console.print(
                f"nightly-{i}: {rec.summary['totals']['passed']}/{rec.summary['totals']['executed']} passed"
            )
    console.rule("1. Baseline: mock-strong and the secure reference agent")
    base_settings = service.RunSettings(
        suites=["core", "injection", "tool_use", "isolation"], label="baseline"
    )
    base_plan_targets = {
        "core": ["mock-strong"],
        "injection": ["mock-strong"],
        "tool_use": ["mock-strong"],
        "isolation": ["agent-secure"],
    }
    base = _progress_runner(lambda cb: _run_with_targets(cfg, base_settings, base_plan_targets, cb, db))
    print_run_summary(base)

    console.rule("2. Candidate: mock-strong-v2 and an agent change that adds a shared response cache")
    cand_settings = service.RunSettings(
        suites=["core", "injection", "tool_use", "isolation"], label="candidate"
    )
    cand_targets = {
        "core": ["mock-strong-v2"],
        "injection": ["mock-strong-v2"],
        "tool_use": ["mock-strong-v2"],
        "isolation": ["agent-shared-cache"],
    }
    cand = _progress_runner(lambda cb: _run_with_targets(cfg, cand_settings, cand_targets, cb, db))
    print_run_summary(cand)

    console.rule("3. Regression gate")
    gate_result = service.gate_runs(
        cfg, base, cand, {"mock-strong": "mock-strong-v2", "agent-secure": "agent-shared-cache"}
    )
    paths = service.write_gate_artifacts(gate_result, cfg.runs_path / cand.id, base, cand)
    _print_gate(gate_result)

    console.rule("4. Full sweep: every model and every reference-agent preset")
    sweep, sweep_dir = _progress_runner(
        lambda cb: service.execute_run(cfg, service.RunSettings(label="full-sweep"), cb, db)
    )
    print_run_summary(sweep)

    if not fast:
        console.rule("5. Load test: spike profile")
        settings = service.RunSettings(
            suites=["load"],
            targets=["mock-strong"],
            label="load:spike",
            suite_options={"load": {"profile": "spike"}},
        )
        _progress_runner(lambda cb: service.execute_run(cfg, settings, cb, db))

    console.rule("Done")
    console.print(
        f"Gate report: {paths['report']}\nFull sweep report: {sweep_dir / 'report.html' if sweep_dir else ''}\n"
        "Dashboard: agentbench serve  (then open http://127.0.0.1:8000)"
    )


async def _run_with_targets(
    cfg: AgentBenchConfig, settings: Any, targets_by_suite: dict[str, list[str]], cb: Any, db: Any
) -> Any:
    from agentbench import service
    from agentbench.engine.runner import Runner, RunRequest
    from agentbench.suites.base import Filters

    plan = {
        s: SuitePlan(
            targets=targets_by_suite[s],
            categories=cfg.suites.get(s, SuitePlan()).categories,
            options=cfg.suites.get(s, SuitePlan()).options,
        )
        for s in settings.suites
    }
    record = await Runner(cfg, progress=cb).run(
        RunRequest(
            plan=plan, label=settings.label, filters=Filters(), options=service.build_options(cfg, settings)
        )
    )
    db.save_run(record)
    service.write_run_artifacts(cfg, record)
    return record


# ------------------------------------------------------------------------------------------------
# datasets
# ------------------------------------------------------------------------------------------------


@datasets_app.command("generate")
def datasets_generate(
    config: ConfigOpt = None,
    seed: Annotated[int, typer.Option()] = 42,
    out: Annotated[Optional[Path], typer.Option()] = None,
) -> None:
    """Regenerate the labelled datasets deterministically."""
    from agentbench.datasets.generator import write_datasets

    cfg = _cfg(config)
    manifest = write_datasets(out or cfg.datasets_path, seed)
    console.print(
        f"Wrote {manifest['total_cases']} cases (dataset version {manifest['version']}, seed {seed})."
    )


@datasets_app.command("list")
def datasets_list(config: ConfigOpt = None) -> None:
    """Show dataset files, case counts, categories and hashes."""
    from agentbench.datasets.loader import DatasetRegistry

    cfg = _cfg(config)
    table = Table(header_style="bold")
    for col in ("File", "Cases", "Categories", "SHA-256"):
        table.add_column(col)
    total = 0
    for row in DatasetRegistry(cfg.datasets_path).summary():
        total += row["cases"]
        table.add_row(
            row["file"],
            str(row["cases"]),
            ", ".join(f"{k} {v}" for k, v in row["categories"].items()),
            row["sha256"],
        )
    console.print(table)
    console.print(f"{total} cases and scenarios in total.")


@datasets_app.command("validate")
def datasets_validate(config: ConfigOpt = None) -> None:
    """Validate every case against the schema and evaluator registry."""
    from agentbench.datasets.loader import DatasetRegistry
    from agentbench.evaluators.base import available_evaluators

    cfg = _cfg(config)
    reg = DatasetRegistry(cfg.datasets_path)
    known = set(available_evaluators())
    problems = 0
    ids: set[str] = set()
    for suite_name in ("core", "injection"):
        for case in reg.load_cases(suite_name).cases:
            if case.id in ids:
                problems += 1
                err.print(f"duplicate id {case.id}")
            ids.add(case.id)
            for ev in case.evaluators:
                if ev["type"] not in known:
                    problems += 1
                    err.print(f"{case.id}: unknown evaluator {ev['type']}")
    for rel in ("tool_use/scenarios.yaml", "isolation/scenarios.yaml"):
        data, _ = reg.load_yaml(rel)
        for scn in data.get("scenarios", []):
            if scn["id"] in ids:
                problems += 1
                err.print(f"duplicate id {scn['id']}")
            ids.add(scn["id"])
    if problems:
        raise typer.Exit(1)
    console.print(f"[green]{len(ids)} cases and scenarios valid.[/green]")


# ------------------------------------------------------------------------------------------------
# runs, baselines, traces, judge
# ------------------------------------------------------------------------------------------------


@runs_app.command("list")
def runs_list(config: ConfigOpt = None, limit: Annotated[int, typer.Option()] = 20) -> None:
    """List recent runs."""
    from agentbench import service

    cfg = _cfg(config)
    table = Table(header_style="bold")
    for col in ("Run", "Label", "Status", "Started", "Suites", "Passed", "Cost"):
        table.add_column(col)
    for r in service.open_store(cfg).list_runs(limit=limit):
        totals = r["summary"].get("totals", {})
        table.add_row(
            r["id"],
            r["label"],
            r["status"],
            r["created_at"][:19].replace("T", " "),
            ", ".join(r["suites"]),
            f"{totals.get('passed', '–')}/{totals.get('executed', '–')}",
            usd(totals.get("cost_usd")),
        )
    console.print(table)


@runs_app.command("show")
def runs_show(run_ref: Annotated[str, typer.Argument()] = "latest", config: ConfigOpt = None) -> None:
    """Show a stored run's summary."""
    from agentbench import service

    cfg = _cfg(config)
    print_run_summary(service.load_run_ref(cfg, run_ref))


@runs_app.command("delete")
def runs_delete(
    run_ref: Annotated[str, typer.Argument()],
    config: ConfigOpt = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
) -> None:
    """Delete a stored run from the database."""
    from agentbench import service

    cfg = _cfg(config)
    store = service.open_store(cfg)
    run_id = store.resolve(run_ref)
    if not yes and not typer.confirm(f"Delete run {run_id}?"):
        raise typer.Exit(1)
    store.delete_run(run_id)
    console.print(f"Deleted {run_id}")


@baseline_app.command("export")
def baseline_export(
    run_ref: Annotated[str, typer.Argument()] = "latest",
    config: ConfigOpt = None,
    out: Annotated[Path, typer.Option()] = Path("baselines/baseline.json"),
    full: Annotated[bool, typer.Option(help="Keep traces and full prompts")] = False,
) -> None:
    """Export a run as a portable JSON baseline (commit it, or store it as a CI artifact)."""
    from agentbench import service
    from agentbench.storage import export_run

    cfg = _cfg(config)
    rec = service.load_run_ref(cfg, run_ref)
    export_run(rec, out, slim=not full)
    console.print(f"Wrote baseline {rec.id} to {out}")


@traces_app.command("export")
def traces_export(
    run_ref: Annotated[str, typer.Argument()] = "latest",
    config: ConfigOpt = None,
    out: Annotated[Optional[Path], typer.Option(help="Write OTLP/JSON to this file")] = None,
    endpoint: Annotated[
        Optional[str],
        typer.Option(help="POST to an OTLP/HTTP endpoint, e.g. http://localhost:4318/v1/traces"),
    ] = None,
) -> None:
    """Export a run's traces as OTLP/JSON (Jaeger, Tempo, Honeycomb, any OTLP collector)."""
    import httpx

    from agentbench import service
    from agentbench.engine.tracing import to_otlp_json

    cfg = _cfg(config)
    rec = service.load_run_ref(cfg, run_ref)
    spans = [s for r in rec.results for s in r.trace]
    body = to_otlp_json(spans, resource_attrs={"agentbench.run_id": rec.id, "agentbench.label": rec.label})
    if out:
        out.write_text(json.dumps(body))
        console.print(f"Wrote {len(spans)} spans to {out}")
    if endpoint:
        resp = httpx.post(endpoint, json=body, timeout=30)
        console.print(f"Exported {len(spans)} spans: HTTP {resp.status_code}")
    if not out and not endpoint:
        sys.stdout.write(json.dumps(body))


@judge_app.command("export")
def judge_export(
    run_ref: Annotated[str, typer.Argument()] = "latest",
    config: ConfigOpt = None,
    sample: Annotated[int, typer.Option()] = 50,
    out: Annotated[Path, typer.Option()] = Path("judge_review.csv"),
) -> None:
    """Export a random sample of judge decisions for human labelling."""
    from agentbench import service

    cfg = _cfg(config)
    rec = service.load_run_ref(cfg, run_ref)
    rows = []
    for r in rec.results:
        for e in r.evals:
            if e.get("type") == "llm_judge" and not e.get("skipped"):
                rows.append(
                    {
                        "run_id": rec.id,
                        "case_id": r.case_id,
                        "target": r.target_id,
                        "question": (r.input.get("prompt") or "")[-300:],
                        "answer": r.output[:800],
                        "judge_verdict": e["details"].get("verdict"),
                        "judge_score": e["details"].get("score"),
                        "judge_reason": e["details"].get("reason"),
                        "human_verdict": "",
                    }
                )
    random.Random(cfg.run.seed).shuffle(rows)
    rows = rows[:sample]
    if not rows:
        err.print("[yellow]no judge decisions in this run[/yellow]")
        raise typer.Exit(1)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    console.print(
        f"Wrote {len(rows)} rows to {out}. Fill in human_verdict (pass/fail), then run "
        f"`agentbench judge agreement {out}`."
    )


@judge_app.command("agreement")
def judge_agreement(path: Annotated[Path, typer.Argument()]) -> None:
    """Measure judge-vs-human agreement (accuracy and Cohen's kappa) from a labelled CSV."""
    from agentbench.metrics.stats import cohen_kappa

    with path.open() as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("human_verdict", "").strip()]
    if not rows:
        err.print("[red]no rows with human_verdict filled in[/red]")
        raise typer.Exit(1)
    judge = [r["judge_verdict"].strip().lower() for r in rows]
    human = [r["human_verdict"].strip().lower() for r in rows]
    agree = sum(a == b for a, b in zip(judge, human, strict=True)) / len(rows)
    kappa = cohen_kappa(judge, human)
    console.print(f"{len(rows)} labelled decisions: agreement {agree * 100:.1f}%, Cohen's kappa {kappa:.3f}")
    if kappa < 0.6:
        console.print(
            "[yellow]Kappa below 0.6: treat judge scores as advisory and revisit the rubric.[/yellow]"
        )


@agent_app.command("serve")
def agent_serve(
    preset: Annotated[
        str, typer.Option(help="Reference agent preset (secure, leaky-memory, ...)")
    ] = "secure",
    model: Annotated[str, typer.Option(help="Model target id from the config")] = "mock-strong",
    config: ConfigOpt = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8080,
) -> None:
    """Serve the reference agent over the AgentBench agent protocol (for HTTP-agent and Playwright tests)."""
    import uvicorn

    from agentbench.agents.server import create_agent_app

    cfg = _cfg(config)
    console.print(f"Reference agent ({preset} on {model}) on http://{host}:{port}")
    uvicorn.run(create_agent_app(cfg, preset, model), host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    app()
