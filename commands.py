"""Human-readable operator commands for AgentPay Guard."""
from __future__ import annotations

import json
from datetime import datetime

HELP = "AgentPay Guard: status | queue | approve <id> | deny <id> | ledger [n] [--json] | policy | kill on|off | help"


def _time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


def _table(rows: list[dict]) -> str:
    columns = [("id", 8), ("ref", 8), ("time", 5), ("tool", 16), ("amount", 12), ("payee", 14), ("rule", 19), ("status", 10)]
    header = "  ".join(name.ljust(width) for name, width in columns)
    lines = [header, "  ".join("-" * width for _, width in columns)]
    for row in rows:
        amount = "--" if row.get("amount") is None else f"${float(row['amount']):.2f} {row.get('currency', 'USD')}"
        values = [str(row.get("id", "")), str(row.get("ref") or "--"), _time(row.get("ts", "")),
                  str(row.get("tool", "")), amount, str(row.get("payee") or "--"), str(row.get("rule", "")),
                  str(row.get("status", row.get("decision", "")))]
        lines.append("  ".join(value[:width].ljust(width) for value, (_, width) in zip(values, columns)))
    return "\n".join(lines)


def dispatch(raw: str, store, policy) -> str:
    """Run one slash or CLI management command."""
    parts = raw.strip().split()
    command = parts[0] if parts else "help"
    if command in {"help", "-h", "--help"}:
        return HELP
    if command == "status":
        settings = policy()
        return (f"AgentPay Guard ({settings['mode']})\nSpent today: ${store.spent_today():.2f} / ${settings['daily_max']:.2f}\n"
                f"Per call: ${settings['per_call_max']:.2f} | Approval threshold: ${settings['approval_threshold']:.2f}\n"
                f"Pending approvals: {len(store.pending())} | Kill switch: {'ON' if store.kill() else 'off'}")
    if command == "policy":
        return json.dumps(policy(), indent=2, sort_keys=True)
    if command == "queue":
        rows = store.queue() if "--all" in parts else store.pending()
        return _table(rows) if rows else "Queue is empty."
    if command in {"approve", "deny"}:
        if len(parts) < 2:
            return f"Usage: {command} <id>"
        item = store.find(parts[1])
        if not item or item.get("status") != "pending":
            return "Pending queue item not found."
        if command == "deny":
            updated = store.set_status(parts[1], "denied", "operator")
            if updated:
                store.append({**{key: updated.get(key) for key in ("id", "tool", "payee", "amount", "currency", "session_id", "args_hash")},
                              "ref": updated.get("id"), "ts": updated.get("decided_at"), "decision": "denied", "rule": "operator_decision", "decided_by": "operator"})
            return f"Denied {parts[1]}."
        if item.get("rule") in {"kill_switch", "payee_not_allowed", "currency_not_allowed", "per_call_max", "daily_max"}:
            return f"Cannot approve {item['rule']}; raise the limit in config first."
        updated = store.set_status(parts[1], "approved", "operator")
        if updated:
            store.append({**{key: updated.get(key) for key in ("id", "tool", "payee", "amount", "currency", "session_id", "args_hash")},
                          "ref": updated.get("id"), "ts": updated.get("decided_at"), "decision": "approved", "rule": "operator_decision", "decided_by": "operator"})
        item["status"] = "approved"
        store.add_grant(item, int(policy().get("grant_ttl_minutes", 30)))
        return f"Approved exact call {parts[1]} once; hard limits will be rechecked when used."
    if command == "ledger":
        try:
            count = max(1, min(int(parts[1]) if len(parts) > 1 and parts[1] != "--json" else 20, 5000))
        except ValueError:
            return "Usage: ledger [n] [--json]"
        rows = store.ledger()[-count:]
        if "--json" in parts:
            return json.dumps(rows, indent=2)
        return _table(rows) if rows else "Ledger is empty."
    if command == "kill":
        if len(parts) < 2 or parts[1] not in {"on", "off"}:
            return "Usage: kill on|off"
        store.set_kill(parts[1] == "on")
        return f"Kill switch {'ON' if parts[1] == 'on' else 'off'}."
    return f"Unknown subcommand: {command}\n{HELP}"


def register(ctx, store, policy) -> None:
    """Register slash and terminal command surfaces."""
    def slash(raw_args: str = "", **kwargs) -> str:
        return dispatch(raw_args, store, policy)
    ctx.register_command("guard", handler=slash, description="Inspect and control AgentPay Guard", args_hint="<status|queue|approve|deny|ledger|policy|kill>")

    def setup(parser) -> None:
        parser.add_argument("action", choices=["status", "queue", "approve", "deny", "ledger", "policy", "kill"])
        parser.add_argument("value", nargs="?")

    def handler(args) -> None:
        value = args.action + (" " + args.value if args.value else "")
        print(dispatch(value, store, policy))
    ctx.register_cli_command(name="agentpay-guard", help="Inspect and control AgentPay Guard", setup_fn=setup,
                             handler_fn=handler, description="Guard policy, queue, one-time approvals and ledger")
