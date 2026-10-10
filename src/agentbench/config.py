"""Configuration models and YAML loading (with ${ENV} interpolation)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_CONFIG_CANDIDATES = ("agentbench.yaml", "configs/agentbench.yaml")
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

REAL_PROVIDERS = {"openai", "openai_compatible", "anthropic", "gemini"}
AGENT_PROVIDERS = {"reference_agent", "http_agent"}


class Pricing(BaseModel):
    """USD per million tokens. Fill these from your provider's pricing page; they are not hardcoded."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000


class AgentTargetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    preset: str = "secure"
    model_target: str = "mock-strong"
    options: dict[str, Any] = Field(default_factory=dict)


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    model: str = ""
    description: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    pricing: Pricing = Field(default_factory=Pricing)
    rate_limit_rps: float | None = None
    max_concurrency: int | None = None
    timeout_s: float | None = None
    max_retries: int | None = None
    requires_env: list[str] = Field(default_factory=list)
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    agent: AgentTargetConfig | None = None

    @property
    def is_agent(self) -> bool:
        return self.provider in AGENT_PROVIDERS

    def missing_env(self) -> list[str]:
        needed = list(self.requires_env)
        if self.api_key_env:
            needed.append(self.api_key_env)
        return [name for name in needed if not os.environ.get(name)]

    def availability(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "disabled in config"
        missing = self.missing_env()
        if missing:
            return False, f"missing env: {', '.join(missing)}"
        if self.provider in REAL_PROVIDERS and not self.model:
            return False, "no model id configured"
        return True, "ready"

    def describe(self) -> dict[str, Any]:
        """Reproducibility metadata (never includes secrets)."""
        data = self.model_dump(exclude={"headers"})
        data["api_key_env"] = self.api_key_env
        return data


class BudgetConfig(BaseModel):
    max_usd: float | None = 5.0
    max_tokens: int | None = 5_000_000
    max_requests: int | None = 50_000


class RunOptions(BaseModel):
    seed: int = 42
    repeats: int = 1
    concurrency: int = 16
    timeout_s: float = 30.0
    max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    cache: bool = False
    budget: BudgetConfig = Field(default_factory=BudgetConfig)

    @field_validator("repeats", "concurrency")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v


class SuitePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class JudgeConfig(BaseModel):
    target: str | None = None
    enabled: bool = True


class AgentBenchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = "agentbench"
    datasets_dir: str = "datasets"
    runs_dir: str = "runs"
    database_url: str = "sqlite:///agentbench.db"
    gates: str | None = "configs/gates.yaml"
    run: RunOptions = Field(default_factory=RunOptions)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    targets: list[TargetConfig] = Field(default_factory=list)
    suites: dict[str, SuitePlan] = Field(default_factory=dict)

    # Not part of the YAML: where the file was loaded from (paths resolve relative to it).
    base_dir: str = Field(default=".", exclude=True)

    def target(self, target_id: str) -> TargetConfig:
        for t in self.targets:
            if t.id == target_id:
                return t
        known = ", ".join(t.id for t in self.targets)
        raise KeyError(f"unknown target '{target_id}'. Configured targets: {known}")

    def resolve_path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (Path(self.base_dir) / p).resolve()

    @property
    def datasets_path(self) -> Path:
        return self.resolve_path(self.datasets_dir)

    @property
    def runs_path(self) -> Path:
        return self.resolve_path(self.runs_dir)

    @property
    def gates_path(self) -> Path | None:
        return self.resolve_path(self.gates) if self.gates else None

    def effective_database_url(self) -> str:
        url = os.environ.get("AGENTBENCH_DATABASE_URL") or self.database_url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            rel = url[len("sqlite:///") :]
            if rel and rel != ":memory:":
                url = "sqlite:///" + str(self.resolve_path(rel))
        return url

    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            return os.environ.get(name) or (default if default is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def find_config(explicit: str | Path | None = None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"config file not found: {p}")
        return p
    env = os.environ.get("AGENTBENCH_CONFIG")
    if env:
        return Path(env)
    for candidate in DEFAULT_CONFIG_CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    raise FileNotFoundError(
        "no config found. Pass --config or create agentbench.yaml / configs/agentbench.yaml"
    )


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a child config over its parent. Targets merge by id; the suites plan is replaced."""
    out = dict(parent)
    for key, value in child.items():
        if key == "targets":
            by_id = {t["id"]: t for t in parent.get("targets", [])}
            for t in value or []:
                by_id[t["id"]] = {**by_id.get(t["id"], {}), **t}
            out["targets"] = list(by_id.values())
        elif key == "suites":
            out["suites"] = value
        elif isinstance(value, dict) and isinstance(parent.get(key), dict):
            out[key] = _merge(parent[key], value)
        else:
            out[key] = value
    return out


def _read_raw(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        raise ValueError(f"config extends cycle: {' -> '.join(map(str, (*seen, path)))}")
    raw = yaml.safe_load(path.read_text()) or {}
    parent_ref = raw.pop("extends", None)
    if parent_ref:
        parent = _read_raw(path.parent / parent_ref, (*seen, path))
        raw = _merge(parent, raw)
    return raw


def load_config(path: str | Path | None = None) -> AgentBenchConfig:
    """Load a config file. ``extends: other.yaml`` inherits from another config file."""
    cfg_path = find_config(path)
    raw = _interpolate(_read_raw(cfg_path))
    cfg = AgentBenchConfig.model_validate(raw)
    # Paths in the config are relative to the repository root when the config lives in configs/.
    base = cfg_path.resolve().parent
    if base.name == "configs":
        base = base.parent
    cfg.base_dir = str(base)
    return cfg


def load_yaml(path: str | Path) -> Any:
    return _interpolate(yaml.safe_load(Path(path).read_text()))
