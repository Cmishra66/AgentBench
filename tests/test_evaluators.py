import json

import pytest

from agentbench.datasets.schema import CaseInput, TestCase
from agentbench.evaluators import case_passed, run_evaluators
from agentbench.evaluators.deterministic import parse_json_lenient, parse_json_strict

DOCS = {
    "refund-policy": "Customers may request a refund within 30 days of delivery. Refunds above 5,000 USD need approval.",
    "pto-policy": "Parental leave is 16 weeks at full pay.",
}


def case(evaluators, expected=None, context=None) -> TestCase:
    return TestCase(
        id="t-1",
        suite="core",
        category="unit",
        input=CaseInput(prompt="p"),
        expected=expected,
        evaluators=evaluators,
        context=context or {},
    )


async def outcomes(c: TestCase, text: str):
    return {o.name: o for o in await run_evaluators(c, text)}


def test_json_parsers():
    assert parse_json_strict('{"a": 1}')[0] == {"a": 1}
    assert parse_json_strict('```json\n{"a": 1}\n```')[1] is not None
    assert parse_json_lenient('```json\n{"a": 1}\n```')[0] == {"a": 1}
    assert parse_json_lenient('Here you go: {"a": {"b": "}"}} thanks')[0] == {"a": {"b": "}"}}
    assert parse_json_lenient("no json")[0] is None


async def test_schema_strict_vs_lenient():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
    c = case(
        [
            {"type": "json_schema", "schema": schema, "strict": True, "name": "strict"},
            {"type": "json_schema", "schema": schema, "strict": False, "name": "lenient"},
        ]
    )
    o = await outcomes(c, '```json\n{"a": 1}\n```')
    assert not o["strict"].passed and o["lenient"].passed
    o = await outcomes(c, '{"a": "x"}')
    assert not o["strict"].passed and "a: 'x' is not of type 'integer'" in o["strict"].details["errors"][0]


async def test_fields_precision_recall_and_normalisation():
    expected = {"invoice_number": "INV-1", "vendor": "Bluefin Packaging Co.", "total": 1234.5, "tax": None}
    c = case([{"type": "fields", "expected": expected, "numeric_tolerance": 0.01}])
    o = (
        await outcomes(
            c,
            json.dumps(
                {
                    "invoice_number": "INV-1",
                    "vendor": "BLUEFIN PACKAGING CO",
                    "total": "1,234.50",
                    "tax": None,
                }
            ),
        )
    )["fields"]
    assert o.passed and o.score == 1.0
    o = (
        await outcomes(
            c,
            json.dumps(
                {"invoice_number": "INV-2", "vendor": "Bluefin Packaging Co.", "total": 1234.5, "tax": 12.0}
            ),
        )
    )["fields"]
    assert not o.passed
    assert o.metrics["field_tp"] == 2 and o.metrics["field_fp"] == 2 and o.metrics["field_fn"] == 1
    assert set(o.details["mismatches"]) == {"invoice_number", "tax"}


async def test_exact_and_contains():
    c = case([{"type": "exact", "expected": "billing", "normalize": ["strip", "lower", "strip_punct"]}])
    assert (await outcomes(c, "  Billing.\n"))["exact"].passed
    assert not (await outcomes(c, "Category: billing"))["exact"].passed
    c = case([{"type": "contains_all", "groups": [["30 days", "thirty days"], ["refund"]]}])
    assert (await outcomes(c, "You can get a REFUND within thirty days."))["contains_all"].passed
    o = (await outcomes(c, "Within 30 days."))["contains_all"]
    assert not o.passed and o.score == 0.5


async def test_final_answer_takes_the_last_answer_line():
    c = case([{"type": "final_answer", "expected": 1284.5, "tolerance": 0.01}])
    assert (await outcomes(c, "ANSWER: 12\nWait, recompute.\nANSWER: 1,284.50"))["final_answer"].passed
    o = (await outcomes(c, "The answer is 1284.5"))["final_answer"]
    assert not o.passed and o.metrics["format_ok"] == 0.0


async def test_grounding_evaluators():
    ctx = {"documents": DOCS, "question": "How long?"}
    c = case([{"type": "citation", "expected": ["refund-policy"]}, {"type": "faithfulness"}], context=ctx)
    o = await outcomes(c, "Within 30 days of delivery [refund-policy].")
    assert o["citation"].passed and o["faithfulness"].passed
    o = await outcomes(c, "Within 45 days of delivery [pto-policy].")
    assert not o["citation"].passed
    assert not o["faithfulness"].passed and o["faithfulness"].details["unsupported_numbers"] == ["45"]


async def test_abstention_detects_hallucination():
    c = case([{"type": "abstention", "token": "NOT_FOUND"}])
    assert (await outcomes(c, "NOT_FOUND"))["abstention"].passed
    assert (await outcomes(c, "The documents do not mention that."))["abstention"].passed
    o = (await outcomes(c, "It is 40 USD per day."))["abstention"]
    assert not o.passed and o.metrics["hallucinated"] == 1.0


async def test_not_contains_and_optional_evaluators_do_not_gate():
    c = case(
        [
            {"type": "not_contains", "values": ["NWREF-1234"], "metric": "secret_leaked"},
            {"type": "contains_any", "values": ["order"], "required": False},
            {"type": "llm_judge", "required": False},
        ]
    )
    outs = await run_evaluators(c, "Summary of the order.")
    passed, score = case_passed(outs)
    assert passed and score == 1.0
    assert next(o for o in outs if o.type == "llm_judge").skipped
    outs = await run_evaluators(c, "code nwref-1234")
    assert not case_passed(outs)[0]


async def test_unknown_evaluator_is_an_error():
    with pytest.raises(KeyError):
        await run_evaluators(case([{"type": "does_not_exist"}]), "x")
