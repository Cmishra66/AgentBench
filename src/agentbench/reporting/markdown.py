"""Markdown summaries for terminals, PR comments and GitHub step summaries."""

from __future__ import annotations

from typing import Any

from agentbench.engine.results import RunRecord
from agentbench.regression import GateResult

ICON = {"pass": "✅", "warn": "⚠️", "block": "❌", "skipped": "➖"}


def pct(v: Any, nd: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{nd}f}%"


def ms(v: Any) -> str:
    return "–" if v is None else (f"{v / 1000:.2f}s" if v >= 1000 else f"{v:.0f}ms")


def usd(v: Any) -> str:
    return "–" if v is None else (f"${v:.4f}" if v < 1 else f"${v:,.2f}")


def fmt_value(metric: str, v: Any) -> str:
    if v is None:
        return "–"
    if "latency" in metric or metric.endswith("_ms") or ".p" in metric:
        return ms(v)
    if "usd" in metric or "cost" in metric:
        return usd(v)
    if isinstance(v, float) and 0 <= v <= 1 and any(k in metric for k in ("rate", "accuracy", "f1", "valid")):
        return pct(v)
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def suite_rows(suite: str, block: dict[str, Any]) -> list[tuple[str, str]]:
    """Headline metrics for a suite block, in display order."""
    rows = [
        ("Pass rate", f"{pct(block.get('accuracy'))} ({block.get('passed', 0)}/{block.get('executed', 0)})")
    ]
    if suite == "isolation":
        rows += [
            ("Leakage rate", pct(block.get("leakage_rate"))),
            ("Unauthorized disclosures", str(block.get("unauthorized_disclosures", "–"))),
        ]
    elif suite == "injection":
        rows += [("Attack success rate", pct(block.get("attack_success_rate")))]
    elif suite == "tool_use":
        rows += [
            ("Workflow completion", pct(block.get("workflow_completion_rate"))),
            ("Duplicate writes", pct(block.get("duplicate_write_rate"))),
        ]
    elif suite == "load":
        rows += [("Achieved RPS", str(block.get("achieved_rps", "–")))]
    rows += [
        ("p95 latency", ms((block.get("latency_ms") or {}).get("p95"))),
        ("Errors", pct(block.get("error_rate"))),
        ("Cost", usd(block.get("cost_usd"))),
    ]
    return rows


def run_summary_markdown(rec: RunRecord, report_url: str | None = None) -> str:
    lines = [f"## AgentBench run `{rec.id}`" + (f" — {rec.label}" if rec.label else ""), ""]
    totals = rec.summary.get("totals", {})
    lines.append(
        f"Status **{rec.status}**. {totals.get('executed', 0)} results, {totals.get('passed', 0)} passed, "
        f"{totals.get('errors', 0)} errors, cost {usd(totals.get('cost_usd'))}, "
        f"{totals.get('duration_s', 0)}s."
    )
    lines.append("")
    by_suite: dict[str, dict[str, Any]] = {}
    for tid, suites in rec.summary.get("targets", {}).items():
        for s, block in suites.items():
            by_suite.setdefault(s, {})[tid] = block
    for suite, targets in by_suite.items():
        lines += [f"### {suite}", ""]
        heads = [h for h, _ in suite_rows(suite, next(iter(targets.values())))]
        lines.append("| Target | " + " | ".join(heads) + " |")
        lines.append("|---|" + "---|" * len(heads))
        for tid, block in targets.items():
            lines.append(f"| `{tid}` | " + " | ".join(v for _, v in suite_rows(suite, block)) + " |")
        lines.append("")
    if report_url:
        lines.append(f"[Open the HTML report]({report_url})")
    return "\n".join(lines) + "\n"


def gate_markdown(
    gate: GateResult, baseline_label: str = "", candidate_label: str = "", repro_hint: str | None = None
) -> str:
    cmp = gate.comparison
    lines = [f"## {ICON[gate.verdict]} AgentBench regression gate: {gate.verdict.upper()}", ""]
    lines.append(
        f"Baseline `{cmp.baseline_id}`{f' ({baseline_label})' if baseline_label else ''} compared with "
        f"candidate `{cmp.candidate_id}`{f' ({candidate_label})' if candidate_label else ''}."
    )
    for w in cmp.warnings:
        lines.append(f"> ⚠️ {w}")
    lines.append("")
    flagged = [c for c in gate.checks if c.status in ("block", "warn")]
    if flagged:
        lines += ["| Result | Check | Target | Baseline | Candidate | Detail |", "|---|---|---|---|---|---|"]
        for c in flagged:
            lines.append(
                f"| {ICON[c.status]} {c.status} | `{c.suite}.{c.metric}` | `{c.pair}` | "
                f"{fmt_value(c.metric, c.baseline)} | {fmt_value(c.metric, c.candidate)} | {c.detail} |"
            )
        lines.append("")
    passed = [c for c in gate.checks if c.status == "pass"]
    lines.append(
        f"{len(passed)} checks passed, {len([c for c in gate.checks if c.status == 'skipped'])} skipped."
    )
    lines.append("")
    for sc in cmp.suites:
        if not sc.paired_cases:
            continue
        ci = (
            f" (95% CI {sc.delta_ci95[0] * 100:+.1f} to {sc.delta_ci95[1] * 100:+.1f}pp)"
            if sc.delta_ci95
            else ""
        )
        lines.append(f"### {sc.suite}: `{sc.pair}`")
        lines.append(
            f"{sc.paired_cases} paired cases. Pass-rate change {sc.delta * 100:+.1f}pp{ci}, "
            f"McNemar p={sc.mcnemar_p:.3g}. {len(sc.newly_failing)} newly failing, "
            f"{len(sc.newly_passing)} newly passing."
        )
        if sc.newly_failing:
            shown = ", ".join(f"`{c}`" for c in sc.newly_failing[:15])
            more = f" and {len(sc.newly_failing) - 15} more" if len(sc.newly_failing) > 15 else ""
            lines.append(f"Newly failing: {shown}{more}")
            if repro_hint:
                lines.append(
                    f"Reproduce one: `{repro_hint.format(case=sc.newly_failing[0], target=sc.candidate_target, suite=sc.suite)}`"
                )
        lines.append("")
    return "\n".join(lines) + "\n"
