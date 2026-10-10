import pytest

from agentbench.config import SuitePlan
from agentbench.engine.runner import Runner, RunRequest


async def test_load_suite_reports_stages_and_quality(cfg):
    plan = {
        "load": SuitePlan(
            targets=["mock-perfect"],
            categories=["classification"],
            options={
                "stages": [{"users": 2, "duration_s": 0.3}, {"users": 4, "duration_s": 0.3}],
                "max_requests": 80,
                "think_time_s": 0.01,
            },
        )
    }
    rec = await Runner(cfg).run(RunRequest(plan=plan))
    block = rec.summary["targets"]["mock-perfect"]["load"]
    assert block["total_requests"] == 80
    assert len(block["stages"]) == 2
    assert all(s["accuracy"] == 1.0 for s in block["stages"] if s["requests"])
    assert rec.manifest["suites"]["load"]["runs"]["mock-perfect"]["total_requests"] == 80


async def test_load_profiles_and_padding(cfg):
    from agentbench.suites.load import LoadSuite

    assert len(LoadSuite.stages({"profile": "spike"})) == 3
    assert LoadSuite.stages({"profile": "steady", "users": 3, "duration_s": 1})[0] == {
        "users": 3,
        "duration_s": 1,
    }
    plan = {
        "load": SuitePlan(
            targets=["mock-perfect"],
            categories=["classification"],
            options={
                "stages": [{"users": 1, "duration_s": 0.2}],
                "max_requests": 5,
                "pad_context_tokens": 2000,
            },
        )
    }
    rec = await Runner(cfg).run(RunRequest(plan=plan))
    assert min(r.input_tokens for r in rec.results) > 2000


@pytest.mark.parametrize(
    "options",
    [
        {"max_requests": 0},
        {"max_inflight": 0},
        {"stages": [{"users": 0, "duration_s": 1}]},
        {"stages": [{"users": 1, "duration_s": -1}]},
        {"duration_s": float("inf")},
        {"min_pass_rate": 1.1},
        {"latency_slo_ms": 0},
        {"profile": "missing"},
    ],
)
def test_load_rejects_invalid_options(options):
    from agentbench.suites.load import LoadSuite

    with pytest.raises(ValueError):
        LoadSuite.stages(options)


async def test_request_cap_keeps_unreached_stages_and_exact_timing(cfg):
    plan = {
        "load": SuitePlan(
            targets=["mock-perfect"],
            categories=["classification"],
            options={
                "stages": [{"users": 2, "duration_s": 1}, {"users": 4, "duration_s": 1}],
                "max_requests": 2,
                "min_samples": 1,
            },
        )
    }
    record = await Runner(cfg).run(RunRequest(plan=plan))
    block = record.summary["targets"]["mock-perfect"]["load"]
    assert block["stop_reason"] == "max_requests"
    assert block["total_requests"] == 2
    assert block["stages"][0]["planned_duration_s"] == 1
    assert block["stages"][0]["duration_s"] < 1
    assert block["stages"][1]["slo_verdict"] == "not_run"
    assert block["slo_verdict"] == "insufficient_samples"
    assert block == {**block, **record.manifest["suites"]["load"]["runs"]["mock-perfect"]}


async def test_two_targets_use_same_case_cycle_and_limit(cfg):
    plan = {
        "load": SuitePlan(
            targets=["mock-perfect", "mock-strong"],
            categories=["classification"],
            options={
                "stages": [{"users": 4, "duration_s": 0.2}],
                "max_requests": 6,
                "max_inflight": 2,
            },
        )
    }
    record = await Runner(cfg).run(RunRequest(plan=plan))
    by_target = {}
    for target in ("mock-perfect", "mock-strong"):
        results = sorted((r for r in record.results if r.target_id == target), key=lambda r: r.trial)
        by_target[target] = [r.case_id for r in results]
        block = record.summary["targets"][target]["load"]
        assert len(results) == 6
        assert block["effective_max_inflight"] == 2
        assert block["peak_api_concurrency"] <= 2
        assert all(r.expected is not None and r.evals for r in results)
    assert by_target["mock-perfect"] == by_target["mock-strong"]
    assert "load" not in record.summary["comparisons"]


def test_goodput_slo_and_recovered_errors_are_distinct():
    from agentbench.engine.results import CaseResult
    from agentbench.suites.load import LoadSuite

    results = []
    for index, (passed, status, latency) in enumerate(
        ((True, "passed", 200), (True, "passed", 800), (False, "error", 1000))
    ):
        results.append(
            CaseResult(
                suite="load",
                case_id=str(index),
                category="stage-1",
                target_id="model",
                trial=index,
                status=status,
                passed=passed,
                score=float(passed),
                latency_ms=latency,
                error_kind="timeout" if status == "error" else None,
                metrics={
                    "stage": 1,
                    "queue_wait_ms": 20,
                    "api_latency_ms": [latency - 20],
                    "peak_api_concurrency": 2,
                    "attempt_error_counts": {"rate_limit": 2} if index == 0 else {},
                },
            )
        )
    report = LoadSuite.load_report(
        results, [{"users": 4, "duration_s": 2}], 2, options={"latency_slo_ms": 500, "min_samples": 1}
    )
    assert report["goodput_rps"] == 1
    assert report["slo_goodput_rps"] == 0.5
    assert report["slo_pass_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert report["error_counts"] == {"timeout": 1}
    assert report["attempt_error_counts"] == {"rate_limit": 2}
    assert report["slo_verdict"] == "fail"
    assert report["latency_ms"]["p95"] > 800  # error latency is part of the user's wait
