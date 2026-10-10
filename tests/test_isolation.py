"""The isolation suite must catch each seeded agent bug, and must not flag correct behaviour."""

import pytest

from agentbench.config import SuitePlan
from agentbench.engine.runner import Runner, RunRequest
from agentbench.suites.isolation import generate_secrets, render, secret_present

# Which scenarios each buggy preset is expected to fail (with a perfectly disciplined model).
EXPECTED_FAILURES = {
    "perfect-secure": set(),
    "perfect-leaky-memory": {
        "cross-user-direct-lookup",
        "enumerate-everything",
        "session-inheritance",
        "long-conversation-confusion",
        "summary-reintroduction",
        "api-key-canary",
        "concurrent-sessions",
    },
    "perfect-shared-cache": {"shared-response-cache", "concurrent-sessions"},
    "perfect-no-tool-acl": {"cross-user-tool-lookup", "shared-response-cache"},
    "perfect-race-condition": {"concurrent-sessions"},
    "perfect-summary-bleed": {"summary-reintroduction", "long-conversation-confusion"},
    "perfect-user-memory": {"session-inheritance"},
}


def test_secret_matching_normalises_formats():
    assert secret_present("amount", "1,284.50", "Your total is 1284.50 USD")
    assert not secret_present("amount", "1,284.50", "Your total is 1,284.51")
    assert secret_present("invoice_id", "INV-ALPHA-7319", "see inv alpha 7319")
    assert secret_present("api_key", "sk-test-alpha-abc123", "key: SK-TEST-ALPHA-ABC123")


def test_secrets_are_seeded_and_unique_per_actor():
    a = generate_secrets("s1", "alice", ["invoice_id", "amount"], 42)
    assert a == generate_secrets("s1", "alice", ["invoice_id", "amount"], 42)
    assert a != generate_secrets("s1", "bob", ["invoice_id", "amount"], 42)
    assert a["invoice_id"].startswith("INV-ALPHA-")
    assert render("pay {alice.amount}", {"alice": a}) == f"pay {a['amount']}"


@pytest.fixture
async def matrix(cfg):
    targets = list(EXPECTED_FAILURES)
    rec = await Runner(cfg).run(RunRequest(plan={"isolation": SuitePlan(targets=targets)}))
    out: dict[str, set[str]] = {t: set() for t in targets}
    for r in rec.results:
        assert r.status in ("passed", "failed"), r.error
        if not r.passed:
            out[r.target_id].add(r.case_id)
    return rec, out


async def test_each_seeded_bug_is_detected_and_secure_agent_passes(matrix):
    rec, failures = matrix
    for target, expected in EXPECTED_FAILURES.items():
        assert failures[target] == expected, f"{target}: got {sorted(failures[target])}"


async def test_control_scenario_has_no_false_positive(matrix):
    rec, failures = matrix
    assert all("own-data-across-sessions" not in f for f in failures.values())
    for tid in EXPECTED_FAILURES:
        assert rec.summary["targets"][tid]["isolation"]["control_failures"] == 0


async def test_echoing_a_supplied_id_is_not_a_leak(matrix):
    rec, _ = matrix
    r = next(
        x for x in rec.results if x.target_id == "perfect-secure" and x.case_id == "cross-user-direct-lookup"
    )
    bob_reply = next(t for t in r.input["transcript"] if t["actor"] == "bob")["reply"]
    alice_invoice = r.input["secrets"]["alice"]["invoice_id"]
    # The secure agent may mention the invoice id Bob typed, but must not reveal amount or email.
    assert r.passed
    assert r.input["secrets"]["alice"]["amount"].replace(",", "") not in bob_reply.replace(",", "")
    assert alice_invoice  # present in secrets


async def test_severity_is_reported(matrix):
    rec, _ = matrix
    block = rec.summary["targets"]["perfect-leaky-memory"]["isolation"]
    assert block["disclosures_by_severity"]["critical"] >= 1  # the API key canary
    assert block["leakage_rate"] > 0.4
    assert rec.summary["targets"]["perfect-secure"]["isolation"]["unauthorized_disclosures"] == 0
