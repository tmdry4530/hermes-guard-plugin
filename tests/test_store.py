from policy import classify
from store import Store, args_hash

def test_args_hash_order_independent():
    assert args_hash("x", {"a": 1, "b": 2}) == args_hash("x", {"b": 2, "a": 1})

def test_queue_persists(tmp_path):
    store = Store(tmp_path)
    item = store.enqueue("pay", {"amount": 2}, classify("pay", {"amount": 2}), "approval_threshold")
    assert Store(tmp_path).find(item["id"])["tool"] == "pay"

def test_queue_deduplicates_pending(tmp_path):
    store = Store(tmp_path)
    args = {"amount": 2}
    one = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    two = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    assert one["id"] == two["id"]

def test_queue_trimmed(tmp_path):
    store = Store(tmp_path)
    for amount in range(505):
        args = {"amount": amount + 1}
        store.enqueue("pay", args, classify("pay", args), "r")
    assert len(store.queue()) == 500

def test_kill_switch(tmp_path):
    store = Store(tmp_path)
    store.set_kill(True)
    assert store.kill()

def test_only_executed_counts(tmp_path):
    store = Store(tmp_path)
    for decision in ("allow", "escalated", "blocked", "approved", "granted", "denied", "executed"):
        store.append({"decision": decision, "amount": 3, "currency": "USD", "ts": ""})
    assert store.spent_today() == 0
    from datetime import datetime, timezone
    store.append({"decision": "executed", "amount": 4, "currency": "USD", "ts": datetime.now(timezone.utc).isoformat()})
    assert store.spent_today() == 4

def test_status_transition_once(tmp_path):
    store = Store(tmp_path)
    item = store.enqueue("pay", {"amount": 2}, classify("pay", {}), "r")
    assert store.set_status(item["id"], "denied")
    assert store.set_status(item["id"], "approved") is None

def test_grant_bound_to_exact_args(tmp_path, policy):
    store = Store(tmp_path)
    args = {"amount": 25}
    item = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    store.set_status(item["id"], "approved")
    store.add_grant(item)
    assert not store.consume_grant("pay", {"amount": 26}, policy, 0)

def test_grant_single_use(tmp_path, policy):
    store = Store(tmp_path)
    args = {"amount": 25}
    item = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    store.set_status(item["id"], "approved")
    store.add_grant(item)
    assert store.consume_grant("pay", args, policy, 0)
    assert not store.consume_grant("pay", args, policy, 0)

def test_atomic_store_creates_json(tmp_path):
    store = Store(tmp_path)
    store.append({"decision": "blocked"})
    assert (tmp_path / "ledger.json").exists()

def test_grant_rechecks_hard_daily_limit(tmp_path, policy):
    store = Store(tmp_path)
    args = {"amount": 10}
    item = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    store.set_status(item["id"], "approved")
    store.add_grant(item)
    assert not store.consume_grant("pay", args, policy, 195)
    assert store.find(item["id"])["grant"]["used"] is False

def test_grant_rechecks_kill_switch(tmp_path, policy):
    store = Store(tmp_path)
    args = {"amount": 10}
    item = store.enqueue("pay", args, classify("pay", args), "approval_threshold")
    store.set_status(item["id"], "approved")
    store.add_grant(item)
    store.set_kill(True)
    assert not store.consume_grant("pay", args, policy, 0)
