"""Provider-neutral request/response types shared by every adapter, suite and evaluator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: bool = False
    required_role: str | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ModelRequest:
    messages: list[Message]
    system: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int | None = None
    response_format: Literal["text", "json"] = "text"
    # Bookkeeping only (case id, trial, seed, step). Real providers never send it; the
    # mock provider uses it to make simulated behaviour reproducible.
    metadata: dict[str, Any] = field(default_factory=dict)

    def prompt_text(self) -> str:
        parts = [self.system or ""]
        parts.extend(m.content for m in self.messages)
        return "\n".join(p for p in parts if p)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str | None = None
    ttft_ms: float | None = None
    latency_ms: float = 0.0
    cached: bool = False
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


ErrorKind = Literal["rate_limit", "timeout", "server", "client", "auth", "budget", "network", "unknown"]


class ProviderError(Exception):
    """Normalised provider failure. `retryable` drives the runner's retry policy."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind = "unknown",
        status: int | None = None,
        retryable: bool = False,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable
        self.retry_after_s = retry_after_s

    @classmethod
    def from_status(cls, status: int, body: str = "", retry_after: str | None = None) -> ProviderError:
        retry_after_s = None
        if retry_after:
            try:
                retry_after_s = float(retry_after)
            except ValueError:
                retry_after_s = None
        snippet = body[:300]
        if status == 429:
            return cls(
                f"rate limited: {snippet}",
                kind="rate_limit",
                status=status,
                retryable=True,
                retry_after_s=retry_after_s,
            )
        if status in (401, 403):
            return cls(f"auth error {status}: {snippet}", kind="auth", status=status)
        if status in (408, 504):
            return cls(f"gateway timeout {status}", kind="timeout", status=status, retryable=True)
        if status >= 500 or status == 529:
            return cls(f"server error {status}: {snippet}", kind="server", status=status, retryable=True)
        return cls(f"client error {status}: {snippet}", kind="client", status=status)


class BudgetExceeded(Exception):
    pass


@dataclass
class AgentReply:
    """Response from an agent target (a stateful system with users, sessions and tools)."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: float = 0.0
    ttft_ms: float | None = None
