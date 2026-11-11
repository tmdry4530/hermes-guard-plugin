"""Classify tool calls and apply the configured guard policy."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

SPEND_WORDS = {"pay", "payment", "payments", "payout", "purchase", "checkout", "transfer", "buy", "invoice", "charge", "tip", "donate", "withdraw", "subscribe"}
RISK_WORDS = {"delete", "remove", "drop", "destroy", "wipe", "send_email", "email_send"}
AMOUNT_KEYS = {"amount", "amount_usd", "amount_cents", "price", "total", "value", "cost"}
PAYEE_KEYS = {"payee", "to", "recipient", "merchant", "vendor", "address"}
RISKY_COMMANDS = re.compile(r"rm\s+-rf|curl\b[^\n|]*\|\s*(?:sh|bash)|git\s+push\s+--force", re.I)


@dataclass
class Intent:
    """Extracted spend or payee information for one tool call."""

    spending: bool
    amount: float | None = None
    currency: str = "USD"
    payee: str = ""
    reason: str = ""


def _words(name: str) -> list[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [part.lower() for part in re.split(r"[_\-.:/]+|\s+", separated) if part]


def _named_spend(tool: str) -> bool:
    words = _words(tool)
    pairs = {tuple(words[i:i + 2]) for i in range(len(words) - 1)}
    return bool(set(words) & SPEND_WORDS) or bool(pairs & {("send", "money"), ("create", "order"), ("place", "order"), ("submit", "order")})


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower(), child
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def _amount(value: Any) -> tuple[float | None, str]:
    currency = "USD"
    raw = value
    if isinstance(value, dict):
        raw = value.get("amount", value.get("value"))
        currency = str(value.get("currency", "USD")).upper()
    elif isinstance(value, str):
        match = re.search(r"([A-Z]{3})\s*([0-9]+(?:\.[0-9]+)?)|([0-9]+(?:\.[0-9]+)?)\s*([A-Z]{3})", value, re.I)
        if match:
            raw, currency = (match.group(2), match.group(1)) if match.group(1) else (match.group(3), match.group(4))
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None, currency
    return (parsed, currency) if parsed > 0 and parsed < float("inf") else (None, currency)


def classify(tool_name: str, args: Any, spend_patterns: list[str] | None = None, risky_patterns: list[str] | None = None) -> Intent:
    """Identify spending, risk, amount, currency, and payee from a call."""
    named = _named_spend(tool_name) or any(fnmatch.fnmatchcase(tool_name.lower(), pattern.lower()) for pattern in (spend_patterns or []))
    values = list(_walk(args))
    has_currency = any(key.rsplit(".", 1)[-1] == "currency" for key, _ in values)
    amount = None
    currency = "USD"
    payee = ""
    for key, value in values:
        leaf = key.rsplit(".", 1)[-1]
        if leaf == "currency" and isinstance(value, str):
            currency = value.upper()
        if leaf in AMOUNT_KEYS and (named or leaf in {"amount", "amount_usd", "amount_cents"} or has_currency):
            parsed, parsed_currency = _amount(value)
            if leaf == "amount_cents" and parsed is not None:
                parsed /= 100
            if parsed is not None:
                amount, currency = parsed, parsed_currency
        if leaf in PAYEE_KEYS and isinstance(value, str) and value.strip():
            payee = value.strip()
    return Intent(named or amount is not None, amount, currency, payee, "spend_tool" if named else "amount_argument")


def evaluate(tool_name: str, args: Any, policy: dict, spent_today: float = 0.0, *, kill: bool = False) -> dict:
    """Evaluate a call using hard limits before approval thresholds."""
    intent = classify(tool_name, args, policy.get("spend_tool_patterns"), policy.get("risky_tool_patterns"))
    words = _words(tool_name)
    joined = "_".join(words)
    risky = any(word in RISK_WORDS for word in words) or joined in RISK_WORDS or any(fnmatch.fnmatchcase(tool_name.lower(), pattern.lower()) for pattern in policy.get("risky_tool_patterns", []))
    command = args.get("command", "") if isinstance(args, dict) else ""
    risky = risky or (tool_name.lower() in {"terminal", "shell"} and isinstance(command, str) and bool(RISKY_COMMANDS.search(command)))
    if not intent.spending:
        return {"kind": "risk" if risky else "pass", "decision": "approve" if risky else "allow", "rule": "risky_tool" if risky else "non_spending", "intent": intent}
    rule = None
    allowed_payees = policy.get("payee_allowlist", [])
    currencies = policy.get("currency", ["USD"])
    if kill:
        rule = "kill_switch"
    elif allowed_payees and intent.payee.casefold() not in {str(item).casefold() for item in allowed_payees}:
        rule = "payee_not_allowed"
    elif intent.currency.upper() not in {str(item).upper() for item in currencies}:
        rule = "currency_not_allowed"
    elif intent.amount is None:
        rule = "amount_unrecognized"
    elif intent.amount > float(policy.get("per_call_max", 50)):
        rule = "per_call_max"
    elif spent_today + intent.amount > float(policy.get("daily_max", 200)):
        rule = "daily_max"
    if rule:
        decision = "approve" if rule == "amount_unrecognized" and policy.get("mode", "escalate") == "escalate" else "block"
        return {"kind": "spend", "decision": decision, "rule": rule, "intent": intent}
    approval = intent.amount > float(policy.get("approval_threshold", 20)) or any(fnmatch.fnmatchcase(tool_name.lower(), pattern.lower()) for pattern in policy.get("require_approval_tools", []))
    if approval and policy.get("mode", "escalate") == "strict":
        return {"kind": "spend", "decision": "block", "rule": "approval_required_strict", "intent": intent}
    return {"kind": "spend", "decision": "approve" if approval else "allow", "rule": "approval_threshold" if approval else "within_limits", "intent": intent}
