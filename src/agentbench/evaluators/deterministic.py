"""Deterministic oracles: exact matches, schemas, field-level scoring, grounding and safety checks."""

from __future__ import annotations

import json
import re
import string
from typing import Any

import jsonschema

from agentbench.datasets.schema import TestCase
from agentbench.evaluators.base import EvalOutcome, register

_FENCE = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------


def parse_json_strict(text: str) -> tuple[Any, str | None]:
    try:
        return json.loads(text.strip()), None
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc.msg} at char {exc.pos}"


def parse_json_lenient(text: str) -> tuple[Any, str | None]:
    """Accept markdown fences or surrounding prose; return the first JSON object found."""
    stripped = text.strip()
    obj, err = parse_json_strict(stripped)
    if err is None:
        return obj, None
    m = _FENCE.match(stripped)
    if m:
        obj, err = parse_json_strict(m.group(1))
        if err is None:
            return obj, None
    start = stripped.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj, err = parse_json_strict(stripped[start : i + 1])
                    if err is None:
                        return obj, None
                    break
        start = stripped.find("{", start + 1)
    return None, "no JSON object found in output"


def normalize_text(value: str, ops: list[str] | None = None) -> str:
    ops = ops or ["strip", "lower", "collapse_ws"]
    out = value
    if "strip" in ops:
        out = out.strip()
    if "lower" in ops:
        out = out.casefold()
    if "strip_punct" in ops:
        out = out.translate(str.maketrans("", "", string.punctuation))
    if "collapse_ws" in ops or "strip_punct" in ops:
        out = " ".join(out.split())
    return out


def _canon_string(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", value.casefold()).split())


def to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        cleaned = re.sub(r"^[A-Z]{3}\s*|[$€£]", "", cleaned)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def values_match(expected: Any, predicted: Any, tolerance: float = 0.0) -> bool:
    if expected is None:
        return predicted is None or predicted == ""
    if isinstance(expected, bool):
        return isinstance(predicted, bool) and predicted is expected
    if isinstance(expected, (int, float)):
        num = to_number(predicted)
        return num is not None and abs(num - float(expected)) <= max(tolerance, 1e-9)
    if isinstance(expected, str):
        return isinstance(predicted, str) and _canon_string(predicted) == _canon_string(expected)
    return expected == predicted


def normalize_for_search(text: str) -> str:
    t = text.casefold()
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    return " ".join(t.split())


# ------------------------------------------------------------------------------------------------
# evaluators
# ------------------------------------------------------------------------------------------------


@register("json_valid")
def json_valid(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    parser = parse_json_strict if spec.get("strict", True) else parse_json_lenient
    obj, err = parser(text)
    ok = err is None and isinstance(obj, (dict, list))
    return EvalOutcome(
        "json_valid",
        "json_valid",
        ok,
        1.0 if ok else 0.0,
        details={"error": err},
        metrics={"schema_valid": float(ok)},
    )


@register("json_schema")
def json_schema(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    strict = spec.get("strict", True)
    obj, err = (parse_json_strict if strict else parse_json_lenient)(text)
    errors: list[str] = []
    if err:
        errors.append(err)
    else:
        validator = jsonschema.Draft202012Validator(spec["schema"], format_checker=jsonschema.FormatChecker())
        errors = [
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
        ]
    ok = not errors
    return EvalOutcome(
        "json_schema",
        "json_schema",
        ok,
        1.0 if ok else 0.0,
        details={"strict": strict, "errors": errors[:10]},
        metrics={"schema_valid": float(ok)},
    )


@register("fields")
def fields(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    expected: dict[str, Any] = spec.get("expected") or case.expected or {}
    tolerance = float(spec.get("numeric_tolerance", 0.0))
    obj, err = parse_json_lenient(text)
    if not isinstance(obj, dict):
        n_expected = sum(1 for v in expected.values() if v is not None)
        return EvalOutcome(
            "fields",
            "fields",
            False,
            0.0,
            details={"error": err or "output is not a JSON object"},
            metrics={
                "field_tp": 0,
                "field_fp": 0,
                "field_fn": n_expected,
                "fields_correct": 0,
                "fields_total": len(expected),
            },
        )
    tp = fp = fn = correct = 0
    per_field: dict[str, Any] = {}
    for key, exp in expected.items():
        pred = obj.get(key)
        ok = values_match(exp, pred, tolerance)
        correct += ok
        pred_present = pred is not None and pred != ""
        if exp is not None:
            if ok:
                tp += 1
            else:
                fn += 1
                if pred_present:
                    fp += 1
        elif pred_present:
            fp += 1
        if not ok:
            per_field[key] = {"expected": exp, "actual": pred}
    total = len(expected)
    return EvalOutcome(
        "fields",
        "fields",
        correct == total,
        round(correct / total, 4) if total else 1.0,
        details={"mismatches": per_field, "correct": correct, "total": total},
        metrics={
            "field_tp": tp,
            "field_fp": fp,
            "field_fn": fn,
            "fields_correct": correct,
            "fields_total": total,
        },
    )


@register("exact")
def exact(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    ops = spec.get("normalize", ["strip"])
    expected = str(spec.get("expected", case.expected))
    ok = normalize_text(text, ops) == normalize_text(expected, ops)
    return EvalOutcome(
        "exact",
        "exact",
        ok,
        1.0 if ok else 0.0,
        details={} if ok else {"expected": expected, "actual": text.strip()[:500]},
    )


@register("regex")
def regex(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    flags = re.IGNORECASE if spec.get("ignore_case", True) else 0
    ok = re.search(spec["pattern"], text, flags) is not None
    return EvalOutcome("regex", "regex", ok, 1.0 if ok else 0.0, details={"pattern": spec["pattern"]})


@register("contains_all")
def contains_all(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    hay = normalize_for_search(text)
    groups: list[list[str]] = spec["groups"]
    missing = [g for g in groups if not any(normalize_for_search(v) in hay for v in g)]
    score = (len(groups) - len(missing)) / len(groups) if groups else 1.0
    return EvalOutcome(
        "contains_all",
        "contains_all",
        not missing,
        round(score, 4),
        details={"missing": missing} if missing else {},
    )


@register("contains_any")
def contains_any(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    hay = normalize_for_search(text)
    hits = [v for v in spec["values"] if normalize_for_search(v) in hay]
    ok = bool(hits)
    metric = spec.get("metric") or spec.get("name") or "contains_any"
    return EvalOutcome(
        "contains_any",
        "contains_any",
        ok,
        1.0 if ok else 0.0,
        details={"matched": hits},
        metrics={metric: float(ok)},
    )


@register("not_contains")
def not_contains(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    hay = normalize_for_search(text)
    hits = [v for v in spec["values"] if normalize_for_search(v) in hay]
    ok = not hits
    metric = spec.get("metric") or "violation"
    return EvalOutcome(
        "not_contains",
        "not_contains",
        ok,
        1.0 if ok else 0.0,
        details={"found": hits} if hits else {},
        metrics={metric: float(not ok)},
    )


_ANSWER = re.compile(r"ANSWER\s*[:=]\s*\$?\s*([-+]?\d[\d,]*\.?\d*)", re.IGNORECASE)


@register("final_answer")
def final_answer(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    expected = spec.get("expected", case.expected)
    matches = _ANSWER.findall(text)
    if not matches:
        return EvalOutcome(
            "final_answer",
            "final_answer",
            False,
            0.0,
            details={"error": "no 'ANSWER: <number>' line found", "expected": expected},
            metrics={"format_ok": 0.0},
        )
    got = to_number(matches[-1])
    ok = got is not None and abs(got - float(expected)) <= float(spec.get("tolerance", 0.0)) + 1e-9
    return EvalOutcome(
        "final_answer",
        "final_answer",
        ok,
        1.0 if ok else 0.0,
        details={"expected": expected, "actual": got},
        metrics={"format_ok": 1.0},
    )


_CITATION = re.compile(r"\[([a-z0-9][a-z0-9_\-]*)\]", re.IGNORECASE)


def cited_ids(text: str) -> list[str]:
    return [c.lower() for c in _CITATION.findall(text)]


@register("citation")
def citation(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    expected = {e.lower() for e in spec["expected"]}
    cited = cited_ids(text)
    known = set((case.context.get("documents") or {}).keys())
    valid = [c for c in cited if not known or c in known]
    correct = expected & set(valid)
    precision = len(correct) / len(set(valid)) if valid else 0.0
    ok = bool(correct) and precision >= 0.5
    return EvalOutcome(
        "citation",
        "citation",
        ok,
        round(precision if correct else 0.0, 4),
        details={"cited": cited, "expected": sorted(expected)},
        metrics={"citation_correct": float(ok)},
    )


_ABSTAIN_PATTERNS = [
    r"not_found",
    r"not (?:found|contained|mentioned|covered|specified) in the (?:provided )?documents",
    r"documents do not (?:contain|mention|specify|say)",
    r"i (?:could not|couldn't|cannot) find",
]


@register("abstention")
def abstention(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    lowered = text.casefold()
    token = spec.get("token", "NOT_FOUND").casefold()
    abstained = token in lowered or any(re.search(p, lowered) for p in _ABSTAIN_PATTERNS)
    return EvalOutcome(
        "abstention",
        "abstention",
        abstained,
        1.0 if abstained else 0.0,
        details={} if abstained else {"answer": text.strip()[:300]},
        metrics={"hallucinated": float(not abstained)},
    )


@register("faithfulness")
def faithfulness(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
    """Every number stated in the answer must appear in a cited document (or the question)."""
    docs: dict[str, str] = case.context.get("documents") or {}
    question = case.context.get("question", "")
    cited = [c for c in cited_ids(text) if c in docs]
    sources = " ".join(docs[c] for c in cited) if cited else " ".join(docs.values())
    source_nums = {n.replace(",", "").rstrip(".") for n in _NUM.findall(sources + " " + question)}
    answer = _CITATION.sub(" ", text)
    claimed = [n.replace(",", "").rstrip(".") for n in _NUM.findall(answer)]
    claimed = [c for c in claimed if c and c not in {"-", "+"}]
    unsupported = [c for c in claimed if c not in source_nums]
    score = 1.0 if not claimed else (len(claimed) - len(unsupported)) / len(claimed)
    return EvalOutcome(
        "faithfulness",
        "faithfulness",
        not unsupported,
        round(score, 4),
        details={"unsupported_numbers": unsupported, "cited": cited},
        metrics={"faithful": float(not unsupported)},
    )
