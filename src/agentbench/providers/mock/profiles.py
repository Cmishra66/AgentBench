"""Behaviour profiles for the mock models."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any


@dataclass(frozen=True)
class MockProfile:
    name: str
    version: str = "1.0"
    quality: float = 0.95  # P(an answer is NOT corrupted)
    latency_ms: float = 400.0  # median latency
    latency_sigma: float = 0.35  # log-normal spread (tail heaviness)
    ttft_fraction: float = 0.25  # time-to-first-token as a share of latency
    capacity: int = 24  # concurrent requests before queueing kicks in
    rate_limit_p: float = 0.0  # P(HTTP 429) per attempt when not overloaded
    server_error_p: float = 0.0  # P(HTTP 503) per attempt
    hang_p: float = 0.0  # P(request hangs for hang_s seconds)
    hang_s: float = 4.0
    injection_susceptibility: float = 0.05
    hallucination_p: float = 0.1  # P(answering an unanswerable question anyway)
    tool_error_p: float = 0.04  # P(wrong tool / bad arguments / unauthorised write)
    idempotency_aware: bool = True  # reuses idempotency keys on retried writes
    isolation_discipline: float = 0.9  # P(refusing to discuss other customers)
    format_slip_p: float = 0.03  # P(wrapping JSON in prose or markdown fences)
    price_in: float = 0.0  # informational; real pricing comes from target config
    price_out: float = 0.0

    def with_overrides(self, overrides: dict[str, Any]) -> MockProfile:
        names = {f.name for f in fields(self)}
        valid = {k: v for k, v in overrides.items() if k in names}
        return replace(self, **valid) if valid else self


PROFILES: dict[str, MockProfile] = {
    p.name: p
    for p in [
        MockProfile(
            "mock-strong",
            quality=0.95,
            latency_ms=420,
            latency_sigma=0.35,
            capacity=24,
            injection_susceptibility=0.05,
            hallucination_p=0.12,
            tool_error_p=0.04,
            isolation_discipline=0.9,
            format_slip_p=0.03,
        ),
        MockProfile(
            "mock-fast",
            quality=0.84,
            latency_ms=140,
            latency_sigma=0.3,
            ttft_fraction=0.2,
            capacity=48,
            injection_susceptibility=0.35,
            hallucination_p=0.5,
            tool_error_p=0.15,
            idempotency_aware=False,
            isolation_discipline=0.4,
            format_slip_p=0.18,
        ),
        MockProfile(
            "mock-flaky",
            quality=0.9,
            latency_ms=380,
            latency_sigma=0.9,
            capacity=6,
            rate_limit_p=0.06,
            server_error_p=0.03,
            hang_p=0.02,
            hang_s=4.0,
            injection_susceptibility=0.15,
            hallucination_p=0.25,
            tool_error_p=0.07,
            isolation_discipline=0.7,
            format_slip_p=0.06,
        ),
        # A "new release" of mock-strong with deliberate regressions, used to demo the CI gate.
        MockProfile(
            "mock-strong-v2",
            version="2.0",
            quality=0.9,
            latency_ms=560,
            latency_sigma=0.45,
            capacity=20,
            injection_susceptibility=0.2,
            hallucination_p=0.3,
            tool_error_p=0.08,
            isolation_discipline=0.85,
            format_slip_p=0.06,
        ),
        MockProfile("mock-judge", quality=0.93, latency_ms=200, latency_sigma=0.25, capacity=64),
        # Deterministic, instant and always correct: used by the framework's own unit tests.
        MockProfile(
            "mock-perfect",
            quality=1.0,
            latency_ms=0.0,
            latency_sigma=0.0,
            capacity=10_000,
            injection_susceptibility=0.0,
            hallucination_p=0.0,
            tool_error_p=0.0,
            isolation_discipline=1.0,
            format_slip_p=0.0,
        ),
    ]
}
