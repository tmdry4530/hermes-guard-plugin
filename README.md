# AgentPay Guard

**Policy guardrails for payment, MCP, and destructive tool calls in Hermes.**

## Why

Agents with payment and MCP tools can overspend, pay the wrong payee, double pay, or run destructive commands. Hermes's native MCP `spend_limit` covers only MCP servers that declare costs. AgentPay Guard applies policy to any Hermes tool by name and arguments.

## How it works

```text
Hermes tool call
      |
      v
name tokens + argument inspection
      |
      +-- non-spending, safe ------> allow + ledger decision
      +-- hard limit -------------> block + queue
      +-- approval required ------> Hermes approval (escalate)
      |                              or block + queue (strict)
      +-- approved exact call ----> grant, recheck limits, run once
                                      |
                                      v
                              post-call spend ledger
```

## Install

```sh
hermes plugins install chamdom/hermes-guard-plugin
hermes plugins enable agentpay-guard
```

## Configure

Add settings under `plugins.entries.agentpay-guard.settings` in Hermes `config.yaml`:

```yaml
plugins:
  entries:
    agentpay-guard:
      enabled: true
      settings:
        mode: escalate
        per_call_max: 50
        daily_max: 200
        approval_threshold: 20
        currency: [USD]
        payee_allowlist: []
        spend_tool_patterns: []
        risky_tool_patterns: []
        require_approval_tools: []
        grant_ttl_minutes: 30
```

Spend and risk defaults use tool-name tokens, including camelCase words. The amount keys `amount`, `amount_usd`, and `amount_cents` identify spending on any tool; `price`, `total`, `value`, and `cost` do so only when the tool name indicates spending or a sibling `currency` is present. A spend-named call without a readable amount escalates in `escalate` mode and blocks in `strict` mode.

An optional `policy.json` in `<HERMES_HOME>/plugin-data/agentpay-guard/` overrides recognized settings. Invalid JSON and values are ignored with a warning. No YAML parser dependency is needed.

## Commands

| Command | Action |
| --- | --- |
| `/guard status` | Show today's tracked spend, limits, pending count, and kill switch. |
| `/guard queue` | Show pending approvals in an aligned table. |
| `/guard approve <id>` | Grant one exact tool-and-arguments call, subject to hard-limit rechecks. |
| `/guard deny <id>` | Deny a queued call. |
| `/guard ledger [n]` | Show recent decisions; add `--json` for JSON output. |
| `/guard policy` | Print effective policy. |
| `/guard kill on\|off` | Toggle the spend kill switch. |

The same actions are available through `hermes agentpay-guard ...`. `/guard approve` refuses hard-limit violations; raise the relevant config limit first. Grants bind to a tool and argument hash, expire after `grant_ttl_minutes`, and are single-use. Hermes `[a]lways` approval keys include tool, payee, and an argument hash prefix, so they apply only to that exact call.

## Interaction with Hermes approvals

In `escalate` mode, threshold calls, unknown spend amounts, and configured approval tools use Hermes's approval gate. In `strict` mode, approval-required and risky calls block and enter the queue for an operator grant. Non-interactive Hermes approval requests fail closed. If the user runs Hermes with `--yolo`, Hermes may auto-approve escalations; use strict mode for hard guarantees. Hard limits always block regardless of approval mode.

## Disclosure

The plugin uses no network, subprocess, credentials, or background process. It writes queue, ledger, kill switch, and grants only under `<HERMES_HOME>/plugin-data/agentpay-guard`. Ledger and queue entries store an argument hash, never raw arguments.

## Limitations

Classification is deterministic but heuristic. A value-moving tool with an unrelated name and no recognized amount fields may not be identified as spending. Daily totals use UTC dates and count calls only after successful-looking execution hooks. Reconcile external payment outcomes independently. Hermes owns its approval prompt and `--yolo` behavior.

## License

MIT. See [LICENSE](LICENSE).
