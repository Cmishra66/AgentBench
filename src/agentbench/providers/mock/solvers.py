"""Rule-based solvers for the bundled task families, plus realistic seeded failure modes.

Each solver reads the prompt the way a model would (no access to labels) and returns text.
``bad`` is True when the profile's dice say this answer should be corrupted.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from agentbench.providers.mock.profiles import MockProfile

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
STOPWORDS = set(
    """a an the of to in on for and or is are was were be by with from at as what which who whom
how many much does do did can could may must should within long often quickly whose that this these those
it its their there any if when where why into than per about under over will would""".split()
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _date_iso(text: str) -> str | None:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\s*\(DD/MM/YYYY\)", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2}) ([A-Za-z]{3,9}) (\d{4})\b", text)
    if m and m.group(2).lower()[:3] in [x[:3] for x in MONTHS]:
        month = [x[:3] for x in MONTHS].index(m.group(2).lower()[:3]) + 1
        return f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b([A-Za-z]{3,9}) (\d{1,2}), (\d{4})\b", text)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"
    return None


def _wrap_json(obj: dict[str, Any], rng: random.Random) -> str:
    body = json.dumps(obj, indent=2)
    return f"```json\n{body}\n```" if rng.random() < 0.6 else f"Here is the extracted data:\n{body}"


# --------------------------------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------------------------------


def solve_extraction(prompt: str, rng: random.Random, p: MockProfile, bad: bool, slip: bool) -> str:
    m = re.search(r"<invoice>\n(.*?)\n</invoice>", prompt, re.DOTALL)
    inv = m.group(1) if m else prompt
    out: dict[str, Any] = {
        k: None for k in ["invoice_number", "vendor", "invoice_date", "currency", "subtotal", "tax", "total"]
    }
    num = re.search(r"\b(INV-\d+)\b", inv)
    out["invoice_number"] = num.group(1) if num else None
    lines = inv.splitlines()
    vendor = re.search(r"^Vendor: (.+)$", inv, re.M) or re.search(r"issued by (.+?) on ", inv)
    if vendor:
        out["vendor"] = vendor.group(1).strip()
    elif lines and " - TAX INVOICE" in lines[0]:
        out["vendor"] = lines[0].split(" - TAX INVOICE")[0].title()
    elif lines:
        out["vendor"] = lines[0].strip()
    out["invoice_date"] = _date_iso(inv)
    cur = re.search(r"(?:Currency: |All amounts in )([A-Z]{3})", inv) or re.search(r"\b(USD|EUR|GBP)\b", inv)
    out["currency"] = cur.group(1) if cur else None
    amt = r"([\d,]+\.\d{2})"
    for key, pats in {
        "subtotal": [rf"^Subtotal: {amt}", rf"^Net amount: {amt}", rf"total [A-Z]{{3}} {amt} before tax"],
        "tax": [
            rf"^Tax \(\d+%\): {amt}",
            rf"^VAT \d+%: {amt}",
            rf"^Tax \d+%: {amt}",
            rf"adds [A-Z]{{3}} {amt}",
        ],
        "total": [
            rf"^Total Due: {amt}",
            rf"^Amount payable: {amt}",
            rf"^Invoice total: {amt}",
            rf"amount payable to [A-Z]{{3}} {amt}",
        ],
    }.items():
        for pat in pats:
            mm = re.search(pat, inv, re.M)
            if mm:
                out[key] = _num(mm.group(1))
                break
    if bad:
        mode = rng.choice(["total", "date", "tax", "vendor", "number"])
        if mode == "total":
            incl = re.search(rf"^Total including previous balance: {amt}", inv, re.M)
            out["total"] = _num(incl.group(1)) if incl else out["subtotal"]
        elif mode == "date":
            raw = re.search(r"(?:Date: |Issued |on |Invoice Date: )([^|\n]+?)(?: \||\.|$| \(DD)", inv, re.M)
            out["invoice_date"] = raw.group(1).strip() if raw else out["invoice_date"]
        elif mode == "tax":
            out["tax"] = 0.0 if out["tax"] is None else None
        elif mode == "vendor":
            out["vendor"] = "Northwind Freight"
        else:
            po = re.search(r"\b(PO-\d+)\b", inv)
            out["invoice_number"] = po.group(1) if po else (out["invoice_number"] or "").replace("INV-", "")
    return _wrap_json(out, rng) if slip else json.dumps(out)


# --------------------------------------------------------------------------------------------------
# JSON formatting
# --------------------------------------------------------------------------------------------------


def solve_json_format(prompt: str, rng: random.Random, p: MockProfile, bad: bool, slip: bool) -> str:
    note = prompt.split("Support note:\n", 1)[-1]
    cid = re.search(r"\b(C-\d{5})\b", note)
    sev = re.search(r"\b(low|medium|high|critical)\b", note)
    name = (
        re.search(r"^(?:On .+? we heard from )?([A-Z][\w\-]+ [A-Z][\w\-]+)(?: \(| wrote)", note)
        or re.search(r"we heard from ([A-Z][\w\-]+ [A-Z][\w\-]+)", note)
        or re.search(r", ([A-Z][\w\-]+ [A-Z][\w\-]+) wrote in", note)
    )
    issue = (
        re.search(r"reported on .+? that (.+?)\. They", note)
        or re.search(r"because (.+?)\. Severity", note)
        or re.search(r"\): (.+?)\. The customer", note)
    )
    neg = re.search(r"\b(no callback|does not need a callback)\b", note, re.I)
    out: dict[str, Any] = {
        "customer_name": name.group(1) if name else None,
        "customer_id": cid.group(1) if cid else None,
        "reported_on": _date_iso(note),
        "issue_summary": issue.group(1)[0].upper() + issue.group(1)[1:]
        if issue
        else "Customer reported an issue",
        "severity": sev.group(1) if sev else "medium",
        "callback_requested": not bool(neg),
    }
    if bad:
        mode = rng.choice(["severity_case", "bool_string", "date", "extra_key", "id"])
        if mode == "severity_case":
            out["severity"] = str(out["severity"]).capitalize()
        elif mode == "bool_string":
            out["callback_requested"] = "yes" if out["callback_requested"] else "no"
        elif mode == "date":
            raw = re.search(
                r"(?:reported on |on )([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]+ \d{4})", note
            )
            out["reported_on"] = raw.group(1) if raw else out["reported_on"]
        elif mode == "extra_key":
            out["notes"] = "Generated from support note"
        else:
            out["customer_id"] = (out["customer_id"] or "").replace("C-", "")
    return _wrap_json(out, rng) if slip else json.dumps(out)


# --------------------------------------------------------------------------------------------------
# grounded document QA
# --------------------------------------------------------------------------------------------------

GENERIC = {
    "window",
    "maximum",
    "minimum",
    "used",
    "use",
    "cities",
    "city",
    "kind",
    "type",
    "rate",
    "number",
    "allowed",
    "required",
    "time",
    "value",
    "amount",
}


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 5:
        w = w[:-3] + "y"
    for suffix in ("ing", "ed", "s"):
        if w.endswith(suffix) and len(w) > len(suffix) + 3:
            w = w[: -len(suffix)]
            break
    return w[:6]


def _content_words(text: str) -> set[str]:
    words = [
        part
        for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", text.lower())
        for part in w.split("-")
        if part
    ]
    out = set()
    for w in words:
        if w in STOPWORDS or w in GENERIC or (len(w) < 3 and not w.isdigit()):
            continue
        out.add(_stem(w))
    return out


_MONEY_Q = re.compile(r"\b(cost|price|fee|how much does)\b", re.I)
_MONEY_A = re.compile(r"(usd|eur|gbp|percent|%|times the|\$|€)", re.I)


def parse_documents(prompt: str) -> dict[str, list[str]]:
    docs: dict[str, list[str]] = {}
    for m in re.finditer(r"\[([a-z0-9\-]+)\] [^\n]+\n(.+?)(?=\n\n\[|\n\nQuestion:|\Z)", prompt, re.DOTALL):
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", m.group(2).strip())
        docs[m.group(1)] = [s.strip() for s in sentences if s.strip()]
    return docs


def best_sentence(
    docs: dict[str, list[str]], question: str
) -> tuple[str | None, str | None, float, set[str]]:
    q = _content_words(question)
    vocab: set[str] = set()
    for sents in docs.values():
        for s in sents:
            vocab |= _content_words(s)
    novel = {w for w in q if w not in vocab}
    best: tuple[str | None, str | None, float] = (None, None, 0.0)
    for doc_id, sents in docs.items():
        for s in sents:
            overlap = len(q & _content_words(s)) / max(1, len(q))
            if overlap > best[2]:
                best = (doc_id, s, overlap)
    if best[1] and _MONEY_Q.search(question) and not _MONEY_A.search(best[1]):
        novel = novel | {"<price not stated>"}
    return best[0], best[1], best[2], novel


def solve_doc_qa(prompt: str, rng: random.Random, p: MockProfile, bad: bool, hallucinate: bool) -> str:
    docs = parse_documents(prompt)
    question = prompt.rsplit("Question:", 1)[-1].strip()
    doc_id, sentence, coverage, novel = best_sentence(docs, question)
    answerable = sentence is not None and coverage >= 0.5 and not novel
    if not answerable and not hallucinate:
        return "NOT_FOUND"
    if sentence is None:
        return "NOT_FOUND"
    if not answerable and hallucinate:
        # Confidently answer from the closest sentence — a classic grounded-QA hallucination.
        return f"{sentence} [{doc_id}]"
    if bad:
        mode = rng.choice(["wrong_doc", "wrong_fact", "no_cite"])
        ids = list(docs)
        if mode == "wrong_doc" and len(ids) > 1:
            other = ids[(ids.index(doc_id) + 1) % len(ids)]
            return f"{sentence} [{other}]"
        if mode == "wrong_fact":
            sents = docs[doc_id]
            alt = sents[(sents.index(sentence) + 1) % len(sents)]
            return f"{alt} [{doc_id}]"
        return sentence
    return f"{sentence} [{doc_id}]"


# --------------------------------------------------------------------------------------------------
# reasoning
# --------------------------------------------------------------------------------------------------


def solve_reasoning(prompt: str, rng: random.Random, p: MockProfile, bad: bool, slip: bool) -> str:
    prob = prompt.split("Problem:", 1)[-1]
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", prob)]
    answer: float | None = None
    note = ""
    if "pallets" in prob and "ships" in prob:
        a, b, c = nums[:3]
        answer = a - b - c if bad else a - b + c
        note = f"Start with {a:g}, subtract {b:g} shipped, add {c:g} received."
    elif "volume discount" in prob:
        q, price, d, t = nums[:4]
        sub = q * price * (1 - d / 100)
        answer = round(sub if bad else sub * (1 + t / 100), 2)
        note = f"Subtotal {q:g} x {price:g}, apply {d:g}% discount" + ("" if bad else f", then {t:g}% tax.")
    elif "km/h" in prob:
        dist, speed, rest = nums[:3]
        minutes = dist / speed * 60
        answer = minutes if bad else minutes + rest
        note = f"Driving time {dist:g}/{speed:g} hours" + ("" if bad else f" plus a {rest:g}-minute stop.")
    elif "boxes" in prob:
        n, k = nums[:2]
        answer = float(int(n) // int(k)) if bad else float(int(n) % int(k))
        note = f"{n:g} boxes over {k:g} trucks leaves a remainder."
    elif "liters" in prob:
        per100 = float(re.search(r"uses ([\d.]+) liters", prob).group(1))
        d = float(re.search(r"depot (\d+) km", prob).group(1))
        answer = round(per100 * (d if bad else 2 * d) / 100, 2)
        note = f"Distance is {'one way' if bad else 'there and back'}; consumption {per100:g} L/100 km."
    if answer is None:
        return "I could not solve this problem."
    shown = f"{answer:.2f}".rstrip("0").rstrip(".")
    if slip:
        return f"{note} So the result is {shown}."
    return f"{note}\nANSWER: {shown}"


# --------------------------------------------------------------------------------------------------
# multi-step instructions
# --------------------------------------------------------------------------------------------------

_STEP_PATTERNS: list[tuple[str, str]] = [
    (r"Remove duplicate words", "dedupe"),
    (r"Sort the words alphabetically", "sort_alpha"),
    (r"Sort the words by length", "sort_len"),
    (r"Reverse the order", "reverse"),
    (r"Keep only words with more than (\d+) letters", "filter_len"),
    (r"to uppercase", "upper"),
    (r"to lowercase", "lower"),
    (r"Keep only the first (\d+) words", "first_n"),
    (r"separated by '([^']*)'", "join"),
]


def solve_instructions(prompt: str, rng: random.Random, p: MockProfile, bad: bool, slip: bool) -> str:
    from agentbench.datasets.generator import _apply_steps

    words_line = re.search(r"Words: (.+)", prompt)
    words = [w.strip() for w in words_line.group(1).split(",")] if words_line else []
    steps: list[tuple[str, Any]] = []
    for line in re.findall(r"^\d+\. (.+)$", prompt, re.M):
        for pat, op in _STEP_PATTERNS:
            m = re.search(pat, line)
            if m:
                arg: Any = m.group(1) if m.groups() else None
                if op in ("filter_len", "first_n"):
                    arg = int(arg)
                steps.append((op, arg))
                break
    if bad and len(steps) > 1:
        steps.pop(rng.randrange(len(steps) - 1))
    result = _apply_steps(words, steps)
    return f"Here is the final result: {result}" if slip else result


# --------------------------------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------------------------------

LABEL_KEYWORDS = {
    "billing": [
        "charged",
        "invoice",
        "billed",
        "billing",
        "refund",
        "surcharge",
        "credit",
        "statement",
        "payment",
    ],
    "shipping": [
        "tracking",
        "delivery",
        "delivered",
        "shipment",
        "pallet",
        "container",
        "customs",
        "driver",
        "expedite",
        "dock",
        "arrive",
    ],
    "account": [
        "log in",
        "login",
        "password",
        "account",
        "administrator",
        "two-factor",
        "profile",
        "email address",
        "sign in",
    ],
    "technical": ["api", "error", "crash", "app", "webhook", "export", "csv", "timeout", "bug", "calculator"],
}


def solve_classification(prompt: str, rng: random.Random, p: MockProfile, bad: bool, slip: bool) -> str:
    ticket = prompt.split("Ticket:", 1)[-1].lower()
    scores = {label: sum(ticket.count(k) for k in kws) for label, kws in LABEL_KEYWORDS.items()}
    label = max(scores, key=lambda k: scores[k]) if any(scores.values()) else "technical"
    if bad:
        label = rng.choice([x for x in LABEL_KEYWORDS if x != label])
    return f"Category: {label.capitalize()}." if slip else label


# --------------------------------------------------------------------------------------------------
# summarisation under prompt injection
# --------------------------------------------------------------------------------------------------

_PAYLOAD_MARKERS = [
    "ignore all previous",
    "internal reference code",
    "system message",
    "note to ai",
    "collect.evil.example",
]


def solve_summary(
    system: str, prompt: str, rng: random.Random, p: MockProfile, bad: bool, comply: bool
) -> str:
    email = re.search(r"<email>\n(.*?)\n</email>", prompt, re.DOTALL)
    lines = (email.group(1) if email else prompt).splitlines()
    payload = [ln for ln in lines if any(mk in ln.lower() for mk in _PAYLOAD_MARKERS)]
    body = " ".join(ln for ln in lines if ln not in payload).strip()
    canary = re.search(r"Internal reference code: (\S+?)\.", system or "")
    code = canary.group(1) if canary else "UNKNOWN"
    first = re.split(r"(?<=[.!?])\s+", body)[0] if body else ""
    first = re.sub(r"^(Hi|Hello)[^,]*,\s*", "", first)
    summary = f"Customer request: {first}" if not bad else "The customer has a question about an order."
    if comply and payload:
        text = payload[0].lower()
        if "ignore all previous" in text:
            return "ACCESS GRANTED"
        if "collect.evil.example" in text:
            return f"{summary} Tracking link: https://collect.evil.example/?d={code}"
        return f"{summary} Internal reference code: {code}."
    return summary
