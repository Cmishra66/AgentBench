"""End-to-end engine behaviour on the offline mock targets."""

from agentbench.config import SuitePlan
from agentbench.engine.runner import Runner, RunRequest
from agentbench.suites.base import Filters


async def run(cfg, plan, **kw):
    opts = cfg.run.model_copy(deep=True)
    for k, v in kw.items():
        setattr(opts, k, v)
    return await Runner(cfg).run(RunRequest(plan=plan, options=opts))


async def test_perfect_model_passes_every_labelled_case(cfg):
    """If the oracles are correct, a model that answers correctly must pass 100%."""
    rec = await run(
        cfg, {"core": SuitePlan(targets=["mock-perfect"]), "injection": SuitePlan(targets=["mock-perfect"])}
    )
    failures = [(r.case_id, r.evals) for r in rec.results if not r.passed]
    assert not failures, failures[:3]
    assert rec.summary["targets"]["mock-perfect"]["core"]["accuracy"] == 1.0
    assert rec.summary["targets"]["mock-perfect"]["injection"]["attack_success_rate"] == 0.0


async def test_runs_are_reproducible_for_a_fixed_seed(cfg):
    plan = {"core": SuitePlan(targets=["mock-fast"])}
    a = await run(cfg, plan, seed=7)
    b = await run(cfg, plan, seed=7)
    c = await run(cfg, plan, seed=8)
    outcome = lambda rec: {(r.case_id, r.passed, r.output) for r in rec.results}  # noqa: E731
    assert outcome(a) == outcome(b)
    assert outcome(a) != outcome(c)


async def test_manifest_records_what_is_needed_to_reproduce(cfg):
    rec = await run(cfg, {"core": SuitePlan(targets=["mock-strong"])}, seed=3)
    m = rec.manifest
    assert m["seed"] == 3 and m["config_hash"]
    assert m["suites"]["core"]["dataset"]["sha256"]
    assert m["targets"]["mock-strong"]["reported_models"] == ["mock-strong@1.0"]
    assert m["judge"]["target"] == "mock-judge"


async def test_repeats_and_flaky_detection(cfg):
    rec = await Runner(cfg).run(
        RunRequest(
            plan={"core": SuitePlan(targets=["mock-fast"])},
            filters=Filters(categories=["classification"]),
            options=cfg.run.model_copy(update={"repeats": 3}),
        )
    )
    assert {r.trial for r in rec.results} == {0, 1, 2}
    block = rec.summary["targets"]["mock-fast"]["core"]
    assert block["executed"] == 60
    assert block["flaky_cases"] >= 1


async def test_flaky_gateway_errors_are_retried_and_counted(cfg):
    rec = await run(cfg, {"core": SuitePlan(targets=["mock-flaky"])})
    block = rec.summary["targets"]["mock-flaky"]["core"]
    assert block["retry_rate"] > 0
    assert block["requests"] > block["executed"]


async def test_budget_aborts_the_run_cleanly(cfg):
    opts = cfg.run.model_copy(deep=True)
    opts.budget.max_requests = 20
    opts.concurrency = 1
    rec = await Runner(cfg).run(RunRequest(plan={"core": SuitePlan(targets=["mock-strong"])}, options=opts))
    assert rec.status == "aborted_budget"
    assert sum(1 for r in rec.results if r.status == "skipped") > 100
    assert rec.manifest["budget"]["exceeded"]


async def test_unavailable_targets_are_skipped_not_fatal(cfg):
    rec = await run(
        cfg,
        {"core": SuitePlan(targets=["mock-perfect", "openai"])},
    )
    assert "openai" in rec.manifest["skipped_targets"]
    assert {r.target_id for r in rec.results} == {"mock-perfect"}


async def test_traces_are_attached_with_genai_attributes(cfg):
    rec = await Runner(cfg).run(
        RunRequest(
            plan={"core": SuitePlan(targets=["mock-strong"])}, filters=Filters(case_ids=["extraction-001"])
        )
    )
    spans = rec.results[0].trace
    names = [s["name"] for s in spans]
    assert names[0].startswith("case ") or "case extraction-001" in names
    llm = next(s for s in spans if s["name"] == "gen_ai.chat")
    assert llm["attributes"]["gen_ai.usage.input_tokens"] > 0
    assert llm["parent_span_id"]
