"""JUnit XML so CI systems show each case (and each gate check) as a test."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from agentbench.engine.results import RunRecord
from agentbench.regression import GateResult


def run_to_junit(rec: RunRecord) -> ET.Element:
    root = ET.Element("testsuites", name=f"agentbench {rec.id}")
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in rec.results:
        groups[(r.suite, r.target_id)].append(r)
    for (suite, target), results in sorted(groups.items()):
        failures = sum(1 for r in results if r.status == "failed")
        errors = sum(1 for r in results if r.status == "error")
        skipped = sum(1 for r in results if r.status == "skipped")
        ts = ET.SubElement(
            root,
            "testsuite",
            name=f"{suite}[{target}]",
            tests=str(len(results)),
            failures=str(failures),
            errors=str(errors),
            skipped=str(skipped),
            time=f"{sum((r.latency_ms or 0) for r in results) / 1000:.3f}",
        )
        for r in results:
            name = r.case_id + (f"#{r.trial}" if r.trial else "")
            tc = ET.SubElement(
                ts,
                "testcase",
                classname=f"agentbench.{suite}.{target}",
                name=name,
                time=f"{(r.latency_ms or 0) / 1000:.3f}",
            )
            if r.status == "failed":
                failed = [
                    e for e in r.evals if e.get("required") and not e.get("passed") and not e.get("skipped")
                ]
                msg = ", ".join(e["name"] for e in failed) or "failed"
                el = ET.SubElement(tc, "failure", message=f"failed: {msg}", type="AssertionError")
                el.text = json.dumps(
                    {
                        "failed_checks": [{e["name"]: e.get("details")} for e in failed],
                        "output": (r.output or "")[:1500],
                    },
                    indent=1,
                    default=str,
                )
            elif r.status == "error":
                el = ET.SubElement(
                    tc,
                    "error",
                    message=(r.error or "error").splitlines()[0][:300],
                    type=r.error_kind or "error",
                )
                el.text = r.error or ""
            elif r.status == "skipped":
                ET.SubElement(tc, "skipped", message=r.error or "skipped")
    return root


def gate_to_junit(gate: GateResult) -> ET.Element:
    root = ET.Element("testsuites", name="agentbench regression gate")
    ts = ET.SubElement(
        root,
        "testsuite",
        name="regression-gate",
        tests=str(len(gate.checks)),
        failures=str(sum(1 for c in gate.checks if c.status == "block")),
        skipped=str(sum(1 for c in gate.checks if c.status == "skipped")),
    )
    for c in gate.checks:
        tc = ET.SubElement(ts, "testcase", classname=f"gate.{c.suite}.{c.pair}", name=c.rule)
        if c.status == "block":
            ET.SubElement(
                tc, "failure", message=c.detail, type="RegressionBlocked"
            ).text = f"baseline={c.baseline} candidate={c.candidate}"
        elif c.status == "skipped":
            ET.SubElement(tc, "skipped", message=c.detail)
        elif c.status == "warn":
            ET.SubElement(tc, "system-out").text = f"WARNING: {c.detail}"
    for sc in gate.comparison.suites:
        if not sc.newly_failing:
            continue
        nts = ET.SubElement(
            root,
            "testsuite",
            name=f"newly-failing.{sc.suite}[{sc.candidate_target}]",
            tests=str(len(sc.newly_failing)),
            failures=str(len(sc.newly_failing)),
        )
        for case in sc.newly_failing:
            tc = ET.SubElement(
                nts, "testcase", classname=f"agentbench.{sc.suite}.{sc.candidate_target}", name=case
            )
            ET.SubElement(tc, "failure", message="passed in baseline, fails in candidate", type="Regression")
    return root


def write_xml(root: ET.Element, path: Path) -> Path:
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
