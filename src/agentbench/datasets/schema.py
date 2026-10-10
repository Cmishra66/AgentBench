"""Test-case schema shared by every dataset file."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str | None = None
    prompt: str
    response_format: Literal["text", "json"] = "text"


class TestCase(BaseModel):
    """One labelled, reproducible test case.

    ``evaluators`` is an ordered list of oracle configs, e.g.
    ``{"type": "fields", "expected": {...}, "required": true}``. A case passes when every
    evaluator with ``required: true`` (the default) passes.
    """

    __test__ = False  # stop pytest from collecting this class
    model_config = ConfigDict(extra="forbid")

    id: str
    suite: str
    category: str
    input: CaseInput
    expected: Any = None
    evaluators: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
