"""Read-only guard policy inspection tool."""
from __future__ import annotations

import json

SCHEMA = {"type": "object", "properties": {"tool_name": {"type": "string"}, "args": {"type": "object"}},
          "required": ["tool_name"], "additionalProperties": False}


def make_handler(policy, store):
    """Build a Hermes-compatible JSON-string tool handler."""
    def guard_check(args: dict, **kwargs) -> str:
        """Return a dry-run policy decision as JSON."""
        try:
            try:
                from .policy import classify, evaluate
            except ImportError:
                from policy import classify, evaluate
            tool_name = str(args.get("tool_name", ""))
            tool_args = args.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            settings = policy()
            intent = classify(tool_name, tool_args, settings.get("spend_tool_patterns"), settings.get("risky_tool_patterns"))
            result = evaluate(tool_name, tool_args, settings, store.spent_today(intent.currency), kill=store.kill())
            detail = result["intent"]
            return json.dumps({"decision": result["decision"], "rule": result["rule"], "kind": result["kind"],
                               "amount": detail.amount, "currency": detail.currency, "payee": detail.payee,
                               "explanation": f"{tool_name}: {result['rule']} ({result['decision']})"})
        except Exception as exc:
            return json.dumps({"decision": "block", "rule": "evaluation_error", "kind": "unknown",
                               "amount": None, "currency": "USD", "payee": "", "explanation": str(exc)})
    return guard_check
