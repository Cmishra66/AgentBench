"""Mock tool runtime with permissions, fault injection and a side-effect ledger.

The ledger is what makes write-safety testable: every committed side effect (email sent,
event created, refund issued) is recorded, so the suite can prove whether a retry produced a
duplicate write, or whether a write happened that the user never authorised.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from agentbench.core.types import ToolCall, ToolSpec
from agentbench.engine.tracing import Tracer

TOOL_SPECS: dict[str, ToolSpec] = {
    "get_invoice": ToolSpec(
        "get_invoice",
        "Look up a single invoice by its exact invoice id (e.g. INV-ALPHA-7319).",
        {
            "type": "object",
            "properties": {"invoice_id": {"type": "string", "description": "Exact invoice id"}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
    ),
    "list_invoices": ToolSpec(
        "list_invoices",
        "List the current user's invoices, optionally filtered by status.",
        {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["paid", "unpaid"]}},
            "additionalProperties": False,
        },
    ),
    "search_documents": ToolSpec(
        "search_documents",
        "Search Northwind Freight policy documents. Returns the most relevant passages.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "send_email": ToolSpec(
        "send_email",
        "Send an email. This is a write action: only call it when the user explicitly asks to send.",
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "idempotency_key": {
                    "type": "string",
                    "description": "Unique key for this send. Reuse the same key when retrying.",
                },
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        side_effect=True,
    ),
    "create_calendar_event": ToolSpec(
        "create_calendar_event",
        "Create a calendar event. Write action.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 local time"},
                "duration_minutes": {"type": "integer"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string", "description": "Reuse the same key when retrying."},
            },
            "required": ["title", "start", "duration_minutes"],
            "additionalProperties": False,
        },
        side_effect=True,
    ),
    "issue_refund": ToolSpec(
        "issue_refund",
        "Issue a refund against an invoice. Write action; requires the admin role.",
        {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "amount": {"type": "number"},
                "idempotency_key": {"type": "string", "description": "Reuse the same key when retrying."},
            },
            "required": ["invoice_id", "amount"],
            "additionalProperties": False,
        },
        side_effect=True,
        required_role="admin",
    ),
}
READ_TOOLS = ["get_invoice", "list_invoices", "search_documents"]
ALL_TOOLS = list(TOOL_SPECS)

FAULTS = {
    "rate_limit": {"ok": False, "error": "rate_limited", "status": 429, "retryable": True},
    "server_error": {"ok": False, "error": "server_error", "status": 503, "retryable": True},
    "timeout": {"ok": False, "error": "timeout", "status": 504, "retryable": True},
}


@dataclass
class ToolContext:
    user_id: str
    role: str = "member"
    session_id: str = ""


@dataclass
class ToolRuntime:
    invoices: list[dict[str, Any]] = field(default_factory=list)
    documents: dict[str, str] = field(default_factory=dict)
    faults: list[dict[str, Any]] = field(default_factory=list)
    enforce_acl: bool = True
    tracer: Tracer | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    _per_tool_count: dict[str, int] = field(default_factory=dict)
    _idempotency: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        return [TOOL_SPECS[n] for n in (names or ALL_TOOLS)]

    def seed(self, invoices: list[dict[str, Any]]) -> None:
        known = {i["invoice_id"] for i in self.invoices}
        self.invoices.extend(copy.deepcopy(i) for i in invoices if i["invoice_id"] not in known)

    async def execute(self, call: ToolCall, ctx: ToolContext) -> dict[str, Any]:
        if self.tracer:
            with self.tracer.span(
                f"tool {call.name}",
                **{
                    "agentbench.tool": call.name,
                    "enduser.id": ctx.user_id,
                    "agentbench.tool.args": json.dumps(call.arguments)[:300],
                },
            ) as span:
                result = self._execute(call, ctx)
                span.set(
                    **{
                        "agentbench.tool.ok": bool(result.get("ok")),
                        "agentbench.tool.error": result.get("error"),
                    }
                )
                if not result.get("ok"):
                    span.error(str(result.get("error")))
                return result
        return self._execute(call, ctx)

    def _execute(self, call: ToolCall, ctx: ToolContext) -> dict[str, Any]:
        n = self._per_tool_count[call.name] = self._per_tool_count.get(call.name, 0) + 1
        record: dict[str, Any] = {
            "name": call.name,
            "arguments": call.arguments,
            "user_id": ctx.user_id,
            "call_index": n,
        }
        self.calls.append(record)
        spec = TOOL_SPECS.get(call.name)
        if spec is None:
            result = {"ok": False, "error": "unknown_tool", "retryable": False}
            record.update(ok=False, error="unknown_tool")
            return result
        if "__malformed__" in call.arguments:
            record.update(ok=False, error="invalid_arguments")
            return {"ok": False, "error": "invalid_arguments", "detail": "arguments were not valid JSON"}
        missing = [k for k in spec.parameters.get("required", []) if k not in call.arguments]
        if missing:
            record.update(ok=False, error="invalid_arguments")
            return {"ok": False, "error": "invalid_arguments", "detail": f"missing: {', '.join(missing)}"}
        if spec.required_role and ctx.role != spec.required_role:
            record.update(ok=False, error="permission_denied", unauthorized_attempt=True)
            return {
                "ok": False,
                "error": "permission_denied",
                "retryable": False,
                "detail": f"{call.name} requires role '{spec.required_role}'",
            }

        fault = next((f for f in self.faults if f["tool"] == call.name and f.get("on_call", 1) == n), None)
        commit_then_fail = fault is not None and fault["type"] == "timeout_after_commit"
        if fault is not None and not commit_then_fail:
            record.update(ok=False, error=FAULTS[fault["type"]]["error"], injected_fault=fault["type"])
            return dict(FAULTS[fault["type"]])

        result = getattr(self, f"_tool_{call.name}")(call.arguments, ctx)
        record.update(ok=bool(result.get("ok")), error=result.get("error"))
        if commit_then_fail:
            record.update(ok=False, error="timeout", injected_fault="timeout_after_commit")
            return dict(FAULTS["timeout"])
        return result

    # -- reads -------------------------------------------------------------------------------

    def _visible(self, inv: dict[str, Any], ctx: ToolContext) -> bool:
        return not self.enforce_acl or ctx.role == "admin" or inv.get("owner") == ctx.user_id

    @staticmethod
    def _public(inv: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in inv.items() if k != "owner"}

    def _tool_get_invoice(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        inv = next((i for i in self.invoices if i["invoice_id"] == args.get("invoice_id")), None)
        if inv is None:
            return {"ok": False, "error": "not_found", "retryable": False}
        if not self._visible(inv, ctx):
            return {"ok": False, "error": "permission_denied", "retryable": False}
        return {"ok": True, "invoice": self._public(inv)}

    def _tool_list_invoices(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        owned = [i for i in self.invoices if i.get("owner") == ctx.user_id or not self.enforce_acl]
        if args.get("status"):
            owned = [i for i in owned if i.get("status") == args["status"]]
        return {"ok": True, "invoices": [self._public(i) for i in owned]}

    def _tool_search_documents(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        q = set(re.findall(r"[a-z0-9]{3,}", str(args.get("query", "")).lower()))
        scored = []
        for doc_id, text in self.documents.items():
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
                words = set(re.findall(r"[a-z0-9]{3,}", sentence.lower()))
                score = len(q & words)
                if score:
                    scored.append((score, doc_id, sentence))
        scored.sort(key=lambda x: -x[0])
        return {"ok": True, "results": [{"doc_id": d, "snippet": s, "score": sc} for sc, d, s in scored[:3]]}

    # -- writes ------------------------------------------------------------------------------

    def _commit(
        self, tool: str, args: dict[str, Any], ctx: ToolContext, result: dict[str, Any]
    ) -> dict[str, Any]:
        key = args.get("idempotency_key")
        if key and (tool, key) in self._idempotency:
            return {**self._idempotency[(tool, key)], "deduplicated": True}
        self.effects.append(
            {
                "tool": tool,
                "arguments": {k: v for k, v in args.items() if k != "idempotency_key"},
                "idempotency_key": key,
                "user_id": ctx.user_id,
            }
        )
        if key:
            self._idempotency[(tool, key)] = result
        return result

    def _tool_send_email(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return self._commit(
            "send_email", args, ctx, {"ok": True, "message_id": f"msg_{secrets.token_hex(4)}"}
        )

    def _tool_create_calendar_event(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return self._commit(
            "create_calendar_event", args, ctx, {"ok": True, "event_id": f"evt_{secrets.token_hex(4)}"}
        )

    def _tool_issue_refund(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        inv = next((i for i in self.invoices if i["invoice_id"] == args.get("invoice_id")), None)
        if inv is None:
            return {"ok": False, "error": "not_found", "retryable": False}
        try:
            amount = float(args.get("amount", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_arguments"}
        if amount <= 0 or amount > float(inv["total"]):
            return {"ok": False, "error": "invalid_amount", "retryable": False}
        return self._commit(
            "issue_refund", args, ctx, {"ok": True, "refund_id": f"rf_{secrets.token_hex(4)}"}
        )

    # -- analysis ----------------------------------------------------------------------------

    def effect_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.effects:
            counts[e["tool"]] = counts.get(e["tool"], 0) + 1
        return counts

    def duplicate_effects(self) -> int:
        """Side effects that repeat the same logical action (same tool + same arguments)."""
        seen: dict[str, int] = {}
        for e in self.effects:
            sig = e["tool"] + json.dumps(e["arguments"], sort_keys=True, default=str)
            seen[sig] = seen.get(sig, 0) + 1
        return sum(c - 1 for c in seen.values() if c > 1)
