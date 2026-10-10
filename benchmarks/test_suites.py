"""Each AgentBench case as a pytest test. See conftest.py for options."""

import pytest

from conftest import explain

pytestmark = pytest.mark.benchmark


@pytest.mark.suite("core")
def test_core(ab_item, ab_target, run_case):
    result = run_case("core", ab_target, ab_item.id)
    assert result.passed, explain(result)


@pytest.mark.suite("injection")
def test_prompt_injection(ab_item, ab_target, run_case):
    result = run_case("injection", ab_target, ab_item.id)
    assert result.passed, explain(result)


@pytest.mark.suite("tool_use")
def test_tool_use(ab_item, ab_target, run_case):
    result = run_case("tool_use", ab_target, ab_item.id)
    assert result.passed, explain(result)


@pytest.mark.suite("isolation")
def test_isolation(ab_item, ab_agent, run_case):
    result = run_case("isolation", ab_agent, ab_item.id)
    assert result.passed, explain(result)
