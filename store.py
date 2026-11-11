"""Thread-safe atomic JSON storage for guard decisions and grants."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


def args_hash(tool: str, args: Any) -> str:
    """Hash canonical tool arguments without persisting raw arguments."""
    raw = json.dumps([tool, args], sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Store:
    """Persist queue, ledger, kill switch, and single-use grants."""

    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "ledger.json"
        self.queue_path = self.root / "queue.json"
        self.kill_path = self.root / "kill.json"
        self.lock = threading.RLock()

    def _read(self, path: Path, default: Any) -> Any:
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return default
        return value if isinstance(value, type(default)) else default

    def _write(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def ledger(self) -> list[dict]:
        with self.lock:
            return self._read(self.ledger_path, [])

    def queue(self) -> list[dict]:
        with self.lock:
            return self._read(self.queue_path, [])

    def kill(self) -> bool:
        with self.lock:
            return bool(self._read(self.kill_path, {"on": False}).get("on"))

    def set_kill(self, enabled: bool) -> None:
        with self.lock:
            self._write(self.kill_path, {"on": bool(enabled)})

    def pending(self) -> list[dict]:
        return [item for item in self.queue() if item.get("status") == "pending"]

    def enqueue(self, tool: str, args: Any, intent: Any, rule: str, session: str = "") -> dict:
        digest = args_hash(tool, args)
        with self.lock:
            rows = self.queue()
            for item in rows:
                if item.get("status") == "pending" and item.get("args_hash") == digest:
                    return item
            item = {"id": uuid.uuid4().hex[:6], "ts": now().isoformat(), "tool": tool,
                    "payee": intent.payee, "amount": intent.amount, "currency": intent.currency,
                    "rule": rule, "session_id": session, "args_hash": digest, "status": "pending"}
            rows.append(item)
            self._write(self.queue_path, rows[-500:])
            return item

    def find(self, ident: str) -> dict | None:
        return next((item for item in self.queue() if item.get("id") == ident), None)

    def set_status(self, ident: str, status: str, decided_by: str = "") -> dict | None:
        with self.lock:
            rows = self.queue()
            for item in rows:
                if item.get("id") == ident and item.get("status") == "pending":
                    item["status"] = status
                    item["decided_at"] = now().isoformat()
                    if decided_by:
                        item["decided_by"] = decided_by
                    self._write(self.queue_path, rows[-500:])
                    return item
        return None

    def add_grant(self, item: dict, ttl_minutes: int = 30) -> bool:
        with self.lock:
            rows = self.queue()
            for row in rows:
                if row.get("id") == item.get("id"):
                    row["grant"] = {"args_hash": row["args_hash"], "expires_at": (now() + timedelta(minutes=ttl_minutes)).isoformat(), "used": False}
                    self._write(self.queue_path, rows[-500:])
                    return True
        return False

    def consume_grant(self, tool: str, args: Any, policy: dict, spent_today: float) -> bool:
        try:
            from .policy import evaluate
        except ImportError:
            from policy import evaluate
        digest = args_hash(tool, args)
        current = now()
        with self.lock:
            rows = self.queue()
            for item in rows:
                grant = item.get("grant") or {}
                if item.get("status") != "approved" or item.get("tool") != tool or item.get("args_hash") != digest or grant.get("used"):
                    continue
                try:
                    if datetime.fromisoformat(grant["expires_at"]) < current:
                        continue
                except (KeyError, ValueError):
                    continue
                hard_policy = {**policy, "approval_threshold": float("inf"), "require_approval_tools": [], "mode": "escalate"}
                verdict = evaluate(tool, args, hard_policy, spent_today, kill=self.kill())
                if verdict["rule"] in {"kill_switch", "payee_not_allowed", "currency_not_allowed", "per_call_max", "daily_max"}:
                    return False
                grant["used"] = True
                self._write(self.queue_path, rows[-500:])
                return True
        return False

    def append(self, entry: dict) -> None:
        with self.lock:
            rows = self._read(self.ledger_path, [])
            rows.append(entry)
            self._write(self.ledger_path, rows[-5000:])

    def spent_today(self, currency: str = "USD") -> float:
        today = now().date().isoformat()
        return sum(float(row.get("amount") or 0) for row in self.ledger()
                   if row.get("decision") == "executed" and str(row.get("ts", ""))[:10] == today
                   and str(row.get("currency", "USD")).upper() == currency.upper())
