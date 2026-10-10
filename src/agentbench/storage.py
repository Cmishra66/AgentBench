"""Run history storage (SQLite by default, PostgreSQL via AGENTBENCH_DATABASE_URL)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from agentbench.engine.results import CaseResult, RunRecord


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    suites: Mapped[str] = mapped_column(String(200), default="")
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class ResultRow(Base):
    __tablename__ = "results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    suite: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(80))
    trial: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_results_run_suite_target", "run_id", "suite", "target_id"),
        Index("ix_results_run_status", "run_id", "status"),
    )


DETAIL_FIELDS = ("input", "expected", "evals", "metrics", "trace", "tags", "models", "model_latency_ms")


def _row_to_result(row: ResultRow) -> CaseResult:
    d = dict(row.detail or {})
    return CaseResult(
        suite=row.suite,
        case_id=row.case_id,
        category=row.category,
        target_id=row.target_id,
        trial=row.trial,
        status=row.status,
        passed=row.passed,
        score=row.score,
        latency_ms=row.latency_ms,  # type: ignore[arg-type]
        ttft_ms=row.ttft_ms,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        attempts=row.attempts,
        retries=row.retries,
        cached=row.cached,
        error=row.error,
        error_kind=row.error_kind,
        output=row.output,
        **{k: d.get(k) for k in DETAIL_FIELDS if k in d},
    )


def result_row_dict(row: ResultRow, include_detail: bool = True) -> dict[str, Any]:
    out = {
        "id": row.id,
        "run_id": row.run_id,
        "suite": row.suite,
        "case_id": row.case_id,
        "category": row.category,
        "target_id": row.target_id,
        "trial": row.trial,
        "status": row.status,
        "passed": row.passed,
        "score": row.score,
        "latency_ms": row.latency_ms,
        "ttft_ms": row.ttft_ms,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": row.cost_usd,
        "attempts": row.attempts,
        "retries": row.retries,
        "cached": row.cached,
        "error_kind": row.error_kind,
        "error": row.error,
    }
    if include_detail:
        out["output"] = row.output
        out.update(row.detail or {})
    else:
        out["output_preview"] = (row.output or "")[:160]
    return out


class Store:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

    # -- writes ------------------------------------------------------------------------------

    def save_run(self, rec: RunRecord) -> None:
        with Session(self.engine) as s, s.begin():
            s.merge(
                RunRow(
                    id=rec.id,
                    label=rec.label,
                    status=rec.status,
                    created_at=rec.created_at,
                    finished_at=rec.finished_at,
                    suites=",".join(sorted(rec.manifest.get("suites", {}).keys())),
                    git_sha=(rec.manifest.get("git") or {}).get("sha"),
                    config_hash=rec.manifest.get("config_hash"),
                    manifest=rec.manifest,
                    summary=rec.summary,
                )
            )
            s.execute(delete(ResultRow).where(ResultRow.run_id == rec.id))
            s.add_all(
                ResultRow(
                    run_id=rec.id,
                    suite=r.suite,
                    case_id=r.case_id,
                    category=r.category,
                    target_id=r.target_id,
                    trial=r.trial,
                    status=r.status,
                    passed=r.passed,
                    score=r.score,
                    latency_ms=r.latency_ms,
                    ttft_ms=r.ttft_ms,
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    cost_usd=r.cost_usd,
                    attempts=r.attempts,
                    retries=r.retries,
                    cached=r.cached,
                    error_kind=r.error_kind,
                    error=r.error,
                    output=r.output,
                    detail=json.loads(json.dumps({k: getattr(r, k) for k in DETAIL_FIELDS}, default=str)),
                )
                for r in rec.results
            )

    def update_status(self, run_id: str, status: str) -> None:
        with Session(self.engine) as s, s.begin():
            row = s.get(RunRow, run_id)
            if row:
                row.status = status

    def create_placeholder(self, run_id: str, label: str, created_at: str, suites: list[str]) -> None:
        with Session(self.engine) as s, s.begin():
            s.merge(
                RunRow(
                    id=run_id,
                    label=label,
                    status="running",
                    created_at=created_at,
                    suites=",".join(suites),
                    manifest={},
                    summary={},
                )
            )

    def delete_run(self, run_id: str) -> None:
        with Session(self.engine) as s, s.begin():
            s.execute(delete(ResultRow).where(ResultRow.run_id == run_id))
            s.execute(delete(RunRow).where(RunRow.id == run_id))

    # -- reads -------------------------------------------------------------------------------

    @staticmethod
    def _run_dict(row: RunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "label": row.label,
            "status": row.status,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
            "suites": [s for s in row.suites.split(",") if s],
            "git_sha": row.git_sha,
            "config_hash": row.config_hash,
            "summary": row.summary,
            "manifest": row.manifest,
        }

    def list_runs(
        self, limit: int = 50, suite: str | None = None, label: str | None = None
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as s:
            q = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
            if suite:
                q = q.where(RunRow.suites.contains(suite))
            if label:
                q = q.where(RunRow.label == label)
            return [self._run_dict(r) for r in s.scalars(q)]

    def resolve(self, ref: str) -> str:
        """Accepts a run id, unique id prefix, ``latest``, ``latest~N`` or ``label:<name>``."""
        with Session(self.engine) as s:
            if ref.startswith("latest"):
                offset = int(ref.split("~", 1)[1]) if "~" in ref else 0
                row = s.scalars(
                    select(RunRow)
                    .where(RunRow.status != "running")
                    .order_by(RunRow.created_at.desc())
                    .offset(offset)
                    .limit(1)
                ).first()
            elif ref.startswith("label:"):
                row = s.scalars(
                    select(RunRow).where(RunRow.label == ref[6:]).order_by(RunRow.created_at.desc()).limit(1)
                ).first()
            else:
                row = s.get(RunRow, ref)
                if row is None:
                    rows = list(s.scalars(select(RunRow).where(RunRow.id.startswith(ref)).limit(2)))
                    if len(rows) > 1:
                        raise KeyError(f"run reference '{ref}' is ambiguous")
                    row = rows[0] if rows else None
            if row is None:
                raise KeyError(f"no run matches '{ref}'")
            return row.id

    def get_run(self, ref: str, with_results: bool = True) -> RunRecord:
        run_id = self.resolve(ref)
        with Session(self.engine) as s:
            row = s.get(RunRow, run_id)
            assert row is not None
            results = []
            if with_results:
                results = [
                    _row_to_result(r)
                    for r in s.scalars(
                        select(ResultRow).where(ResultRow.run_id == run_id).order_by(ResultRow.id)
                    )
                ]
            return RunRecord(
                id=row.id,
                label=row.label,
                status=row.status,
                created_at=row.created_at,
                finished_at=row.finished_at,
                manifest=row.manifest,
                summary=row.summary,
                results=results,
            )

    def get_run_meta(self, ref: str) -> dict[str, Any]:
        run_id = self.resolve(ref)
        with Session(self.engine) as s:
            row = s.get(RunRow, run_id)
            assert row is not None
            return self._run_dict(row)

    def query_results(
        self,
        run_id: str,
        *,
        status: str | None = None,
        suite: str | None = None,
        target: str | None = None,
        category: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        with Session(self.engine) as s:
            q = select(ResultRow).where(ResultRow.run_id == run_id)
            if status == "failing":
                q = q.where(ResultRow.status.in_(["failed", "error"]))
            elif status:
                q = q.where(ResultRow.status == status)
            if suite:
                q = q.where(ResultRow.suite == suite)
            if target:
                q = q.where(ResultRow.target_id == target)
            if category:
                q = q.where(ResultRow.category == category)
            if search:
                like = f"%{search}%"
                q = q.where(
                    or_(
                        ResultRow.case_id.ilike(like),
                        ResultRow.output.ilike(like),
                        ResultRow.error.ilike(like),
                    )
                )
            total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
            rows = s.scalars(
                q.order_by(ResultRow.suite, ResultRow.case_id, ResultRow.target_id)
                .offset(offset)
                .limit(limit)
            )
            return [result_row_dict(r, include_detail=False) for r in rows], total

    def get_result(self, result_id: int) -> dict[str, Any]:
        with Session(self.engine) as s:
            row = s.get(ResultRow, result_id)
            if row is None:
                raise KeyError(f"no result {result_id}")
            return result_row_dict(row)

    def history(self, suite: str, target: str, limit: int = 30) -> list[dict[str, Any]]:
        out = []
        for run in reversed(self.list_runs(limit=limit * 3, suite=suite)):
            block = (run["summary"].get("targets", {}).get(target) or {}).get(suite)
            if block and run["status"] != "running":
                out.append(
                    {
                        "run_id": run["id"],
                        "created_at": run["created_at"],
                        "label": run["label"],
                        "git_sha": run["git_sha"],
                        "metrics": block,
                    }
                )
        return out[-limit:]

    def targets_and_suites(self) -> dict[str, list[str]]:
        pairs: dict[str, set[str]] = {}
        for run in self.list_runs(limit=200):
            for tid, suites in (run["summary"].get("targets") or {}).items():
                for s in suites:
                    pairs.setdefault(s, set()).add(tid)
        return {s: sorted(t) for s, t in pairs.items()}


def export_run(rec: RunRecord, path: Path, slim: bool = False) -> Path:
    data = rec.to_dict()
    if slim:
        for r in data["results"]:
            r["trace"] = []
            if isinstance(r.get("input"), dict) and "prompt" in r["input"]:
                r["input"] = {"prompt": r["input"]["prompt"][:200]}
            r["output"] = (r.get("output") or "")[:500]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return path


def load_run_file(path: Path) -> RunRecord:
    return RunRecord.from_dict(json.loads(Path(path).read_text()))
