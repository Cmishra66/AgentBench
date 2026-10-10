"""Optional LLM-as-judge evaluator.

The judge is deliberately a *separate* target from the systems under test, its verdicts never
gate a case on their own (``required: false`` in the shipped datasets), and its decisions can be
exported for periodic human validation (``agentbench judge export`` / ``agentbench judge agreement``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agentbench.core.types import Message, ModelRequest, ModelResponse
from agentbench.datasets.schema import TestCase
from agentbench.evaluators.base import EvalOutcome, JudgeFn
from agentbench.evaluators.deterministic import parse_json_lenient

JUDGE_SYSTEM = (
    "You are an impartial evaluator of AI assistant answers. Grade the candidate answer against the "
    "reference answer and the rubric. Be strict about factual correctness and ignore style."
)


def build_judge_prompt(question: str, reference: str, answer: str, rubric: str) -> str:
    return (
        f"Question:\n{question}\n\nReference answer:\n{reference}\n\nCandidate answer:\n{answer}\n\n"
        f"Rubric: {rubric}\n\nRespond with only a JSON object: "
        '{"score": <integer 1-5>, "verdict": "pass" or "fail", "reason": "<one sentence>"}'
    )


def make_judge(call: Callable[[ModelRequest], Awaitable[ModelResponse]], judge_id: str) -> JudgeFn:
    async def judge(spec: dict[str, Any], text: str, case: TestCase) -> EvalOutcome:
        question = case.context.get("question") or case.input.prompt[-500:]
        prompt = build_judge_prompt(
            question,
            str(spec.get("reference", case.expected)),
            text,
            spec.get("rubric", "Is the answer correct and complete?"),
        )
        req = ModelRequest(
            messages=[Message("user", prompt)],
            system=JUDGE_SYSTEM,
            temperature=0.0,
            max_output_tokens=200,
            metadata={"case_id": case.id, "judge": True},
        )
        try:
            resp = await call(req)
        except Exception as exc:  # judge failures never fail the case under test
            return EvalOutcome(
                "llm_judge",
                "llm_judge",
                True,
                0.0,
                skipped=True,
                details={"judge": judge_id, "error": str(exc)},
            )
        obj, err = parse_json_lenient(resp.text)
        if not isinstance(obj, dict):
            return EvalOutcome(
                "llm_judge",
                "llm_judge",
                True,
                0.0,
                skipped=True,
                details={"judge": judge_id, "error": err, "raw": resp.text[:300]},
            )
        try:
            score = max(1, min(5, int(obj.get("score", 1))))
        except (TypeError, ValueError):
            score = 1
        verdict = str(obj.get("verdict", "fail")).lower() == "pass"
        return EvalOutcome(
            "llm_judge",
            "llm_judge",
            verdict,
            round((score - 1) / 4, 4),
            details={
                "judge": judge_id,
                "judge_model": resp.model,
                "score": score,
                "reason": obj.get("reason", ""),
                "verdict": "pass" if verdict else "fail",
            },
            metrics={"judge_score": float(score), "judge_pass": float(verdict)},
        )

    return judge
