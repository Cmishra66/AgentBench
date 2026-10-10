"""Self-contained HTML run report (one file, no network access needed) — ideal as a CI artifact."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from markupsafe import Markup

from agentbench.engine.results import CaseResult, RunRecord
from agentbench.regression import GateResult
from agentbench.reporting import svg
from agentbench.reporting.markdown import fmt_value, ms, pct, usd

SUITE_ORDER = ["core", "injection", "tool_use", "isolation", "load"]
SUITE_TITLES = {
    "core": "Accuracy",
    "injection": "Prompt injection",
    "tool_use": "Tool use",
    "isolation": "Context isolation",
    "load": "Load and performance",
}
MAX_FAILURES = 400


def cell_status(results: list[CaseResult]) -> str:
    statuses = [r.status for r in results]
    if all(s == "passed" for s in statuses):
        return "pass"
    if all(s == "skipped" for s in statuses):
        return "skip"
    if "passed" in statuses:
        return "flaky"
    if "error" in statuses and "failed" not in statuses:
        return "error"
    return "fail"


def repro_command(r: CaseResult, run: RunRecord) -> str:
    return f"agentbench repro {run.id} {r.case_id} --target {r.target_id} --suite {r.suite}" + (
        f" --trial {r.trial}" if r.trial else ""
    )


def build_context(run: RunRecord, gate: GateResult | None = None) -> dict[str, Any]:
    by_suite: dict[str, dict[str, list[CaseResult]]] = defaultdict(lambda: defaultdict(list))
    for r in run.results:
        by_suite[r.suite][r.target_id].append(r)
    anchors = {id(r): f"r{i}" for i, r in enumerate(run.results)}
    suites = []
    for name in [s for s in SUITE_ORDER if s in by_suite] + [s for s in by_suite if s not in SUITE_ORDER]:
        targets = by_suite[name]
        target_ids = list(targets)
        blocks = {t: run.summary["targets"].get(t, {}).get(name, {}) for t in target_ids}
        section: dict[str, Any] = {
            "name": name,
            "title": SUITE_TITLES.get(name, name),
            "targets": target_ids,
            "blocks": blocks,
            "info": run.manifest.get("suites", {}).get(name, {}),
        }
        if name != "load":
            cases: dict[str, str] = {}
            for rs in targets.values():
                for r in rs:
                    cases.setdefault(r.case_id, r.category)
            ordered = sorted(cases, key=lambda c: (cases[c], c))
            rows = []
            for t in target_ids:
                grouped: dict[str, list[CaseResult]] = defaultdict(list)
                for r in targets[t]:
                    grouped[r.case_id].append(r)
                cells = []
                for c in ordered:
                    rs = grouped.get(c, [])
                    if not rs:
                        cells.append({"case": c, "status": "none", "anchor": None, "category": cases[c]})
                        continue
                    status = cell_status(rs)
                    bad = next((r for r in rs if r.status in ("failed", "error")), rs[0])
                    cells.append(
                        {
                            "case": c,
                            "status": status,
                            "category": cases[c],
                            "anchor": anchors[id(bad)] if status != "pass" else None,
                        }
                    )
                rows.append({"target": t, "cells": cells})
            section["matrix"] = rows
            section["categories"] = sorted(set(cases.values()))
            groups: list[dict[str, Any]] = []
            for c in ordered:
                if groups and groups[-1]["name"] == cases[c]:
                    groups[-1]["span"] += 1
                else:
                    groups.append({"name": cases[c], "span": 1})
            section["groups"] = groups
            section["chart"] = Markup(
                svg.bar_ci_chart(
                    [
                        (t, blocks[t].get("accuracy"), *(blocks[t].get("accuracy_ci95") or [None, None]))
                        for t in target_ids
                    ],
                    title=f"{name} pass rate by target",
                )
            )
        else:
            charts = []
            for t in target_ids:
                tl = blocks[t].get("timeline", [])
                charts.append(
                    {
                        "target": t,
                        "rps": Markup(
                            svg.line_chart(
                                [(p["t"], p["requests"]) for p in tl], title="Requests per second", y_unit=""
                            )
                        ),
                        "p95": Markup(
                            svg.line_chart(
                                [(p["t"], p["p95_ms"]) for p in tl], title="p95 latency", y_unit="ms"
                            )
                        ),
                    }
                )
            section["load_charts"] = charts
        suites.append(section)

    failures = []
    for r in run.results:
        if r.status not in ("failed", "error") or len(failures) >= MAX_FAILURES:
            continue
        failed_evals = [e for e in r.evals if not e.get("passed") and not e.get("skipped")]
        failures.append(
            {
                "anchor": anchors[id(r)],
                "r": r,
                "failed_evals": failed_evals,
                "input_json": json.dumps(r.input, indent=2, default=str, ensure_ascii=False),
                "expected_json": json.dumps(r.expected, indent=2, default=str, ensure_ascii=False),
                "evals_json": json.dumps(r.evals, indent=2, default=str, ensure_ascii=False),
                "trace_svg": Markup(svg.trace_waterfall(r.trace)) if r.trace else "",
                "repro": repro_command(r, run),
            }
        )
    total_failing = sum(1 for r in run.results if r.status in ("failed", "error"))
    return {
        "run": run,
        "suites": suites,
        "failures": failures,
        "total_failing": total_failing,
        "failure_suites": sorted({f["r"].suite for f in failures}),
        "failure_targets": sorted({f["r"].target_id for f in failures}),
        "gate": gate,
        "comparisons": run.summary.get("comparisons", {}),
        "manifest_json": json.dumps(run.manifest, indent=2, default=str),
        "pct": _safe(pct),
        "ms": _safe(ms),
        "usd": _safe(usd),
        "fmt": lambda m, v: fmt_value(m, None if isinstance(v, Undefined) else v),
    }


def _safe(fn: Any) -> Any:
    """Formatting helpers treat missing metrics (Jinja Undefined) as "not measured"."""

    def wrapped(v: Any, *args: Any) -> Any:
        return fn(None if isinstance(v, Undefined) else v, *args)

    return wrapped


def render_report(run: RunRecord, gate: GateResult | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("report.html.j2").render(**build_context(run, gate))


def write_report(run: RunRecord, path: Path, gate: GateResult | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run, gate), encoding="utf-8")
    return path
