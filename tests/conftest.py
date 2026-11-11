import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

@pytest.fixture
def policy():
    return {"per_call_max": 50, "daily_max": 200, "approval_threshold": 20, "currency": ["USD"],
            "payee_allowlist": [], "spend_tool_patterns": [], "risky_tool_patterns": [],
            "require_approval_tools": [], "mode": "escalate", "grant_ttl_minutes": 30}

class Context:
    def __init__(self, values=None):
        self.values = values or {}
    def get_config(self, key, default):
        return self.values.get(key, default)
