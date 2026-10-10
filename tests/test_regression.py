"""Regression comparison and CI gate decisions, using synthetic run records."""

from xml.etree import ElementTree as ET

from agentbench.engine.results import CaseResult, RunRecord
from agentbench.metrics.aggregate import build_summary
from agentbench.regression import apply_gate, compare_runs
from agentbench.reporting.junit import gate_to_junit, run_to_junit
from agentbench.reporting.markdown import gate_markdown, run_summary_markdown
from agentbench.suites import get_suite

RULES = {
    "alpha": 0.05,
    "rules": [
        {"metric": "core.accuracy", "max_drop_pp": 2.0, "significance": True, "severity": "block"},
        {"metric": "core.latency_ms.p95", "max_increase_pct": 25, "severity": "warn"},
        {"metric": "isolation.new_failures", "max_value": 0, "severity": "block"},
    ],
}


def make_run(
    run_id: str,
    passes: list[bool],
    latency: float = 100.0,
    target: str = "m",
    iso_fail: set[str] | None = None,
) -> RunRecord:
    results = [
        CaseResult(
            suite="core",
            case_id=f"c{i:03d}",
            category="cat",
            target_id=target,
            trial=0,
            status="passed" if p else "failed",
            passed=p,
            score=float(p),
            latency_ms=latency,
            cost_usd=0.001,
            attempts=1,
        )
        for i, p in enumerate(passes)
    ]
    for i in range(5):
        cid = f"iso-{i}"
        ok = cid not in (iso_fail or set())
        results.append(
            CaseResult(
                suite="isolation",
                case_id=cid,
                category="cross_user",
                target_id="agent",
                trial=0,
                status="passed" if ok else "failed",
                passed=ok,
                score=float(ok),
                latency_ms=50,
                metrics={"violations": 0 if ok else 1},
            )
        )
    suites = {s: get_suite(s) for s in ("core", "isolation")}
    summary = build_summary(results, suites)
    summary["totals"]["duration_s"] = 1.0
    manifest = {
        "suites": {"core": {"dataset": {"sha256": "abc"}}, "isolation": {"dataset": {"sha256": "def"}}},
        "seed": 1,
    }
    return RunRecord(
        run_id, run_id, "completed", "2026-10-01T00:00:00+00:00", None, manifest, summary, results
    )


def test_identical_runs_pass():
    base = make_run("b", [True] * 90 + [False] * 10)
    gate = apply_gate(compare_runs(base, make_run("c", [True] * 90 + [False] * 10)), RULES)
    assert gate.verdict == "pass" and gate.exit_code == 0


def test_significant_accuracy_drop_blocks():
    base = make_run("b", [True] * 100)
    cand = make_run("c", [True] * 85 + [False] * 15)
    cmp = compare_runs(base, cand)
    core = next(s for s in cmp.suites if s.suite == "core")
    assert len(core.newly_failing) == 15 and core.mcnemar_p < 0.001
    assert core.delta_ci95[1] < 0
    gate = apply_gate(cmp, RULES)
    assert gate.verdict == "block" and gate.exit_code == 1
    assert any(c.metric == "accuracy" and c.status == "block" for c in gate.checks)


def test_small_noisy_drop_is_investigate_not_block():
    base = make_run("b", [True] * 95 + [False] * 5)
    # 3 cases flip each way plus 3 extra failures: a 3pp drop that is not statistically significant
    cand_passes = [True] * 95 + [False] * 5
    for i in (0, 1, 2, 3, 4, 5):
        cand_passes[i] = False
    for i in (95, 96, 97):
        cand_passes[i] = True
    gate = apply_gate(compare_runs(base, make_run("c", cand_passes)), RULES)
    check = next(c for c in gate.checks if c.metric == "accuracy")
    assert check.status == "warn" and "not statistically significant" in check.detail
    assert gate.exit_code == 0


def test_latency_regression_warns_and_fail_on_warn_fails():
    base = make_run("b", [True] * 50, latency=100)
    cand = make_run("c", [True] * 50, latency=140)
    gate = apply_gate(compare_runs(base, cand), {**RULES, "fail_on": "warn"})
    assert gate.verdict == "warn" and gate.exit_code == 1


def test_new_isolation_failure_blocks_and_target_map_pairs_runs():
    base = make_run("b", [True] * 50)
    cand = make_run("c", [True] * 50, target="m2", iso_fail={"iso-3"})
    cmp = compare_runs(base, cand, target_map={"m": "m2", "agent": "agent"})
    gate = apply_gate(cmp, RULES)
    iso = next(c for c in gate.checks if c.metric == "new_failures")
    assert iso.status == "block" and iso.candidate == 1
    assert gate.verdict == "block"


def test_dataset_change_is_flagged():
    base = make_run("b", [True] * 10)
    cand = make_run("c", [True] * 10)
    cand.manifest["suites"]["core"]["dataset"]["sha256"] = "zzz"
    assert any("dataset changed" in w for w in compare_runs(base, cand).warnings)


def test_reports_render():
    base = make_run("b", [True] * 100)
    cand = make_run("c", [True] * 85 + [False] * 15)
    gate = apply_gate(compare_runs(base, cand), RULES)
    md = gate_markdown(gate, repro_hint="agentbench repro c {case} --target {target}")
    assert "BLOCK" in md and "Newly failing" in md and "agentbench repro c c085" in md
    root = gate_to_junit(gate)
    assert root.find("testsuite").get("failures") != "0"
    junit = run_to_junit(cand)
    failures = [tc for tc in junit.iter("testcase") if tc.find("failure") is not None]
    assert len(failures) == 15
    ET.tostring(junit)
    assert "| `m` |" in run_summary_markdown(cand)
