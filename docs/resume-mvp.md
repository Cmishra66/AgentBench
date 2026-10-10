# AgentBench — LLM Evaluation and Load Testing MVP

AgentBench lets a developer select models, run labelled test cases and configurable load profiles,
and compare correctness, latency, throughput and estimated token cost in a dashboard. Failed cases
include the prompt, expected answer, actual response and evaluation checks for debugging.

**Stack:** Python, FastAPI, asyncio, React, TypeScript, SQLAlchemy, SQLite, OpenRouter, pytest, Playwright.

## Resume entry

**AgentBench | LLM Evaluation and Load Testing Platform | Personal Project (MVP)**

- Developed a Python/FastAPI backend and React/TypeScript dashboard to evaluate and compare Nex N2.5
  Mini and NVIDIA Nemotron 3 Ultra through real OpenRouter API integrations, with SQLite run history.
- Implemented configurable load profiles, concurrency and request limits, and measurements for
  p50/p95/p99 latency, client queue time, throughput, retries and correct answers per second.
- Added failure inspection, HTML/JUnit reports and automated regression checks; validated the
  platform with 80 Python unit/integration tests and 15 Playwright browser/API checks.

## MVP scope

1. Select available model targets and configure a test run.
2. Grade responses against expected answers and deterministic evaluation rules.
3. Run smoke, steady, ramp, spike, stress, soak or custom load stages.
4. Compare model results, including quality under load and stage latency/error objectives.
5. Inspect failed cases and retain reproducible run records and reports.

The repository also includes prompt-injection, tool-use and session-isolation suites, provider
adapters, a CLI and GitHub Actions workflows. These extend the MVP; the resume entry above focuses
on model evaluation and load testing.

## Interview explanation

“I built a testing platform for LLM applications. It sends the same configured workload to selected
models, checks their answers against labelled expectations, and records correctness, latency, errors
and token usage. I integrated Nex and Nemotron through OpenRouter and added load stages to distinguish
client queueing from provider response time. The dashboard helps inspect failures and compare runs.”

## Evidence and project status

The test datasets are synthetic. The Nex and Nemotron integrations use real API responses. Offline
mock targets provide repeatable tests of the platform and simulated failure scenarios.

Local verification run `20261010-123732-9fc7` recorded one passing answer from each model; Nemotron
recovered after one timeout. This verifies the integrations, while larger runs are needed for
conclusions about model quality or sustained capacity. Run JSON and HTML reports are generated in
`runs/` locally and are excluded from version control.

This is a locally tested personal-project MVP. The resume entry describes implemented features and
validation, without asserting production adoption or measured performance improvements.
