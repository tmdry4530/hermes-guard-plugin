from commands import dispatch
from policy import classify
from store import Store

def test_status_human_friendly(tmp_path, policy):
    output = dispatch("status", Store(tmp_path), lambda: policy)
    assert "AgentPay Guard" in output and "Spent today" in output

def test_queue_table(tmp_path):
    store = Store(tmp_path); item = store.enqueue("pay_invoice", {"amount": 2}, classify("pay_invoice", {"amount": 2}), "approval_threshold")
    output = dispatch("queue", store, lambda: {})
    assert item["id"] in output and "pay_invoice" in output and "rule" in output

def test_ledger_table_and_json(tmp_path):
    store = Store(tmp_path); store.append({"id": "abc", "tool": "pay", "amount": 1, "currency": "USD", "decision": "allow", "ts": "2025-01-01T12:00:00+00:00"})
    assert "allow" in dispatch("ledger", store, lambda: {})
    assert '"id": "abc"' in dispatch("ledger --json", store, lambda: {})

def test_policy_command(tmp_path, policy):
    assert '"daily_max": 200' in dispatch("policy", Store(tmp_path), lambda: policy)

def test_refuses_hard_limit_approval(tmp_path, policy):
    store = Store(tmp_path); item = store.enqueue("pay", {"amount": 100}, classify("pay", {}), "per_call_max")
    assert "raise the limit in config first" in dispatch("approve " + item["id"], store, lambda: policy)

def test_unknown_queue_id(tmp_path, policy):
    assert "not found" in dispatch("approve missing", Store(tmp_path), lambda: policy)

def test_kill_command(tmp_path, policy):
    store = Store(tmp_path)
    assert dispatch("kill on", store, lambda: policy) == "Kill switch ON."
    assert store.kill()
