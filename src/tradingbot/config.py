from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.toml"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines. Hand-rolled to avoid a dependency.

    Only a line beginning with `#` is a comment: splitting on an inline one
    would truncate any credential containing a hash. Values keep everything
    after the first `=`, so tokens and passwords survive intact.

    Empty values are dropped. `.env.template` ships its keys with no value,
    and loading those would shadow a variable already set in the environment
    with an empty string.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        name, _, raw = stripped.partition("=")
        name = name.strip()
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and value:
            values[name] = value
    return values


def load_dotenv(path: str | Path | None = None) -> list[str]:
    """Load `.env` into the environment; returns the names it set.

    An existing environment variable always wins, so a one-off
    `$env:X = ...` in the shell can still override the file.

    Names only, never values — the caller logs this and these are
    credentials. A missing or unreadable file is not an error: the file is
    optional, and a locked one must not stop the CLI from starting.
    """
    # Resolved on each call rather than at import, so PROJECT_ROOT stays the
    # single source of truth for where the project lives.
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return []

    loaded = []
    for name, value in parse_dotenv(text).items():
        if os.environ.get(name):
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


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
