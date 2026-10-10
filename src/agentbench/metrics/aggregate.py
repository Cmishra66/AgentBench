"""Turn case results into per-target, per-suite metrics and paired comparisons."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

from agentbench.engine.results import CaseResult
from agentbench.metrics.stats import mcnemar_exact, mean, percentile, wilson_interval

RATE_METRICS = {
    "schema_valid": "schema_valid_rate",
    "citation_correct": "citation_accuracy",
    "hallucinated": "hallucination_rate",
    "faithful": "faithfulness_rate",
    "format_ok": "format_compliance_rate",
    "judge_pass": "judge_pass_rate",
    "judge_score": "judge_mean_score",
}
SKIP_METRICS = {"field_tp", "field_fp", "field_fn", "fields_correct", "fields_total"}


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def latency_block(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": _r(percentile(values, 50), 1),
        "p95": _r(percentile(values, 95), 1),
        "p99": _r(percentile(values, 99), 1),
        "mean": _r(mean(values), 1),
        "max": _r(max(values), 1) if values else None,
        "n": len(values),
    }


def eval_metrics(results: list[CaseResult]) -> dict[str, Any]:
    collected: dict[str, list[float]] = defaultdict(list)
    tp = fp = fn = 0
    has_fields = False
    for r in results:
        for e in r.evals:
            if e.get("skipped"):
                continue
            for k, v in (e.get("metrics") or {}).items():
                if k == "field_tp":
                    tp += v
                    has_fields = True
                elif k == "field_fp":
                    fp += v
                elif k == "field_fn":
                    fn += v
                elif k not in SKIP_METRICS:
                    collected[k].append(float(v))
    out: dict[str, Any] = {}
    for k, vals in collected.items():
        out[RATE_METRICS.get(k, f"{k}_rate")] = _r(mean(vals))
    if has_fields:
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out.update(field_precision=_r(precision), field_recall=_r(recall), field_f1=_r(f1))
    return out


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    executed = [r for r in results if r.status != "skipped"]
    n = len(executed)
    passed = sum(1 for r in executed if r.passed)
    errors = [r for r in executed if r.status == "error"]
    timeouts = [r for r in errors if r.error_kind == "timeout"]
    ok = [r for r in executed if r.status != "error"]
    lat = [r.latency_ms for r in ok if r.latency_ms is not None and not r.cached]
    ttft = [r.ttft_ms for r in ok if r.ttft_ms is not None and not r.cached]
    attempts = sum(r.attempts for r in executed)
    retries = sum(r.retries for r in executed)
    cost = sum(r.cost_usd for r in executed)
    out_tokens = sum(r.output_tokens for r in executed)
    model_seconds = sum((r.model_latency_ms or 0) for r in ok if not r.cached) / 1000
    by_cat: dict[str, dict[str, Any]] = {}
    for r in executed:
        c = by_cat.setdefault(r.category, {"n": 0, "passed": 0})
        c["n"] += 1
        c["passed"] += int(r.passed)
    for c in by_cat.values():
        c["accuracy"] = _r(c["passed"] / c["n"])
    lo, hi = wilson_interval(passed, n)
    outcomes_by_case: dict[str, set[bool]] = defaultdict(set)
    for r in executed:
        outcomes_by_case[r.case_id].add(r.passed)
    models = sorted({m for r in executed for m in r.models})
    return {
        "cases": total,
        "executed": n,
        "skipped": total - n,
        "passed": passed,
        "failed": n - passed - len(errors),
        "errors": len(errors),
        "accuracy": _r(passed / n) if n else None,
        "accuracy_ci95": [_r(lo), _r(hi)],
        "error_rate": _r(len(errors) / n) if n else None,
        "timeout_rate": _r(len(timeouts) / n) if n else None,
        "requests": attempts,
        "retry_rate": _r(retries / attempts) if attempts else 0.0,
        "latency_ms": latency_block(lat),
        "ttft_ms": latency_block(ttft),
        "input_tokens": sum(r.input_tokens for r in executed),
        "output_tokens": out_tokens,
        "tokens_per_second": _r(out_tokens / model_seconds, 1) if model_seconds else None,
        "cost_usd": _r(cost, 6),
        "cost_per_case_usd": _r(cost / n, 6) if n else None,
        "cost_per_success_usd": _r(cost / passed, 6) if passed else None,
        "by_category": dict(sorted(by_cat.items())),
        "eval_metrics": eval_metrics(executed),
        "flaky_cases": sum(1 for v in outcomes_by_case.values() if len(v) > 1),
        "cached_results": sum(1 for r in executed if r.cached),
        "models": models,
    }


def majority_by_case(results: list[CaseResult]) -> dict[str, bool]:
    votes: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        if r.status != "skipped":
            votes[r.case_id].append(r.passed)
    return {cid: sum(v) * 2 > len(v) for cid, v in votes.items()}


def paired_comparison(a: list[CaseResult], b: list[CaseResult]) -> dict[str, Any]:
    pa, pb = majority_by_case(a), majority_by_case(b)
    shared = sorted(set(pa) & set(pb))
    a_only = sum(1 for c in shared if pa[c] and not pb[c])
    b_only = sum(1 for c in shared if pb[c] and not pa[c])
    acc_a = sum(pa[c] for c in shared) / len(shared) if shared else None
    acc_b = sum(pb[c] for c in shared) / len(shared) if shared else None
    return {
        "paired_cases": len(shared),
        "a_accuracy": _r(acc_a),
        "b_accuracy": _r(acc_b),
        "delta": _r(acc_b - acc_a) if shared else None,
        "a_only_passed": a_only,
        "b_only_passed": b_only,
        "mcnemar_p": _r(mcnemar_exact(a_only, b_only)),
    }


def build_summary(results: list[CaseResult], suites: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[CaseResult]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        grouped[r.target_id][r.suite].append(r)
    targets: dict[str, dict[str, Any]] = {}
    for tid, by_suite in grouped.items():
        targets[tid] = {}
        for suite_name, rs in by_suite.items():
            block = summarize(rs)
            suite = suites.get(suite_name)
            if suite is not None:
                extra = suite.aggregate(rs)
                if extra:
                    block.update(extra)
            targets[tid][suite_name] = block
    comparisons: dict[str, list[dict[str, Any]]] = {}
    suite_targets: dict[str, list[str]] = defaultdict(list)
    for tid, by_suite in grouped.items():
        for s in by_suite:
            suite_targets[s].append(tid)
    for s, tids in suite_targets.items():
        if (
            not getattr(suites.get(s), "paired_comparisons", True)
            or getattr(suites.get(s), "mode", "cases") != "cases"
        ):
            continue
        rows = []
        for ta, tb in combinations(sorted(tids), 2):
            row = {"a": ta, "b": tb, **paired_comparison(grouped[ta][s], grouped[tb][s])}
            rows.append(row)
        if rows:
            comparisons[s] = rows
    executed = [r for r in results if r.status != "skipped"]
    return {
        "targets": targets,
        "comparisons": comparisons,
        "totals": {
            "results": len(results),
            "executed": len(executed),
            "passed": sum(1 for r in executed if r.passed),
            "errors": sum(1 for r in executed if r.status == "error"),
            "skipped": len(results) - len(executed),
            "cost_usd": _r(sum(r.cost_usd for r in results), 6),
            "requests": sum(r.attempts for r in results),
        },
    }
