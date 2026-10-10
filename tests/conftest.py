"""Shared fixtures. Mock latency is disabled so the framework's own tests run in seconds."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("AGENTBENCH_MOCK_TIME_SCALE", "0")

from agentbench.config import AgentBenchConfig, AgentTargetConfig, TargetConfig, load_config  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path: Path) -> AgentBenchConfig:
    config = load_config(REPO / "configs" / "agentbench.yaml")
    config.runs_dir = str(tmp_path / "runs")
    config.database_url = f"sqlite:///{tmp_path / 'test.db'}"
    config.datasets_dir = str(REPO / "datasets")
    config.run.backoff_base_s = 0.0
    config.run.backoff_max_s = 0.0
    config.targets.append(
        TargetConfig(
            id="mock-perfect",
            provider="mock",
            model="mock-perfect",
            pricing={"input_per_mtok": 1.0, "output_per_mtok": 2.0},
        )
    )
    for preset in (
        "secure",
        "leaky-memory",
        "shared-cache",
        "no-tool-acl",
        "race-condition",
        "summary-bleed",
        "user-memory",
        "naive-retries",
    ):
        config.targets.append(
            TargetConfig(
                id=f"perfect-{preset}",
                provider="reference_agent",
                agent=AgentTargetConfig(preset=preset, model_target="mock-perfect"),
            )
        )
    os.environ.pop("AGENTBENCH_DATABASE_URL", None)
    return config
