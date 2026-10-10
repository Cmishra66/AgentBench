# Agent protocol

Any agent, written in any language, can be tested by AgentBench if it exposes this small HTTP
contract. Point a `http_agent` target at it:

```yaml
targets:
  - id: my-agent
    provider: http_agent
    base_url: http://localhost:8080
    api_key_env: MY_AGENT_TOKEN        # optional, sent as "Authorization: Bearer ..."
    pricing: {input_per_mtok: 3.0, output_per_mtok: 15.0}   # applied to the usage you report
```

## `POST /chat` (required)

Request:

```json
{
  "user_id": "alice-20261010-1",
  "session_id": "s1-alice-20261010-1",
  "message": "What is the total on invoice INV-ALPHA-7319?",
  "metadata": {"case_id": "cross-user-tool-lookup", "trial": 0, "seed": 42}
}
```

Response:

```json
{
  "reply": "Invoice INV-ALPHA-7319 totals USD 1,284.50 and is unpaid.",
  "tool_calls": [{"name": "get_invoice", "arguments": {"invoice_id": "INV-ALPHA-7319"}, "ok": true}],
  "usage": {"input_tokens": 812, "output_tokens": 41},
  "model": "your-model-id"
}
```

* `user_id` and `session_id` are the isolation boundaries under test. Treat them exactly as your
  production auth and session layer would.
* `tool_calls` is optional but lets the tool-use suite score tool selection and arguments.
* `usage` is optional; when present, spend is tracked against the target's pricing and budget.
* Non-2xx responses are treated as provider errors. `429`, `5xx` and timeouts are retried with
  backoff; `4xx` errors are not.

## `POST /reset` (optional)

Clears all agent state. AgentBench does not rely on it for correctness: every isolation scenario
uses user and session ids namespaced by run and trial, so scenarios can run concurrently against a
shared deployment.

## `POST /seed` (optional)

Lets the isolation and tool-use suites place fixtures in your data layer, so scenarios can test
access to stored records (for example, "Bob asks for Alice's invoice by id").

```json
{
  "invoices": [{"invoice_id": "INV-ALPHA-7319", "owner": "alice-...", "total": 1284.5, "currency": "USD",
                "status": "unpaid", "billing_email": "alice@acme.example", "vendor": "Acme"}],
  "users": {"alice-...": "member"},
  "faults": [{"tool": "send_email", "on_call": 1, "type": "timeout_after_commit"}],
  "enable_write_tools": true
}
```

If you do not implement `/seed`, scenarios that depend on stored records still run, but only the
conversation-memory paths are exercised.

## `GET /health` (optional)

Any `2xx` response.

## Reference implementation

`agentbench agent serve --preset secure --port 8080` serves the bundled reference agent over this
protocol (`src/agentbench/agents/server.py`). Try `--preset leaky-memory` to see what a failing
agent looks like from the outside, and use it as a template for wrapping your own agent.
