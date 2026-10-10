import json
from pathlib import Path

from agentbench.datasets.generator import generate_all, write_datasets
from agentbench.datasets.loader import DatasetRegistry
from agentbench.evaluators.base import available_evaluators

REPO = Path(__file__).resolve().parents[1]


def test_generator_is_deterministic_and_matches_committed_files(tmp_path):
    manifest = write_datasets(tmp_path, seed=42)
    committed = json.loads((REPO / "datasets" / "manifest.json").read_text())
    for rel, info in manifest["files"].items():
        if "cases" in info:
            assert info["sha256"] == committed["files"][rel]["sha256"], f"{rel} drifted from the generator"


def test_dataset_size_and_coverage():
    cases = generate_all(42)
    total = sum(len(v) for v in cases.values())
    assert 150 <= total <= 250
    categories = {c.category for v in cases.values() for c in v}
    for cat in ("extraction", "json_format", "doc_qa", "reasoning", "instructions", "classification"):
        assert cat in categories
    ids = [c.id for v in cases.values() for c in v]
    assert len(ids) == len(set(ids))


def test_every_case_uses_known_evaluators():
    known = set(available_evaluators())
    for cases in generate_all(42).values():
        for c in cases:
            assert c.evaluators, c.id
            for ev in c.evaluators:
                assert ev["type"] in known, (c.id, ev["type"])


def test_registry_filters_and_hash():
    reg = DatasetRegistry(REPO / "datasets")
    all_core = reg.load_cases("core")
    assert len(all_core.cases) == 160
    extraction = reg.load_cases("core", categories=["extraction"])
    assert {c.category for c in extraction.cases} == {"extraction"}
    one = reg.load_cases("core", case_ids=["docqa-001"])
    assert [c.id for c in one.cases] == ["docqa-001"]
    assert one.sha256 == all_core.sha256  # hash covers the files, not the filter
    data, digest = reg.load_yaml("isolation/scenarios.yaml")
    assert len(data["scenarios"]) >= 10 and len(digest) == 16


def test_config_extends_merges_targets_and_replaces_plan():
    from agentbench.config import load_config

    ci = load_config(REPO / "configs" / "ci.yaml")
    base = load_config(REPO / "configs" / "agentbench.yaml")
    assert {t.id for t in ci.targets} == {t.id for t in base.targets}
    assert ci.run.concurrency == 8 and ci.run.seed == base.run.seed
    assert ci.suites["core"].targets == ["mock-strong"] and "load" not in ci.suites
