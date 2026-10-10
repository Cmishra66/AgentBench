# AgentBench

A model-agnostic testing, benchmarking and regression platform for AI models and agents.

Run reproducible test suites against multiple models or agents, deliberately try to break them, and
prove — with statistics, traces and a reproducible command — when a change made things worse.
AgentBench detects wrong answers, hallucinations, context leakage between users and sessions,
prompt-injection compliance, broken tool calls, duplicate writes after retries, latency regressions
and cost increases, and turns all of it into a CI gate.

Everything runs offline out of the box: simulated "mock" models and a reference agent with
deliberately seeded bugs let you exercise the whole platform with no API keys and no spend.
Add OpenAI, Anthropic, Gemini, any OpenAI-compatible server or your own HTTP agent when you are ready.

![Overview: every case for every target](docs/images/overview.png)

![Failure explorer: a leaked API key, highlighted in the transcript](docs/images/failure-explorer.png)

## Quick start

```bash
pip install -e ".[dev]"                 # Python 3.10+
cd dashboard && npm ci && npm run build && cd ..
agentbench demo                          # history, baseline, regressed candidate, gate, isolation sweep, load test
agentbench serve                         # dashboard on http://127.0.0.1:8000
```

The demo ends with a **blocked** regression gate: the candidate model's accuracy drop is
statistically significant, its prompt-injection resistance got worse, and an agent change that
introduced a shared response cache leaks one user's invoice to another.

## What it tests

| Suite | What it proves | Cases |
|---|---|---|
| `core` | Accuracy on labelled tasks: invoice extraction (4 layouts, distractors), strict JSON against a schema, grounded document QA with citations and unanswerable questions, multi-step instructions, arithmetic reasoning, classification | 160 |
| `injection` | Resistance to prompt injection hidden in untrusted content: secret exfiltration, instruction override, hidden comments, URL exfiltration, fake system messages | 20 |
| `tool_use` | Tool selection, arguments, permissions, recovery from 429/503/timeouts, and **write idempotency** — a side-effect ledger proves whether a retry sent the email twice | 17 |
| `isolation` | Context bleeding across users, sessions, caches, summaries, tool ACLs and concurrent requests, using seeded synthetic secrets and explicit access rules | 12 |
| `load` | Latency percentiles, throughput, errors and **pass rate under load** across steady, ramp and spike stages | time-based |

The isolation suite catches each bug seeded into the reference agent, and the test suite asserts it:

| Agent preset | Seeded bug | Caught by |
|---|---|---|
| `secure` | none | passes all 12 scenarios |
| `leaky-memory` | memory shared across users | 7 scenarios |
| `shared-cache` | response cache keyed by text only | shared cache, concurrent sessions |
| `no-tool-acl` | invoice tools skip ownership checks | tool lookup, shared cache |
| `race-condition` | request state stored on the shared instance | concurrent sessions |
| `summary-bleed` | summaries stored globally | summary reintroduction, long conversation |
| `user-memory` | memory shared across a user's sessions | session inheritance |
| `naive-retries` | agent retries writes without idempotency keys | duplicate writes (tool-use suite) |

## Making the numbers credible

* **Deterministic oracles first.** Exact match, JSON Schema, field-level precision/recall, citations,
  numeric faithfulness, abstention, side-effect ledgers and seeded-secret detection decide pass/fail.
  The LLM judge is a separate target, never gating, and can be checked against human labels
  (`agentbench judge export` / `judge agreement` reports Cohen's kappa).
* **Paired statistics.** Pass rates carry Wilson 95% intervals; comparisons use McNemar's exact
  test on the same cases plus a paired bootstrap CI. A drop that is not significant is reported as
  "investigate", not "block".
* **Reproducibility.** Every run records versions, git commit, config hash, dataset hashes, seed,
  sampling parameters and the model versions providers reported. `agentbench repro <run> <case>`
  reruns one stored failure.
* **Model vs agent quality.** The same model can be tested bare and inside different agent builds.
* **Cost control.** Per-run spend/token/request budgets, `--dry-run` estimates, per-target rate
  limits and concurrency, and a cache for temperature-0 requests.

See [docs/metrics.md](docs/metrics.md) for every definition.

## Using real providers

```bash
cp .env.example .env      # add keys and model ids, then fill pricing in configs/agentbench.yaml
agentbench targets        # shows which targets are ready
agentbench run --suite core --target anthropic --target openai --dry-run
agentbench run --suite core --target anthropic --target openai --label first-real-run
```

Targets without keys are skipped, never fatal. Any OpenAI-compatible server (Ollama, vLLM, LM Studio)
works through the `local` target. To test your own agent, implement the small
[agent protocol](docs/agent-protocol.md) and set `AGENT_URL`.

## Command line

```text
agentbench run        Run suites (--suite, --target, --category, --case, --repeats, --baseline, --dry-run)
agentbench load       Load test: --profile smoke|steady|ramp|spike, --users, --pad-tokens
agentbench compare    Gate a candidate against a baseline (exit 1 when blocked)
agentbench report     Self-contained HTML report for a run
agentbench repro      Rerun one stored case with the original seed
agentbench serve      Dashboard and REST API (OpenAPI docs at /docs)
agentbench agent serve  Reference agent over HTTP (for HTTP-agent and Playwright tests)
agentbench datasets   generate | list | validate
agentbench runs       list | show | delete
agentbench baseline export, traces export (OTLP/JSON), judge export | agreement
```

## Dashboard

Four screens, served by `agentbench serve`:

* **Overview** — the case ledger (every case for every target, one square each), pass rates with
  confidence intervals, suite metrics, paired comparisons, load stages.
* **Test lab** — choose suites, targets, repeats, concurrency and spend limit; estimate cost; launch a run.
* **Failure explorer** — prompt, expected, actual, failed checks, chat transcript with leaked secrets
  highlighted, tool calls and committed side effects, trace waterfall, and the repro command.
* **Regression history** — trends per target across runs, and a two-run gate comparison.

## CI

`.github/workflows/` contains lint and tests, a dashboard build, Playwright UI and API tests, a Docker
build, a pull-request **regression gate** that comments the verdict on the PR, and a nightly sweep.
Thresholds live in [configs/gates.yaml](configs/gates.yaml); see [docs/ci.md](docs/ci.md).

```bash
make gate       # what CI runs on a pull request
make baseline   # refresh baselines/main.json after an intentional change
```

## Testing the platform itself

```bash
make test        # 67 unit and integration tests, offline, ~10s
make benchmark   # every benchmark case as a pytest test with JUnit output
make e2e         # Playwright: dashboard flows, REST API, agent protocol
```

## Docker

```bash
docker compose up --build                       # PostgreSQL + dashboard + reference agent
docker compose run --rm agentbench demo --fast
docker compose --profile tracing up             # adds Jaeger; export with `agentbench traces export --endpoint`
```

## Project layout

```text
src/agentbench/   config, providers (+ mock), datasets, evaluators, engine, agents, suites,
                  metrics, regression, reporting, storage, server, cli
datasets/         labelled cases (JSONL), tool-use and isolation scenarios (YAML), documents
configs/          agentbench.yaml (targets and plan), ci.yaml, gates.yaml
dashboard/        React + TypeScript dashboard
tests/            unit and integration tests       benchmarks/  suites as pytest tests
e2e/              Playwright UI and API tests      loadtest/    Locust file for HTTP agents
docs/             architecture, metrics, writing tests, agent protocol, CI
```

## Notes

* Mock targets are simulators that solve the bundled task families with rules and inject seeded,
  realistic failures. Their numbers demonstrate the platform; they say nothing about real models.
* All datasets are synthetic. Never send confidential data to external providers from a test run.
* Real-provider adapters are covered by contract tests against recorded API shapes; verify them with
  a small `--limit` run when you add keys.

MIT licensed.
