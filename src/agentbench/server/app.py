"""REST API for the dashboard: runs, results, comparisons, history, and launching new runs."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentbench import __version__, service
from agentbench.config import AgentBenchConfig
from agentbench.datasets.loader import DatasetRegistry
from agentbench.reporting.html import write_report
from agentbench.suites import available_suites, get_suite
from agentbench.suites.load import PROFILES

DASHBOARD_DIST = Path(
    os.environ.get("AGENTBENCH_DASHBOARD_DIR") or Path(__file__).resolve().parents[3] / "dashboard" / "dist"
)


class LaunchRun(BaseModel):
    suites: list[str] = Field(min_length=1)
    targets: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    label: str = Field(default="", max_length=200)
    repeats: int = Field(default=1, ge=1, le=10)
    concurrency: int = Field(default=16, ge=1, le=256)
    limit: int | None = Field(default=None, ge=1)
    seed: int | None = None
    max_usd: float | None = Field(default=None, ge=0)
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)


def create_app(cfg: AgentBenchConfig, dashboard_dir: Path | None = None) -> FastAPI:
    store = service.open_store(cfg)
    app = FastAPI(title="AgentBench API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    jobs: dict[str, dict[str, Any]] = {}
    tasks: set[asyncio.Task] = set()

    def target_info(target_id: str) -> dict[str, Any]:
        target = cfg.target(target_id)
        model = cfg.target(target.agent.model_target) if target.agent else target
        return {
            "id": target.id,
            "provider": target.provider,
            "model": model.model,
            "source": "simulation" if model.provider == "mock" else "api",
            "max_concurrency": model.max_concurrency,
            "rate_limit_rps": model.rate_limit_rps,
        }

    def resolve(ref: str) -> str:
        try:
            return store.resolve(ref)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        targets = []
        for t in cfg.targets:
            ok, why = t.availability()
            targets.append(
                {
                    **target_info(t.id),
                    "id": t.id,
                    "kind": "agent" if t.is_agent else "model",
                    "provider": t.provider,
                    "model": t.model,
                    "description": t.description,
                    "available": ok,
                    "status": why,
                    "agent": t.agent.model_dump() if t.agent else None,
                }
            )
        suites = [
            {
                "name": n,
                "description": d,
                "default_targets": cfg.suites.get(n).targets if n in cfg.suites else [],
                "categories": cfg.suites.get(n).categories if n in cfg.suites else [],
                "accepts": sorted(get_suite(n).accepts),
            }
            for n, d in available_suites().items()
        ]
        try:
            datasets = DatasetRegistry(cfg.datasets_path).summary()
        except FileNotFoundError:
            datasets = []
        return {
            "project": cfg.project,
            "targets": targets,
            "suites": suites,
            "datasets": datasets,
            "run": cfg.run.model_dump(),
            "judge": cfg.judge.model_dump(),
            "load_profiles": PROFILES,
        }

    @app.get("/api/runs")
    def runs(
        limit: int = Query(50, ge=1, le=500), suite: str | None = None, label: str | None = None
    ) -> list[dict]:
        out = []
        for r in store.list_runs(limit=limit, suite=suite, label=label):
            r = dict(r)
            r["totals"] = r["summary"].get("totals", {})
            r["targets"] = sorted((r["summary"].get("targets") or {}).keys())
            r.pop("summary")
            r.pop("manifest")
            out.append(r)
        return out

    @app.get("/api/runs/{ref}")
    def run_detail(ref: str) -> dict[str, Any]:
        return store.get_run_meta(resolve(ref))

    @app.get("/api/runs/{ref}/results")
    def run_results(
        ref: str,
        status: str | None = None,
        suite: str | None = None,
        target: str | None = None,
        category: str | None = None,
        q: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        items, total = store.query_results(
            resolve(ref),
            status=status,
            suite=suite,
            target=target,
            category=category,
            search=q,
            limit=limit,
            offset=offset,
        )
        return {"items": items, "total": total}

    @app.get("/api/runs/{ref}/matrix")
    def run_matrix(ref: str, suite: str) -> dict[str, Any]:
        """Case-by-target status grid for one suite (the dashboard's case ledger)."""
        items, _ = store.query_results(resolve(ref), suite=suite, limit=100_000)
        cells: dict[str, dict[str, Any]] = {}
        cases: dict[str, str] = {}
        for it in items:
            cases.setdefault(it["case_id"], it["category"])
            key = f"{it['target_id']}|{it['case_id']}"
            cur = cells.get(key)
            if cur is None:
                cells[key] = {
                    "status": it["status"],
                    "result_id": it["id"],
                    "trials": 1,
                    "passed": int(it["passed"]),
                }
            else:
                cur["trials"] += 1
                cur["passed"] += int(it["passed"])
                if it["status"] in ("failed", "error") and cur["status"] == "passed":
                    cur["status"], cur["result_id"] = it["status"], it["id"]
        for c in cells.values():
            if 0 < c["passed"] < c["trials"]:
                c["status"] = "flaky"
        ordered = sorted(cases, key=lambda c: (cases[c], c))
        return {"cases": [{"id": c, "category": cases[c]} for c in ordered], "cells": cells}

    @app.get("/api/results/{result_id}")
    def result_detail(result_id: int) -> dict[str, Any]:
        try:
            return store.get_result(result_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/compare")
    def compare(baseline: str, candidate: str, map: str | None = None) -> dict[str, Any]:
        target_map = dict(pair.split(":", 1) for pair in map.split(",") if ":" in pair) if map else None
        base = store.get_run(resolve(baseline))
        cand = store.get_run(resolve(candidate))
        result = service.gate_runs(cfg, base, cand, target_map)
        out = result.to_dict()
        out["baseline_run"] = {"id": base.id, "label": base.label, "created_at": base.created_at}
        out["candidate_run"] = {"id": cand.id, "label": cand.label, "created_at": cand.created_at}
        return out

    @app.get("/api/history")
    def history(suite: str, target: str, limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
        return store.history(suite, target, limit)

    @app.get("/api/series")
    def series() -> dict[str, list[str]]:
        return store.targets_and_suites()

    @app.get("/api/runs/{ref}/report", response_model=None)
    def run_report(ref: str) -> FileResponse:
        rec = store.get_run(resolve(ref))
        path = cfg.runs_path / rec.id / "report.html"
        if not path.exists():
            write_report(rec, path)
        return FileResponse(path, media_type="text/html")

    def settings_from(body: LaunchRun) -> service.RunSettings:
        return service.RunSettings(
            suites=body.suites,
            targets=body.targets,
            categories=body.categories,
            label=body.label or "dashboard",
            repeats=body.repeats,
            concurrency=body.concurrency,
            limit=body.limit,
            seed=body.seed,
            max_usd=body.max_usd,
            suite_options=body.options,
        )

    @app.post("/api/estimate")
    def estimate(body: LaunchRun) -> dict[str, Any]:
        settings = settings_from(body)
        try:
            plan = service.build_plan(cfg, settings)
            return service.estimate(cfg, plan, settings)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runs", status_code=202)
    async def launch(body: LaunchRun) -> dict[str, Any]:
        settings = settings_from(body)
        try:
            plan = service.build_plan(cfg, settings)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        job_id = secrets.token_hex(6)
        jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "done": 0,
            "total": None,
            "run_id": None,
            "error": None,
            "started": time.time(),
            "label": settings.label,
            "suites": list(plan),
            "targets": [
                target_info(tid) for tid in sorted({tid for suite in plan.values() for tid in suite.targets})
            ],
        }

        def progress(done: int, total: int, _r: Any) -> None:
            jobs[job_id].update(done=done, total=total)

        async def work() -> None:
            try:
                record, _ = await service.execute_run(cfg, settings, progress, store)
                jobs[job_id].update(status="completed", run_id=record.id)
            except Exception as exc:  # surfaced to the dashboard
                jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")

        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return jobs[job_id]

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return sorted(jobs.values(), key=lambda j: -j["started"])

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        if job_id not in jobs:
            raise HTTPException(404, "unknown job")
        return jobs[job_id]

    dist = dashboard_dir or DASHBOARD_DIST
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")
    else:

        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return (
                "<h1>AgentBench API</h1><p>The dashboard has not been built. Run <code>npm ci && npm run build"
                "</code> in <code>dashboard/</code>, or open <a href='/docs'>/docs</a> for the API.</p>"
            )

    return app
