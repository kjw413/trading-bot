from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1


class StrategyStateStore(Protocol):
    """Persistence surface for strategy-internal state.

    Stores plain-JSON-serializable dicts keyed by strategy name so a process
    restart can restore counters such as holding days, last rebalance date,
    and processed signal ids.
    """

    def load(self, strategy_name: str) -> dict[str, Any]:
        ...

    def save(self, strategy_name: str, state: dict[str, Any]) -> None:
        ...


class MemoryStateStore:
    """In-memory store for backtests and tests. Nothing survives the process."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}

    def load(self, strategy_name: str) -> dict[str, Any]:
        return dict(self._states.get(strategy_name, {}))

    def save(self, strategy_name: str, state: dict[str, Any]) -> None:
        self._states[strategy_name] = dict(state)


class JsonStateStore:
    """File-backed store: one JSON file holding every strategy's state.

    A corrupted state file raises instead of silently resetting — silently
    starting from empty state could re-enter positions or double-order.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, strategy_name: str) -> dict[str, Any]:
        data = self._read_all()
        payload = data.get(strategy_name)
        if payload is None:
            return {}
        return dict(payload.get("state", {}))

    def save(self, strategy_name: str, state: dict[str, Any]) -> None:
        data = self._read_all()
        data[strategy_name] = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Strategy state file is corrupted: {self.path}. "
                "Refusing to silently reset state; inspect or remove the file explicitly."
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"Strategy state file has unexpected format: {self.path}")
        return data
