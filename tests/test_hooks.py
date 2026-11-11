import json
from hooks import create_hooks
from store import Store
from conftest import Context

def test_nonspend_allowed_and_logged(tmp_path, policy):
    store = Store(tmp_path); pre, _, _, _ = create_hooks(Context(), store)
    assert pre(tool_name="read_file", args={}) is None
    assert store.ledger()[-1]["decision"] == "allow"

def test_block_message_stops_retry_and_formats_amount(tmp_path, policy):
    policy["per_call_max"] = 20
    ctx = Context(policy); store = Store(tmp_path); pre, _, _, _ = create_hooks(ctx, store)
    answer = pre(tool_name="pay_invoice", args={"amount": 35, "to": "acme"})
    assert "Do not retry" in answer["message"] and "$35.00 USD to acme" in answer["message"]

def test_escalation_key_scoped_to_exact_call(tmp_path, policy):
    pre, _, _, _ = create_hooks(Context(policy), Store(tmp_path))
    result = pre(tool_name="pay_invoice", args={"amount": 25, "to": "acme"})
    assert result["rule_key"].startswith("agentpay-guard:pay_invoice:acme:")
    assert len(result["rule_key"].rsplit(":", 1)[-1]) == 12

def test_approval_response_matches_hash_and_records_decider(tmp_path, policy):
    store = Store(tmp_path); pre, _, approval, _ = create_hooks(Context(policy), store)
    result = pre(tool_name="pay_invoice", args={"amount": 25})
    approval(pattern_key="plugin_rule:" + result["rule_key"], choice="always", decided_by="operator")
    item = store.queue()[0]
    assert item["status"] == "approved"
    assert store.ledger()[-1]["decided_by"] == "operator"

def test_approval_response_expiry_choices(tmp_path, policy):
    for choice in ("timeout", "cancelled", "notify_failed"):
        store = Store(tmp_path); pre, _, approval, _ = create_hooks(Context(policy), store)
        result = pre(tool_name="pay_invoice", args={"amount": 25, "tag": choice})
        approval(pattern_key="plugin_rule:" + result["rule_key"], choice=choice)
        assert store.queue()[0]["status"] == "expired"

def test_denial_choices(tmp_path, policy):
    for choice in ("deny", "smart_deny"):
        store = Store(tmp_path); pre, _, approval, _ = create_hooks(Context(policy), store)
        result = pre(tool_name="pay_invoice", args={"amount": 25, "tag": choice})
        approval(pattern_key="plugin_rule:" + result["rule_key"], choice=choice)
        assert store.queue()[0]["status"] == "denied"

def test_approved_counts_only_on_post_execution(tmp_path, policy):
    store = Store(tmp_path); pre, post, _, _ = create_hooks(Context(policy), store)
    args = {"amount": 25}
    pre(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="id1")
    assert store.spent_today() == 0
    post(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="id1", result="ok", status="ok")
    assert store.spent_today() == 25

def test_post_requires_same_hash(tmp_path, policy):
    store = Store(tmp_path); pre, post, _, _ = create_hooks(Context(policy), store)
    pre(tool_name="pay_invoice", args={"amount": 25}, session_id="s", tool_call_id="id1")
    post(tool_name="pay_invoice", args={"amount": 26}, session_id="s", tool_call_id="id1", status="ok")
    assert store.spent_today() == 0

def test_policy_error_spend_fails_closed(tmp_path, policy, monkeypatch):
    import hooks
    store = Store(tmp_path); pre, _, _, _ = create_hooks(Context(policy), store)
    monkeypatch.setattr(hooks, "evaluate", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("broken")))
    assert pre(tool_name="pay_invoice", args={})["action"] == "block"
    assert store.ledger()[-1]["rule"] == "evaluation_error"

def test_bad_policy_json_ignored(tmp_path, policy):
    store = Store(tmp_path); (tmp_path / "policy.json").write_text("[]")
    _, _, _, load = create_hooks(Context(policy), store)
    assert load()["mode"] == "escalate"

def test_policy_json_validates_types(tmp_path, policy):
    store = Store(tmp_path); (tmp_path / "policy.json").write_text('{"daily_max": "huge", "mode": "wild"}')
    _, _, _, load = create_hooks(Context(policy), store)
    assert load()["daily_max"] == 200 and load()["mode"] == "escalate"

def test_json_string_tool_handler(tmp_path, policy):
    import importlib.util
    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "tools.py"
    spec = importlib.util.spec_from_file_location("agentpay_guard_test_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    make_handler = module.make_handler
    handler = make_handler(lambda: policy, Store(tmp_path))
    result = handler({"tool_name": "pay_invoice", "args": {"amount": 5}}, task_id="x")
    parsed = json.loads(result)
    assert parsed["decision"] == "allow" and parsed["amount"] == 5

def test_allowed_spend_counts_only_after_post_call(tmp_path, policy):
    store = Store(tmp_path); pre, post, _, _ = create_hooks(Context(policy), store)
    args = {"amount": 5}
    assert pre(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="allow1") is None
    assert store.spent_today() == 0
    post(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="allow1", result="ok", status="ok")
    assert store.spent_today() == 5

def test_cross_instance_post_consumes_persisted_authorization_once(tmp_path, policy):
    args = {"amount": 7, "payee": "acme"}
    pre_store = Store(tmp_path)
    pre, _, _, _ = create_hooks(Context(policy), pre_store)
    assert pre(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="cross-1") is None
    _, post, _, _ = create_hooks(Context(policy), Store(tmp_path))
    post(tool_name="pay_invoice", args=args, session_id="different-process", tool_call_id="cross-1", status="ok")
    post(tool_name="pay_invoice", args=args, session_id="s", tool_call_id="cross-1", status="ok")
    rows = [row for row in Store(tmp_path).ledger() if row.get("decision") == "executed"]
    assert len(rows) == 1
    assert Store(tmp_path).spent_today() == 7

def test_post_status_must_be_ok_or_missing(tmp_path, policy):
    store = Store(tmp_path)
    pre, post, _, _ = create_hooks(Context(policy), store)
    args = {"amount": 4}
    pre(tool_name="pay_invoice", args=args, tool_call_id="error")
    post(tool_name="pay_invoice", args=args, tool_call_id="error", status="error")
    assert store.spent_today() == 0
    pre(tool_name="pay_invoice", args=args, tool_call_id="missing")
    post(tool_name="pay_invoice", args=args, tool_call_id="missing")
    assert store.spent_today() == 4

def test_queued_ledger_rows_share_queue_id(tmp_path, policy):
    policy["mode"] = "strict"
    store = Store(tmp_path)
    pre, _, _, _ = create_hooks(Context(policy), store)
    result = pre(tool_name="pay_invoice", args={"amount": 25}, tool_call_id="queued")
    queue_id = store.pending()[0]["id"]
    assert result["action"] == "block"
    assert store.ledger()[-1]["id"] == queue_id

def test_every_hard_rule_is_rejected_not_left_pending(tmp_path, policy):
    cases = [
        ("kill_switch", {}, True),
        ("payee_not_allowed", {"payee_allowlist": ["known"]}, False, {"amount": 2, "payee": "unknown"}),
        ("currency_not_allowed", {}, False, {"amount": 2, "currency": "EUR"}),
        ("per_call_max", {"per_call_max": 1}, False),
        ("daily_max", {"daily_max": 1}, False),
    ]
    for case in cases:
        rule, overrides = case[:2]
        kill = case[2]
        args = case[3] if len(case) > 3 else {"amount": 2, "payee": "known"}
        settings = {**policy, **overrides}
        case_store = Store(tmp_path / rule)
        case_store.set_kill(kill)
        pre, _, _, _ = create_hooks(Context(settings), case_store)
        result = pre(tool_name="pay_invoice", args=args)
        item = case_store.queue()[0]
        assert result["action"] == "block"
        assert item["rule"] == rule and item["status"] == "rejected"
        assert case_store.pending() == []
        assert case_store.ledger()[-1]["id"] == item["id"]

def test_block_approval_grant_and_execution_share_queue_id(tmp_path, policy):
    from commands import dispatch
    policy["mode"] = "strict"
    store = Store(tmp_path)
    pre, post, _, load = create_hooks(Context(policy), store)
    args = {"amount": 25, "payee": "acme"}
    pre(tool_name="pay_invoice", args=args, tool_call_id="approval-id")
    queue_id = store.pending()[0]["id"]
    dispatch(f"approve {queue_id}", store, load)
    assert pre(tool_name="pay_invoice", args=args, tool_call_id="approved-id") is None
    post(tool_name="pay_invoice", args=args, tool_call_id="approved-id", status="ok")
    linked = [row for row in store.ledger() if row.get("decision") in {"blocked", "approved", "granted", "executed"}]
    assert {row["id"] for row in linked} == {queue_id}
    assert all(row.get("ref") == queue_id for row in linked)
