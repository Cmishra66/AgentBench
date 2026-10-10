"""Run AgentBench suites as ordinary pytest tests: one test per case, per target.

    pytest benchmarks/ --ab-target mock-strong --junitxml=benchmark-junit.xml
    pytest benchmarks/test_suites.py -k "core and extraction" --ab-target anthropic
    pytest benchmarks/ --ab-target mock-strong --ab-agent agent-leaky-memory   # watch isolation tests fail

This is the path for teams that already have pytest reporting, retries and selection wired into CI.
The `agentbench run` CLI is the faster path for large sweeps (it shares one event loop and connection
pool across all cases).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentbench.config import SuitePlan, load_config
from agentbench.datasets.loader import DatasetRegistry
from agentbench.engine.results import CaseResult
from agentbench.engine.runner import Runner, RunRequest
from agentbench.suites import get_suite
from agentbench.suites.base import Filters

REPO = Path(__file__).resolve().parents[1]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("agentbench")
    group.addoption("--ab-config", default=str(REPO / "configs" / "agentbench.yaml"), help="AgentBench config")
    group.addoption("--ab-target", default="mock-strong", help="Model target for core/injection/tool_use")
    group.addoption("--ab-agent", default="agent-secure", help="Agent target for the isolation suite")
    group.addoption("--ab-category", action="append", default=[], help="Only these categories")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "suite(name): AgentBench suite whose cases parametrize this test")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    marker = metafunc.definition.get_closest_marker("suite")
    if marker is None or "ab_item" not in metafunc.fixturenames:
        return
    suite_name = marker.args[0]
    cfg = load_config(metafunc.config.getoption("--ab-config"))
    suite = get_suite(suite_name)
    plan = cfg.suites.get(suite_name, SuitePlan())
    items, _ = suite.load(DatasetRegistry(cfg.datasets_path), plan,
                          Filters(categories=metafunc.config.getoption("--ab-category")))
    metafunc.parametrize("ab_item", items, ids=[i.id for i in items])


@pytest.fixture(scope="session")
def ab_config(request: pytest.FixtureRequest):
    return load_config(request.config.getoption("--ab-config"))


@pytest.fixture(scope="session")
def ab_target(request: pytest.FixtureRequest, ab_config) -> str:
    target = request.config.getoption("--ab-target")
    ok, why = ab_config.target(target).availability()
    if not ok:
        pytest.skip(f"target {target} unavailable: {why}")
    return target


@pytest.fixture(scope="session")
def ab_agent(request: pytest.FixtureRequest, ab_config) -> str:
    return request.config.getoption("--ab-agent")


@pytest.fixture(scope="session")
def run_case(ab_config):
    def _run(suite: str, target: str, case_id: str) -> CaseResult:
        request = RunRequest(plan={suite: SuitePlan(targets=[target])}, filters=Filters(case_ids=[case_id]),
                             label=f"pytest:{case_id}")
        record = asyncio.run(Runner(ab_config).run(request))
        assert record.results, f"no result for {case_id}"
        return record.results[0]

    return _run


def explain(result: CaseResult) -> str:
    lines = [f"{result.case_id} on {result.target_id}: {result.status}"]
    if result.error:
        lines.append(result.error.splitlines()[0])
    for e in result.evals:
        if e.get("required") and not e.get("passed") and not e.get("skipped"):
            lines.append(f"  failed check {e['name']}: {e.get('details')}")
    lines.append(f"  output: {(result.output or '')[:400]!r}")
    return "\n".join(lines)
