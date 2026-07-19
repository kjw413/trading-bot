from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradingbot.config import PROJECT_ROOT
from tradingbot.research.ic import ICSummary

RESEARCH_CONFIG_PATH = PROJECT_ROOT / "config" / "research.toml"


def load_research_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else RESEARCH_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Research config not found: {config_path}")
    with config_path.open("rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class GateThresholds:
    horizon_days: int
    n_quantiles: int
    min_ic_mean: float
    min_ic_ir: float
    min_monotonicity: float


def load_gate_thresholds(research_config: dict[str, Any]) -> GateThresholds:
    section = research_config["factor_gate"]
    return GateThresholds(
        horizon_days=int(section["horizon_days"]),
        n_quantiles=int(section["n_quantiles"]),
        min_ic_mean=float(section["min_ic_mean"]),
        min_ic_ir=float(section["min_ic_ir"]),
        min_monotonicity=float(section["min_monotonicity"]),
    )


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]


def evaluate_gate(ic: ICSummary, monotonicity: float, thresholds: GateThresholds) -> GateResult:
    """Check a factor against the acceptance gate.

    NaN metrics fail their check (comparison with NaN is False), so factors
    with insufficient data are rejected loudly rather than passed silently."""
    reasons: list[str] = []
    if not ic.mean >= thresholds.min_ic_mean:
        reasons.append(f"ic_mean {ic.mean:.4f} < {thresholds.min_ic_mean}")
    if not ic.ir >= thresholds.min_ic_ir:
        reasons.append(f"ic_ir {ic.ir:.4f} < {thresholds.min_ic_ir}")
    if not monotonicity >= thresholds.min_monotonicity:
        reasons.append(f"monotonicity {monotonicity:.4f} < {thresholds.min_monotonicity}")
    return GateResult(passed=not reasons, reasons=reasons)
