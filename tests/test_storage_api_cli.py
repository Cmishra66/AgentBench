"""Storage round-trips, the REST API and the CLI."""

import json
import time

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentbench import service
from agentbench.cli import app
from agentbench.reporting.html import render_report
from agentbench.server.app import create_app
from agentbench.storage import Store, export_run, load_run_file


async def seeded_store(cfg):
    store = Store(cfg.effective_database_url())
    settings = service.RunSettings(
        suites=["core", "isolation"], targets=["mock-strong", "perfect-leaky-memory"], limit=10, label="first"
    )
    first, _ = await service.execute_run(cfg, settings, store=store)
    settings.label = "second"
    second, _ = await service.execute_run(cfg, settings, store=store)
    return store, first, second


async def test_store_roundtrip_and_refs(cfg, tmp_path):
    store, first, second = await seeded_store(cfg)
    assert store.resolve("latest") == second.id
    assert store.resolve("latest~1") == first.id
    assert store.resolve("label:first") == first.id
    assert store.resolve(second.id[:18]) == second.id
    loaded = store.get_run(first.id)
    assert len(loaded.results) == len(first.results)
    assert loaded.results[0].evals == first.results[0].evals
    items, total = store.query_results(first.id, status="failing", suite="isolation")
    assert total == len(items) and all(i["status"] in ("failed", "error") for i in items)
    path = export_run(first, tmp_path / "b.json", slim=True)
    assert load_run_file(path).summary == first.summary
    hist = store.history("core", "mock-strong")
    assert [h["label"] for h in hist] == ["first", "second"]


async def test_html_report_renders_every_section(cfg):
    _, first, second = await seeded_store(cfg)
    gate = service.gate_runs(cfg, first, second)
    html = render_report(second, gate)
    for needle in (
        "Every case, every target",
        "Context isolation",
        "Regression gate",
        "Reproduce",
        "agentbench repro",
    ):
        assert needle in html


async def test_api(cfg):
    _, first, second = await seeded_store(cfg)
    client = TestClient(create_app(cfg))
    assert client.get("/api/health").json()["status"] == "ok"
    runs = client.get("/api/runs").json()
    assert [r["id"] for r in runs] == [second.id, first.id]
    detail = client.get(f"/api/runs/{second.id}").json()
    assert "mock-strong" in detail["summary"]["targets"]
    res = client.get(
        f"/api/runs/{second.id}/results", params={"status": "failing", "suite": "isolation"}
    ).json()
    assert res["total"] >= 1
    one = client.get(f"/api/results/{res['items'][0]['id']}").json()
    assert one["input"]["transcript"]
    matrix = client.get(f"/api/runs/{second.id}/matrix", params={"suite": "core"}).json()
    assert len(matrix["cases"]) == 10
    gate = client.get("/api/compare", params={"baseline": first.id, "candidate": second.id}).json()
    assert gate["verdict"] in ("pass", "warn")
    assert client.get("/api/history", params={"suite": "core", "target": "mock-strong"}).status_code == 200
    assert client.get("/api/runs/nope").status_code == 404
    est = client.post("/api/estimate", json={"suites": ["core"], "targets": ["mock-strong"]}).json()
    assert est["rows"][0]["items"] == 160
    load_est = client.post(
        "/api/estimate",
        json={
            "suites": ["load"],
            "targets": ["mock-perfect", "mock-strong"],
            "options": {"load": {"max_requests": 10}},
        },
    ).json()
    assert load_est["total_usd"] is None
    assert [row["target"] for row in load_est["rows"]] == ["mock-perfect", "mock-strong"]
    assert all(row["requests"] == "time-based, up to 10" for row in load_est["rows"])
    bad = client.post("/api/runs", json={"suites": ["core"], "targets": ["not-a-target"]})
    assert bad.status_code == 422
    invalid_load = client.post(
        "/api/runs",
        json={"suites": ["load"], "targets": ["mock-perfect"], "options": {"load": {"max_inflight": 0}}},
    )
    assert invalid_load.status_code == 422
    job = client.post("/api/runs", json={"suites": ["core"], "targets": ["mock-perfect"], "limit": 3}).json()
    assert job["targets"][0]["id"] == "mock-perfect"
    assert job["targets"][0]["source"] == "simulation"
    assert "api_key" not in json.dumps(job)
    for _ in range(100):
        state = client.get(f"/api/jobs/{job['id']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "completed" and state["run_id"]
    assert client.get(f"/api/runs/{state['run_id']}/report").status_code == 200


def test_cli_end_to_end(cfg, tmp_path, monkeypatch):
    cfg_path = tmp_path / "agentbench.yaml"
    data = cfg.model_dump(mode="json")
    data["datasets_dir"] = cfg.datasets_dir
    data["gates"] = str(cfg.resolve_path("configs/gates.yaml"))
    cfg_path.write_text(json.dumps(data))  # JSON is valid YAML
    runner = CliRunner()
    r = runner.invoke(app, ["datasets", "validate", "-c", str(cfg_path)])
    assert r.exit_code == 0, r.output

    def run(target: str, label: str, *extra: str) -> None:
        res = runner.invoke(
            app,
            [
                "run",
                "-c",
                str(cfg_path),
                "--suite",
                "core",
                "--target",
                target,
                "--label",
                label,
                "--json",
                str(tmp_path / f"{label}.json"),
                *extra,
            ],
        )
        assert res.exit_code == 0, res.output

    def gate(base: str, cand: str):
        return runner.invoke(
            app,
            [
                "compare",
                str(tmp_path / f"{base}.json"),
                str(tmp_path / f"{cand}.json"),
                "-c",
                str(cfg_path),
                "--target-map",
                "mock-perfect=mock-fast",
                "--out",
                str(tmp_path / "gate"),
            ],
        )

    # Five cases cannot prove a regression: the gate asks for investigation instead of blocking.
    run("mock-perfect", "small-base", "--limit", "5")
    run("mock-fast", "small-cand", "--limit", "5")
    r = gate("small-base", "small-cand")
    assert r.exit_code == 0, r.output
    # On the full dataset the drop is significant, so the gate blocks (exit code 1).
    run("mock-perfect", "base")
    run("mock-fast", "cand")
    r = gate("base", "cand")
    assert r.exit_code == 1, r.output
    assert (tmp_path / "gate" / "gate.md").exists() and (tmp_path / "gate" / "gate-junit.xml").exists()
    r = runner.invoke(app, ["run", "-c", str(cfg_path), "--dry-run", "--suite", "core"])
    assert r.exit_code == 0 and "Estimated spend" in r.output
    r = runner.invoke(app, ["runs", "list", "-c", str(cfg_path)])
    assert "small-base" in r.output
