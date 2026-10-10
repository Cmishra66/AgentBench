"""Mock behaviour for agentic prompts: tool planning, memory-backed chat and LLM-judge grading."""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from agentbench.core.types import Message, ModelRequest, ToolCall
from agentbench.providers.mock.profiles import MockProfile
from agentbench.providers.mock.solvers import _content_words, best_sentence

INVOICE_RE = re.compile(r"\bINV-[A-Z0-9]+-\d+\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
FACT_RE = re.compile(
    r"INV-[A-Z0-9]+-\d+|\d{1,3}(?:,\d{3})*\.\d{2}|[\w.+-]+@[\w-]+\.[\w.]+|sk-[\w-]+|\b[A-Z0-9]{4}-[A-Z0-9]{4,}\b",
    re.IGNORECASE,
)
WRITE_TOOLS = {"send_email", "create_calendar_event", "issue_refund"}
RETRYABLE = {"rate_limited", "server_error", "timeout"}


def _key(task: str, name: str) -> str:
    return f"{name}-{hashlib.sha256(task.encode()).hexdigest()[:10]}"


def _call_id(name: str, n: int) -> str:
    return f"call_{name}_{n}"


def _signature(name: str, args: dict[str, Any]) -> str:
    clean = {k: v for k, v in args.items() if k != "idempotency_key"}
    return name + json.dumps(clean, sort_keys=True)


def _transcript(req: ModelRequest) -> tuple[str, list[tuple[ToolCall | None, dict[str, Any]]], int]:
    msgs: list[Message] = req.messages
    user_idx = max((i for i, m in enumerate(msgs) if m.role == "user"), default=0)
    task = msgs[user_idx].content if msgs else ""
    pending: dict[str, ToolCall] = {}
    results: list[tuple[ToolCall | None, dict[str, Any]]] = []
    n_calls = 0
    for m in msgs[user_idx + 1 :]:
        if m.role == "assistant":
            for tc in m.tool_calls:
                pending[tc.id] = tc
                n_calls += 1
        elif m.role == "tool":
            try:
                payload = json.loads(m.content)
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "unparseable"}
            results.append((pending.get(m.tool_call_id or ""), payload))
    return task, results, n_calls


def _money(x: Any) -> str:
    try:
        return f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def compose_from_results(task: str, results: list[tuple[ToolCall | None, dict[str, Any]]]) -> str:
    parts: list[str] = []
    for call, res in results:
        if not res.get("ok") or call is None:
            continue
        if call.name == "get_invoice":
            inv = res["invoice"]
            parts.append(
                f"Invoice {inv['invoice_id']} from {inv.get('vendor', 'the vendor')} totals "
                f"{inv.get('currency', 'USD')} {_money(inv['total'])} and is {inv.get('status', 'open')}"
                f" (billing contact {inv.get('billing_email', 'n/a')})."
            )
        elif call.name == "list_invoices":
            invs = res.get("invoices", [])
            if "unpaid" in task.lower():
                invs = [i for i in invs if i.get("status") == "unpaid"]
            listed = ", ".join(
                f"{i['invoice_id']} ({i.get('currency', 'USD')} {_money(i['total'])}, {i.get('status')})"
                for i in invs
            )
            parts.append(f"Your invoices: {listed}." if listed else "You have no matching invoices.")
        elif call.name == "search_documents":
            snippets: dict[str, list[str]] = {}
            for r in res.get("results", []):
                snippets.setdefault(r["doc_id"], []).append(r["snippet"])
            question = re.sub(r"according to [^,?]*,\s*", "", task, flags=re.IGNORECASE)
            doc_id, sentence, coverage, novel = best_sentence(snippets, question)
            if sentence and coverage >= 0.5 and not novel:
                parts.append(f"{sentence} [{doc_id}]")
            else:
                parts.append("I couldn't find that in the policy documents (NOT_FOUND).")
        elif call.name == "send_email":
            parts.append(f"Email sent to {call.arguments.get('to')}.")
        elif call.name == "create_calendar_event":
            parts.append(f"Scheduled '{call.arguments.get('title')}' for {call.arguments.get('start')}.")
        elif call.name == "issue_refund":
            parts.append(
                f"Refund of {_money(call.arguments.get('amount'))} issued for "
                f"{call.arguments.get('invoice_id')}."
            )
    return " ".join(parts) if parts else "Done."


def _email_body(task: str, results: list[tuple[ToolCall | None, dict[str, Any]]]) -> str:
    summary = compose_from_results(task, [(c, r) for c, r in results if c and c.name != "send_email"])
    return summary if summary != "Done." else task


def plan_tool_use(
    req: ModelRequest, step_rng: random.Random, case_rng: random.Random, p: MockProfile, degrade: float
) -> tuple[str, list[ToolCall]]:
    tools = {t.name for t in req.tools}
    task, results, n_calls = _transcript(req)
    lower = task.lower()
    sloppy = case_rng.random() < p.tool_error_p + degrade
    sloppy_kind = case_rng.choice(["wrong_tool", "bad_args", "unauthorized", "extra_call"])
    system = (req.system or "").lower()

    def call(name: str, args: dict[str, Any]) -> tuple[str, list[ToolCall]]:
        return "", [ToolCall(id=_call_id(name, n_calls + 1), name=name, arguments=args)]

    # 1. React to the most recent tool result.
    if results:
        last_call, last = results[-1]
        if not last.get("ok"):
            err = last.get("error", "")
            if err in RETRYABLE and last_call is not None:
                sig = _signature(last_call.name, last_call.arguments)
                attempts = sum(
                    1 for c, _ in results if c is not None and _signature(c.name, c.arguments) == sig
                )
                if attempts < 3:
                    args = dict(last_call.arguments)
                    if not p.idempotency_aware:
                        args.pop("idempotency_key", None)
                    return call(last_call.name, args)
                return (
                    f"Sorry, I couldn't complete the request because the {last_call.name} tool kept failing.",
                    [],
                )
            if err == "permission_denied":
                return "Sorry, you don't have permission for that action, so I didn't perform it.", []
            if err == "not_found":
                ref = last_call.arguments.get("invoice_id") if last_call else "that record"
                return f"I couldn't find {ref}.", []

    done = {_signature(c.name, c.arguments) for c, r in results if c is not None and r.get("ok")}
    done_names = {c.name for c, r in results if c is not None and r.get("ok")}

    ids = [i.upper() for i in INVOICE_RE.findall(task)]
    if sloppy and sloppy_kind == "bad_args":
        ids = [i.lower() for i in INVOICE_RE.findall(task)]
    emails = EMAIL_RE.findall(task)
    draft = "draft" in lower or "do not send" in lower or "don't send" in lower
    wants_refund = "refund" in lower and "policy" not in lower
    wants_email = "email" in lower and bool(emails)
    wants_schedule = "schedule" in lower
    wants_list = "unpaid invoices" in lower or "my invoices" in lower or "my latest invoice" in lower
    wants_policy = ("policy" in lower or "according to" in lower or "overtime" in lower) and not ids
    arithmetic = re.search(r"(\d+(?:\.\d+)?)% of (\d+(?:\.\d+)?)", task)
    role_blocks_refund = "cannot issue refunds" in system

    desired: list[tuple[str, dict[str, Any]]] = []
    if sloppy and sloppy_kind == "extra_call" and "list_invoices" in tools and (ids or wants_list):
        desired.append(("list_invoices", {}))
    if sloppy and sloppy_kind == "extra_call" and arithmetic and "search_documents" in tools:
        desired.append(("search_documents", {"query": task}))
    if wants_list and "list_invoices" in tools:
        desired.append(("list_invoices", {}))
    for inv_id in ids:
        if "get_invoice" in tools and not (wants_refund and role_blocks_refund):
            name = "list_invoices" if sloppy and sloppy_kind == "wrong_tool" else "get_invoice"
            desired.append((name, {"invoice_id": inv_id} if name == "get_invoice" else {}))
    if wants_policy and "search_documents" in tools:
        desired.append(("search_documents", {"query": task}))
    if wants_refund and ids and "issue_refund" in tools:
        if role_blocks_refund and not (sloppy and sloppy_kind == "unauthorized"):
            return "Sorry, your role doesn't allow issuing refunds. Please ask an administrator.", []
        amount = re.search(r"(\d+(?:\.\d+)?)\s*(?:USD|usd)|\$(\d+(?:\.\d+)?)", task)
        amt = float(amount.group(1) or amount.group(2)) if amount else 0.0
        args: dict[str, Any] = {"invoice_id": ids[0], "amount": amt}
        if p.idempotency_aware:
            args["idempotency_key"] = _key(task, "issue_refund")
        desired.append(("issue_refund", args))
    if wants_schedule and "create_calendar_event" in tools:
        title = re.search(r"titled '([^']+)'", task)
        start = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", task)
        dur = re.search(r"(\d+)-minute", task)
        args = {
            "title": title.group(1) if title else "Meeting",
            "start": start.group(0) if start else "",
            "duration_minutes": int(dur.group(1)) if dur else 30,
            "attendees": emails,
        }
        if p.idempotency_aware:
            args["idempotency_key"] = _key(task, "create_calendar_event")
        desired.append(("create_calendar_event", args))
    for name, args in desired:
        already = name in done_names if name in WRITE_TOOLS else _signature(name, args) in done
        if not already:
            return call(name, args)

    # Emails go last so the body can include what earlier tools returned.
    send_anyway = draft and sloppy and sloppy_kind == "unauthorized"
    if (
        wants_email
        and (not draft or send_anyway)
        and "send_email" in tools
        and "send_email" not in done_names
    ):
        args = {"to": emails[0], "subject": "Northwind Freight update", "body": _email_body(task, results)}
        if p.idempotency_aware:
            args["idempotency_key"] = _key(task, "send_email")
        return call("send_email", args)

    # 2. Compose the final answer.
    if draft and not send_anyway:
        to = emails[0] if emails else "the recipient"
        return (
            f"Here is a draft you can review (not sent):\nTo: {to}\nSubject: Late shipment update\n\n"
            "Hello, we wanted to let you know your shipment is running late and we are working on it."
        ), []
    if arithmetic and not results:
        value = float(arithmetic.group(1)) / 100 * float(arithmetic.group(2))
        return f"{arithmetic.group(1)}% of {arithmetic.group(2)} is {value:g}.", []
    if arithmetic:
        value = float(arithmetic.group(1)) / 100 * float(arithmetic.group(2))
        return f"{value:g}", []
    return compose_from_results(task, results), []


# --------------------------------------------------------------------------------------------------
# memory-backed support chat (used by the reference agent and the isolation suite)
# --------------------------------------------------------------------------------------------------

OTHER_REFS = [
    "other customer",
    "another customer",
    "last customer",
    "previous customer",
    "anyone else",
    "someone else",
    "on behalf of",
    "accountant",
    "colleague",
    "other user",
    "other users",
    "other people",
]
RECALL = [
    "remind me",
    "what did i",
    "did i give",
    "did i tell",
    "did i share",
    "did i mention",
    "my invoice",
    "my door",
    "door code",
    "my code",
    "about me",
    "what do you know",
    "summarize",
    "my api",
    "my latest",
    "my email",
    "what was my",
    "what's my",
    "what is my",
]
ENUMERATE = [
    "list every",
    "list all",
    "all invoices",
    "every invoice",
    "last customer",
    "previous customer",
    "anyone else",
    "any api keys",
    "api keys",
    "other customer",
    "what other",
    "everything you know",
]


def _memory_lines(system: str) -> list[str]:
    lines: list[str] = []
    capture = False
    for raw in system.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            capture = "memory" in line.lower() or "summary" in line.lower()
            continue
        if capture and line:
            if line.lower().startswith("user:"):
                lines.append(line[5:].strip())
            elif line.startswith("- "):
                lines.append(line[2:].strip())
    return lines


def support_chat(
    req: ModelRequest, step_rng: random.Random, case_rng: random.Random, p: MockProfile, degrade: float
) -> tuple[str, list[ToolCall]]:
    task, results, n_calls = _transcript(req)
    tools = {t.name for t in req.tools}
    if results:
        _, last = results[-1]
        if not last.get("ok"):
            err = last.get("error")
            if err == "permission_denied":
                return "I'm sorry, I can't access that invoice for your account.", []
            if err == "not_found":
                return "I couldn't find that invoice.", []
            return "Sorry, something went wrong looking that up.", []
        return compose_from_results(task, results), []

    lower = task.lower()
    asking = "?" in task or lower.startswith(
        (
            "what",
            "how",
            "show",
            "list",
            "tell",
            "read",
            "remind",
            "summar",
            "which",
            "who",
            "do you",
            "can you",
            "give",
        )
    )
    if not asking:
        return "Thanks, I've noted that.", []
    if any(k in lower for k in OTHER_REFS) and case_rng.random() < p.isolation_discipline:
        return "I'm sorry, I can't share information about other customers.", []
    memory = _memory_lines(req.system or "")
    with_facts = [m for m in memory if FACT_RE.search(m)]
    ids = [i.upper() for i in INVOICE_RE.findall(task)]
    if ids:
        hits = [m for m in with_facts if any(i.lower() in m.lower() for i in ids)]
        if hits:
            return "From our conversation: " + " ".join(hits), []
        if "get_invoice" in tools:
            return "", [
                ToolCall(id=_call_id("get_invoice", 1), name="get_invoice", arguments={"invoice_id": ids[0]})
            ]
        return "I don't have details for that invoice.", []
    if any(k in lower for k in ENUMERATE + RECALL):
        relevant = with_facts
        if any(k in lower for k in ["amount", "total", "invoice"]) and not any(k in lower for k in ENUMERATE):
            amounts = [m for m in with_facts if re.search(r"\d{1,3}(?:,\d{3})*\.\d{2}", m)]
            relevant = amounts[-1:] if amounts else []
        if not relevant and "list_invoices" in tools and "invoice" in lower:
            return "", [ToolCall(id=_call_id("list_invoices", 1), name="list_invoices", arguments={})]
        if not relevant:
            return "I don't have any saved details yet.", []
        return "Here's what I have on record: " + " ".join(relevant), []
    return "How can I help with your invoices today?", []


# --------------------------------------------------------------------------------------------------
# LLM judge (heuristic stand-in)
# --------------------------------------------------------------------------------------------------


def judge(req: ModelRequest, rng: random.Random, p: MockProfile) -> str:
    text = req.messages[-1].content if req.messages else ""
    ref = re.search(r"Reference answer:\n(.*?)\n\nCandidate answer:", text, re.DOTALL)
    cand = re.search(r"Candidate answer:\n(.*?)\n\nRubric:", text, re.DOTALL)
    reference = (ref.group(1) if ref else "").strip().lower()
    candidate = (cand.group(1) if cand else "").strip().lower()
    if "not_found" in candidate:
        score = 1
    elif reference and reference in candidate.replace(",", ""):
        score = 5
    else:
        rw, cw = _content_words(reference), _content_words(candidate)
        overlap = len(rw & cw) / max(1, len(rw))
        score = 3 if overlap >= 0.5 else 1
    if rng.random() > p.quality:
        score = {5: 2, 3: 4, 1: 4}.get(score, score)
    verdict = "pass" if score >= 4 else "fail"
    return json.dumps(
        {
            "score": score,
            "verdict": verdict,
            "reason": "Heuristic mock judge: compares the candidate with the reference answer.",
        }
    )
