"""Dataset registry: discovers JSONL case files and YAML scenario files under ``datasets/``."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentbench.datasets.schema import TestCase


@dataclass
class DatasetSelection:
    cases: list[TestCase]
    files: list[str] = field(default_factory=list)
    sha256: str = ""
    version: str = ""

    def describe(self) -> dict[str, Any]:
        return {"files": self.files, "sha256": self.sha256, "version": self.version, "cases": len(self.cases)}


class DatasetRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not self.root.exists():
            raise FileNotFoundError(
                f"datasets directory not found: {self.root}. Run `agentbench datasets generate`."
            )

    @property
    def version(self) -> str:
        manifest = self.root / "manifest.json"
        if manifest.exists():
            return json.loads(manifest.read_text()).get("version", "")
        return ""

    def suite_files(self, suite: str) -> list[Path]:
        folder = self.root / suite
        return sorted(folder.glob("*.jsonl")) if folder.exists() else []

    def load_cases(
        self,
        suite: str,
        categories: list[str] | None = None,
        case_ids: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> DatasetSelection:
        files = self.suite_files(suite)
        if not files:
            raise FileNotFoundError(f"no dataset files for suite '{suite}' under {self.root / suite}")
        digest = hashlib.sha256()
        cases: list[TestCase] = []
        used: list[str] = []
        for path in files:
            raw = path.read_bytes()
            digest.update(path.name.encode() + b"\0" + raw)
            used.append(str(path.relative_to(self.root)))
            for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    case = TestCase.model_validate_json(line)
                except Exception as exc:  # pragma: no cover - surfaced to the user
                    raise ValueError(f"{path}:{line_no}: invalid test case: {exc}") from exc
                cases.append(case)
        if categories:
            wanted = set(categories)
            cases = [
                c for c in cases if c.category in wanted or any(c.category.startswith(w) for w in wanted)
            ]
        if case_ids:
            wanted_ids = set(case_ids)
            cases = [c for c in cases if c.id in wanted_ids]
        if tags:
            cases = [c for c in cases if set(tags) & set(c.tags)]
        if limit:
            cases = cases[:limit]
        return DatasetSelection(cases=cases, files=used, sha256=digest.hexdigest()[:16], version=self.version)

    def load_yaml(self, rel: str) -> tuple[Any, str]:
        path = self.root / rel
        if not path.exists():
            raise FileNotFoundError(f"scenario file not found: {path}")
        raw = path.read_bytes()
        return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()[:16]

    def summary(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.root.glob("*/*.jsonl")):
            counts: dict[str, int] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    cat = json.loads(line)["category"]
                    counts[cat] = counts.get(cat, 0) + 1
            rows.append(
                {
                    "file": str(path.relative_to(self.root)),
                    "cases": sum(counts.values()),
                    "categories": counts,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                }
            )
        for path in sorted(self.root.glob("*/*.yaml")):
            data = yaml.safe_load(path.read_text())
            n = len(data.get("scenarios", [])) if isinstance(data, dict) else 0
            rows.append(
                {
                    "file": str(path.relative_to(self.root)),
                    "cases": n,
                    "categories": {"scenarios": n},
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                }
            )
        return rows
