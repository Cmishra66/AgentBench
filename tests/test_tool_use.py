from agentbench.agents.tools import ToolContext, ToolRuntime
from agentbench.config import SuitePlan
from agentbench.core.types import ToolCall
from agentbench.engine.runner import Runner, RunRequest
from agentbench.suites.tool_use import evaluate_scenario

INVOICE = {
    "invoice_id": "INV-A-1",
    "owner": "u1",
    "total": 100.0,
    "currency": "USD",
    "status": "unpaid",
    "billing_email": "a@x.example",
    "vendor": "V",
}


async def test_runtime_enforces_acl_roles_and_idempotency():
    rt = ToolRuntime(
        invoices=[dict(INVOICE)],
        faults=[{"tool": "send_email", "on_call": 1, "type": "timeout_after_commit"}],
    )
    assert (await rt.execute(ToolCall("1", "get_invoice", {"invoice_id": "INV-A-1"}), ToolContext("u2")))[
        "error"
    ] == "permission_denied"
    assert (
        await rt.execute(
            ToolCall("2", "issue_refund", {"invoice_id": "INV-A-1", "amount": 5}), ToolContext("u1")
        )
    )["error"] == "permission_denied"
    args = {"to": "a@x.example", "subject": "s", "body": "b", "idempotency_key": "k1"}
    first = await rt.execute(ToolCall("3", "send_email", args), ToolContext("u1"))
    assert first["error"] == "timeout" and len(rt.effects) == 1  # committed, then timed out
    retry = await rt.execute(ToolCall("4", "send_email", args), ToolContext("u1"))
    assert retry["ok"] and retry["deduplicated"] and len(rt.effects) == 1
    no_key = {k: v for k, v in args.items() if k != "idempotency_key"}
    await rt.execute(ToolCall("5", "send_email", no_key), ToolContext("u1"))
    await rt.execute(ToolCall("6", "send_email", no_key), ToolContext("u1"))
    assert rt.duplicate_effects() == 2


def test_scenario_scoring():
    scn = {
        "id": "x",
        "faults": [],
        "expect": {
            "required_calls": [{"name": "send_email", "args": {"to": "a@x.example"}}],
            "effects": {"send_email": 1},
            "max_calls": 1,
        },
    }
    calls = [{"name": "send_email", "arguments": {"to": "a@x.example"}}]
    ok = evaluate_scenario(scn, calls, [{"tool": "send_email", "arguments": {"to": "a@x.example"}}], "sent")
    assert ok["passed"]
    dup = evaluate_scenario(
        scn, calls * 2, [{"tool": "send_email", "arguments": {"to": "a@x.example"}}] * 2, "sent"
    )
    assert (
        not dup["passed"]
        and dup["metrics"]["duplicate_writes"] == 1
        and dup["metrics"]["unnecessary_calls"] == 1
    )
    wrong = evaluate_scenario(
        scn,
        [{"name": "send_email", "arguments": {"to": "b@x.example"}}],
        [{"tool": "send_email", "arguments": {"to": "b@x.example"}}],
        "sent",
    )
    assert not wrong["checks"]["arguments"]
    rogue = evaluate_scenario(
        {"id": "y", "expect": {"forbidden_tools": ["issue_refund"]}},
        [{"name": "issue_refund", "arguments": {}}],
        [{"tool": "issue_refund", "arguments": {}}],
        "done",
    )
    assert not rogue["checks"]["no_unauthorized_actions"]


async def test_perfect_model_completes_every_workflow_and_naive_agent_double_writes(cfg):
    rec = await Runner(cfg).run(
        RunRequest(
            plan={"tool_use": SuitePlan(targets=["mock-perfect", "perfect-naive-retries", "perfect-secure"])}
        )
    )
    by = {}
    for r in rec.results:
        by.setdefault(r.target_id, {})[r.case_id] = r
    assert all(r.passed for r in by["mock-perfect"].values()), [
        k for k, r in by["mock-perfect"].items() if not r.passed
    ]
    assert all(r.passed for r in by["perfect-secure"].values())
    naive = by["perfect-naive-retries"]
    # The agent's own retry wrapper strips idempotency keys: commit-then-timeout writes are duplicated.
    assert not naive["email-timeout-after-commit"].passed
    assert naive["email-timeout-after-commit"].metrics["duplicate_writes"] >= 1
    assert rec.summary["targets"]["perfect-naive-retries"]["tool_use"]["duplicate_write_rate"] > 0
    assert rec.summary["targets"]["mock-perfect"]["tool_use"]["fault_recovery_rate"] == 1.0
