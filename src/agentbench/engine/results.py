"""Result records produced by every suite."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["passed", "failed", "error", "skipped"]


@dataclass
class CaseResult:
    suite: str
    case_id: str
    category: str
    target_id: str
    trial: int
    status: Status
    passed: bool
    score: float
    latency_ms: float | None = None
    ttft_ms: float | None = None
    model_latency_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 0
    retries: int = 0
    cached: bool = False
    error: str | None = None
    error_kind: str | None = None
    output: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    evals: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseResult:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def key(self) -> tuple[str, str]:
        return self.suite, self.case_id


@dataclass
class RunRecord:
    id: str
    label: str
    status: str
    created_at: str
    finished_at: str | None
    manifest: dict[str, Any]
    summary: dict[str, Any]
    results: list[CaseResult] = field(default_factory=list)

    def to_dict(self, include_results: bool = True) -> dict[str, Any]:
        d = {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "manifest": self.manifest,
            "summary": self.summary,
        }
        if include_results:
            d["results"] = [r.to_dict() for r in self.results]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        return cls(
            id=data["id"],
            label=data.get("label", ""),
            status=data.get("status", "completed"),
            created_at=data.get("created_at", ""),
            finished_at=data.get("finished_at"),
            manifest=data.get("manifest", {}),
            summary=data.get("summary", {}),
            results=[CaseResult.from_dict(r) for r in data.get("results", [])],
        )
