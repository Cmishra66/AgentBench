"""Lightweight OpenTelemetry-compatible tracing.

Spans are recorded in-process (no SDK required) using OTel field names and GenAI semantic
convention attributes (``gen_ai.*``), stored with each case result, and can be exported as
OTLP/JSON (``agentbench traces export``) to Jaeger, Tempo, Honeycomb or any OTLP collector.
"""

from __future__ import annotations

import contextvars
import secrets
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("agentbench_span", default=None)


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_ms: float
    kind: str = "INTERNAL"
    end_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "UNSET"
    status_message: str = ""

    def set(self, **attrs: Any) -> None:
        self.attributes.update({k: v for k, v in attrs.items() if v is not None})

    def event(self, name: str, **attrs: Any) -> None:
        self.events.append({"name": name, "time_ms": time.time() * 1000, "attributes": attrs})

    def error(self, message: str) -> None:
        self.status, self.status_message = "ERROR", message[:500]

    def to_dict(self) -> dict[str, Any]:
        end = self.end_ms or time.time() * 1000
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "start_ms": round(self.start_ms, 3),
            "end_ms": round(end, 3),
            "duration_ms": round(end - self.start_ms, 3),
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status if self.status != "UNSET" else "OK",
            "status_message": self.status_message,
        }


class Tracer:
    def __init__(self) -> None:
        self._spans: dict[str, list[Span]] = defaultdict(list)

    @contextmanager
    def span(self, name: str, kind: str = "INTERNAL", new_trace: bool = False, **attributes: Any):
        parent = None if new_trace else _current_span.get()
        span = Span(
            name=name,
            trace_id=parent.trace_id if parent else secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            parent_span_id=parent.span_id if parent else None,
            start_ms=time.time() * 1000,
            kind=kind,
        )
        span.set(**attributes)
        self._spans[span.trace_id].append(span)
        token = _current_span.set(span)
        try:
            yield span
        except BaseException as exc:
            span.error(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            span.end_ms = time.time() * 1000
            _current_span.reset(token)

    def collect(self, trace_id: str) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._spans.pop(trace_id, [])]


def current_span() -> Span | None:
    return _current_span.get()


def to_otlp_json(
    spans: list[dict[str, Any]],
    service_name: str = "agentbench",
    resource_attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert stored spans into an OTLP/JSON ``ExportTraceServiceRequest`` body."""

    def attr(k: str, v: Any) -> dict[str, Any]:
        if isinstance(v, bool):
            return {"key": k, "value": {"boolValue": v}}
        if isinstance(v, int):
            return {"key": k, "value": {"intValue": str(v)}}
        if isinstance(v, float):
            return {"key": k, "value": {"doubleValue": v}}
        return {"key": k, "value": {"stringValue": str(v)}}

    kind_map = {"INTERNAL": 1, "SERVER": 2, "CLIENT": 3}
    otlp_spans = []
    for s in spans:
        otlp_spans.append(
            {
                "traceId": s["trace_id"],
                "spanId": s["span_id"],
                "parentSpanId": s.get("parent_span_id") or "",
                "name": s["name"],
                "kind": kind_map.get(s.get("kind", "INTERNAL"), 1),
                "startTimeUnixNano": str(int(s["start_ms"] * 1_000_000)),
                "endTimeUnixNano": str(int(s["end_ms"] * 1_000_000)),
                "attributes": [attr(k, v) for k, v in s.get("attributes", {}).items()],
                "events": [
                    {
                        "name": e["name"],
                        "timeUnixNano": str(int(e["time_ms"] * 1_000_000)),
                        "attributes": [attr(k, v) for k, v in e.get("attributes", {}).items()],
                    }
                    for e in s.get("events", [])
                ],
                "status": {
                    "code": 2 if s.get("status") == "ERROR" else 1,
                    "message": s.get("status_message", ""),
                },
            }
        )
    resource = [attr("service.name", service_name)] + [attr(k, v) for k, v in (resource_attrs or {}).items()]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource},
                "scopeSpans": [{"scope": {"name": "agentbench"}, "spans": otlp_spans}],
            }
        ]
    }
