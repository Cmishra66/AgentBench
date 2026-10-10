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
