import json
import os
import shutil
import sys
from pathlib import Path
import pytest

SOURCE = Path("/workspace/hermes-agent-src")
if SOURCE.exists():
    sys.path.insert(0, str(SOURCE))

@pytest.fixture
def loaded_plugin(tmp_path, monkeypatch):
    if not SOURCE.exists():
        pytest.skip("Hermes source unavailable")
    try:
        import hermes_cli.plugins as plugins
        import hermes_constants
        if str(SOURCE) in sys.path:
            sys.path.remove(str(SOURCE))
        sys.path.insert(0, str(SOURCE))
        if "tools" in sys.modules and not hasattr(sys.modules["tools"], "__path__"):
            del sys.modules["tools"]
        from tools.registry import registry
    except Exception as exc:
        pytest.skip(f"Hermes not importable: {exc}")
    home = tmp_path / "home"
    dest = home / "plugins" / "agentpay-guard"
    dest.parent.mkdir(parents=True)
    shutil.copytree(Path(__file__).resolve().parents[1], dest, ignore=shutil.ignore_patterns(".git", "evidence", "tests", "__pycache__", ".pytest_cache"))
    (home / "config.yaml").write_text("plugins:\n  enabled: [agentpay-guard]\n  entries:\n    agentpay-guard:\n      enabled: true\n      settings:\n        mode: strict\n        per_call_max: 50\n        daily_max: 200\n        approval_threshold: 20\n        currency: [USD]\n", encoding="utf-8")
    token = hermes_constants.set_hermes_home_override(home)
    manager = plugins.PluginManager(scope_key=str(home))
    manager.discover_and_load()
    yield plugins, manager, registry, home
    manager.unload()
    hermes_constants.reset_hermes_home_override(token)

def test_real_dispatch_block_and_allow(loaded_plugin):
    plugins, manager, _, _ = loaded_plugin
    blocked = plugins._get_pre_tool_call_directive_details("pay_invoice", {"amount": 25}, session_id="integration")
    allowed = plugins._get_pre_tool_call_directive_details("read_file", {}, session_id="integration")
    assert blocked.action == "block"
    assert allowed.action is None

def test_real_tool_registry_handler_json(loaded_plugin):
    _, _, registry, _ = loaded_plugin
    entry = registry.get_entry("guard_check")
    assert entry is not None
    response = entry.handler({"tool_name": "pay_invoice", "args": {"amount": 5}}, task_id="x")
    assert isinstance(response, str) and json.loads(response)["decision"] == "allow"
