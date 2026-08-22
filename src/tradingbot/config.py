from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.toml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as f:
        return tomllib.load(f)


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def market_initial_cash(config: dict[str, Any], market: str) -> float:
    key = f"initial_cash_{market.lower()}"
    return float(config.get("backtest", {}).get(key, 0))


def market_commission_rate(config: dict[str, Any], market: str) -> float:
    return float(config.get("fees", {}).get(market.upper(), {}).get("commission_rate", 0))
