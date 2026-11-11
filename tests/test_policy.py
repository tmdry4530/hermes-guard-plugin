import pytest
from policy import classify, evaluate

@pytest.mark.parametrize("name", ["display_image", "reorder_list", "border_style", "read_file", "web_search", "todo"])
def test_harmless_names_not_spend(name):
    assert not classify(name, {}).spending

@pytest.mark.parametrize("name", ["pay_invoice", "stripe_create_payment", "mcp__shop__checkout", "buyTicket", "place_order"])
def test_spend_tokens(name):
    assert classify(name, {}).spending

def test_globs_are_additional(policy):
    assert classify("billing_run", {}, ["billing_*"]).spending
    assert not classify("display_image", {}, []).spending

@pytest.mark.parametrize("key", ["amount", "amount_usd", "amount_cents"])
def test_amount_keys_make_other_tool_spend(key):
    assert classify("unit_convert", {key: 5}).spending

def test_currency_sibling_allows_price():
    assert classify("unit_convert", {"price": 5, "currency": "USD"}).spending

def test_value_without_currency_not_spending():
    assert not classify("unit_convert", {"value": 5}).spending

def test_total_without_currency_not_spending():
    assert not classify("calculator", {"total": 5}).spending

def test_spend_name_without_amount_escalates(policy):
    result = evaluate("pay_invoice", {}, policy)
    assert (result["decision"], result["rule"]) == ("approve", "amount_unrecognized")

def test_spend_name_without_amount_strict_blocks(policy):
    policy["mode"] = "strict"
    assert evaluate("pay_invoice", {}, policy)["decision"] == "block"

def test_money_amount_forms():
    assert classify("pay", {"amount": "USD 3.50"}).amount == 3.5
    assert classify("pay", {"amount_cents": 1250}).amount == 12.5
    assert classify("pay", {"amount": {"value": 4, "currency": "EUR"}}).currency == "EUR"

def test_per_call_hard_limit(policy):
    assert evaluate("pay", {"amount": 51}, policy)["rule"] == "per_call_max"

def test_daily_hard_limit(policy):
    assert evaluate("pay", {"amount": 10}, policy, 195)["rule"] == "daily_max"

def test_kill_switch_hard_limit(policy):
    assert evaluate("pay", {"amount": 1}, policy, kill=True)["rule"] == "kill_switch"

def test_payee_hard_limit(policy):
    policy["payee_allowlist"] = ["acme"]
    assert evaluate("pay", {"amount": 1, "to": "other"}, policy)["rule"] == "payee_not_allowed"

def test_currency_hard_limit(policy):
    assert evaluate("pay", {"amount": 1, "currency": "EUR"}, policy)["rule"] == "currency_not_allowed"

def test_threshold_escalates(policy):
    assert evaluate("pay", {"amount": 21}, policy)["decision"] == "approve"

def test_threshold_strict_blocks(policy):
    policy["mode"] = "strict"
    assert evaluate("pay", {"amount": 21}, policy)["rule"] == "approval_required_strict"

@pytest.mark.parametrize("name", ["delete_file", "remove_user", "drop_table", "wipe_disk", "send_email"])
def test_risky_tokens(name, policy):
    assert evaluate(name, {}, policy)["rule"] == "risky_tool"

def test_command_risk(policy):
    assert evaluate("terminal", {"command": "rm -rf /tmp/x"}, policy)["decision"] == "approve"

def test_risky_strict_is_still_queueable_block(policy):
    policy["mode"] = "strict"
    assert evaluate("delete_file", {}, policy)["decision"] == "approve"
