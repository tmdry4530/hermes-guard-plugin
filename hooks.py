"""Hermes hooks for spend decisions, execution accounting, and approval responses."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

try:
    from .policy import classify, evaluate
    from .store import Store, args_hash
except ImportError:
    from policy import classify, evaluate
    from store import Store, args_hash

logger = logging.getLogger(__name__)
DEFAULTS = {"per_call_max": 50.0, "daily_max": 200.0, "approval_threshold": 20.0, "currency": ["USD"],
            "payee_allowlist": [], "spend_tool_patterns": [], "risky_tool_patterns": [],
            "require_approval_tools": [], "mode": "escalate", "grant_ttl_minutes": 30}
HARD_RULES = {"kill_switch", "payee_not_allowed", "currency_not_allowed", "per_call_max", "daily_max"}


def make_policy(ctx, store: Store) -> dict:
    """Load validated plugin settings and optional policy.json overrides."""
    policy = {key: ctx.get_config(key, default) for key, default in DEFAULTS.items()}
    path = store.root / "policy.json"
    if not path.exists():
        return _validated(policy)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("root must be an object")
        policy.update({key: value for key, value in data.items() if key in DEFAULTS})
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Ignoring invalid AgentPay Guard policy.json: %s", exc)
    return _validated(policy)


def _validated(policy: dict) -> dict:
    numeric = {"per_call_max", "daily_max", "approval_threshold", "grant_ttl_minutes"}
    lists = {"currency", "payee_allowlist", "spend_tool_patterns", "risky_tool_patterns", "require_approval_tools"}
    for key in numeric:
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            logger.warning("Ignoring invalid AgentPay Guard setting %s", key)
            policy[key] = DEFAULTS[key]
    for key in lists:
        value = policy.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            logger.warning("Ignoring invalid AgentPay Guard setting %s", key)
            policy[key] = DEFAULTS[key]
    if policy.get("mode") not in {"strict", "escalate"}:
        logger.warning("Ignoring invalid AgentPay Guard setting mode")
        policy["mode"] = "escalate"
    return policy


def _entry(tool: str, args: dict, decision: str, rule: str, intent, session: str = "", ident: str = "", ref: str = "") -> dict:
    digest = args_hash(tool, args)
    return {"id": ident or digest[:12], "ref": ref or ident or digest[:12], "ts": datetime.now(timezone.utc).isoformat(), "tool": tool,
            "payee": intent.payee, "amount": intent.amount, "currency": intent.currency,
            "decision": decision, "rule": rule, "session_id": session, "args_hash": digest}


def create_hooks(ctx, store: Store):
    """Create pre-call, post-call, and approval-response callbacks."""

    def policy() -> dict:
        return make_policy(ctx, store)

    def pre_tool_call(tool_name: str = "", args=None, session_id: str = "", task_id: str = "", tool_call_id: str = "", **kwargs):
        call_args = args if isinstance(args, dict) else {}
        session = session_id or task_id
        try:
            settings = policy()
            first = classify(tool_name, call_args, settings["spend_tool_patterns"], settings["risky_tool_patterns"])
            result = evaluate(tool_name, call_args, settings, store.spent_today(first.currency), kill=store.kill())
            intent = result["intent"]
            digest = args_hash(tool_name, call_args)
            grant_used = False
            if result["decision"] != "allow" and result["rule"] not in HARD_RULES:
                grant_used = store.consume_grant(tool_name, call_args, settings, store.spent_today(intent.currency))
            if grant_used:
                approved = next((row for row in store.queue() if row.get("args_hash") == digest and row.get("status") == "approved"), None)
                queue_id = approved.get("id", "") if approved else ""
                store.append(_entry(tool_name, call_args, "granted", result["rule"], intent, session, queue_id))
                store.record_in_flight(tool_call_id, digest, session, queue_id or digest[:12], queue_id)
                return None
            decision = result["decision"]
            if decision == "allow":
                store.append(_entry(tool_name, call_args, "allow", result["rule"], intent, session))
                store.record_in_flight(tool_call_id, digest, session, digest[:12])
                return None
            item = store.enqueue(tool_name, call_args, intent, result["rule"], session)
            if result["rule"] in HARD_RULES:
                item = store.set_status(item["id"], "rejected") or item
                store.append(_entry(tool_name, call_args, "blocked", result["rule"], intent, session, item["id"]))
                amount = f"${intent.amount:.2f} {intent.currency}" if intent.amount is not None else "amount unknown"
                payee = f" to {intent.payee}" if intent.payee else ""
                return {"action": "block", "message": f"AgentPay Guard blocked {tool_name} ({amount}{payee}): {result['rule'].replace('_', ' ')}. Queue id {item['id']}. Do not retry; ask the user to run `/guard approve {item['id']}` (or `hermes agentpay-guard approve {item['id']}`)."}
            if decision == "block" or (result["kind"] == "risk" and settings.get("mode") == "strict"):
                store.append(_entry(tool_name, call_args, "blocked", result["rule"], intent, session, item["id"]))
                amount = f"${intent.amount:.2f} {intent.currency}" if intent.amount is not None else "amount unknown"
                payee = f" to {intent.payee}" if intent.payee else ""
                return {"action": "block", "message": f"AgentPay Guard blocked {tool_name} ({amount}{payee}): {result['rule'].replace('_', ' ')}. Queue id {item['id']}. Do not retry; ask the user to run `/guard approve {item['id']}` (or `hermes agentpay-guard approve {item['id']}`)."}
            store.append(_entry(tool_name, call_args, "escalated", result["rule"], intent, session, item["id"]))
            store.record_in_flight(tool_call_id, digest, session, item["id"], item["id"])
            key = f"agentpay-guard:{tool_name}:{intent.payee or 'none'}:{digest[:12]}"
            amount = f"${intent.amount:.2f} {intent.currency}" if intent.amount is not None else "amount unknown"
            return {"action": "approve", "message": f"AgentPay Guard requires approval for {tool_name} ({amount}, payee {intent.payee or 'unspecified'}): {result['rule']}.", "rule_key": key}
        except Exception as exc:
            try:
                intent = classify(tool_name, call_args, [], [])
                if intent.spending:
                    item = store.enqueue(tool_name, call_args, intent, "evaluation_error", session)
                    store.set_status(item["id"], "rejected")
                    store.append(_entry(tool_name, call_args, "blocked", "evaluation_error", intent, session, item["id"]))
                    return {"action": "block", "message": f"AgentPay Guard blocked {tool_name}: policy evaluation failed closed ({exc}). Queue id {item['id']}. Do not retry; ask the user to resolve the policy error."}
            except Exception as inner_exc:
                logger.error("AgentPay Guard could not record fail-closed decision: %s", inner_exc)
        return None

    def post_tool_call(tool_name: str = "", args=None, result=None, session_id: str = "", task_id: str = "", tool_call_id: str = "", status: str = "", error_type: str = "", **kwargs):
        call_args = args if isinstance(args, dict) else {}
        digest = args_hash(tool_name, call_args)
        pending = store.consume_in_flight(tool_call_id, digest)
        if pending is None:
            return
        if status and status.lower() != "ok":
            return
        settings = policy()
        intent = classify(tool_name, call_args, settings["spend_tool_patterns"], settings["risky_tool_patterns"])
        if intent.spending and intent.amount is not None:
            store.append(_entry(tool_name, call_args, "executed", "post_tool_call", intent,
                                session_id or task_id, pending.get("id", ""), pending.get("ref", "")))

    def post_approval_response(pattern_key: str = "", choice: str = "", decided_by: str = "", **kwargs):
        marker = "agentpay-guard:"
        if marker not in pattern_key:
            return
        digest_short = pattern_key.rsplit(":", 1)[-1]
        item = next((row for row in store.pending() if row.get("args_hash", "").startswith(digest_short)), None)
        if item is None:
            return
        statuses = {"once": "approved", "session": "approved", "always": "approved", "smart_approve": "approved",
                    "deny": "denied", "smart_deny": "denied", "timeout": "expired", "cancelled": "expired", "notify_failed": "expired"}
        status = statuses.get(choice)
        if status is None:
            return
        updated = store.set_status(item["id"], status, str(decided_by)[:80])
        if updated:
            store.append({**{key: updated.get(key) for key in ("id", "tool", "payee", "amount", "currency", "session_id", "args_hash")},
                          "ref": updated.get("id"), "ts": datetime.now(timezone.utc).isoformat(), "decision": status,
                          "rule": "human_approval_response", "decided_by": str(decided_by)[:80]})

    return pre_tool_call, post_tool_call, post_approval_response, policy
