"""Measure a strategy against the promotion criteria.

Three of the six criteria — walk-forward win rate, annual turnover, and
cost-doubling sensitivity — had no harness at all, so no strategy could
earn a pass. This module supplies them by composing the existing backtest
engine rather than adding new simulation logic.

Nothing here searches for better parameters. It measures; a human decides
what to change. A search loop bolted onto a measurement tool is automated
overfitting.
"""

from __future__ import annotations

import copy
from typing import Any


def scale_costs(config: dict[str, Any], market: str, multiplier: float) -> dict[str, Any]:
    """Return a copy of `config` with this market's trading costs multiplied.

    Scales every numeric entry under `[fees.<market>]` plus
    `[execution] slippage_bps`. The FINRA TAF cap scales with its rate on
    purpose: leaving the cap fixed would let it absorb the increase and
    neuter the sensitivity test.

    The input config is never mutated — callers run the same config at 1x
    and 2x and must not have the first run contaminate the second.
    """
    if multiplier < 0:
        raise ValueError("multiplier must be non-negative")

    scaled = copy.deepcopy(config)
    market_fees = scaled.get("fees", {}).get(market.upper())
    if market_fees:
        for name, value in market_fees.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                market_fees[name] = value * multiplier

    execution = scaled.setdefault("execution", {})
    slippage = execution.get("slippage_bps")
    if isinstance(slippage, (int, float)) and not isinstance(slippage, bool):
        execution["slippage_bps"] = slippage * multiplier
    return scaled
