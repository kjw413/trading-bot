"""Turn a selected list of names into portfolio weights.

Inverse-volatility weighting sizes positions so each contributes similar
risk — a theme's calmest name gets more capital than its wildest. A symbol
whose volatility cannot be measured is excluded rather than guessed; if no
symbol can be measured the whole basket falls back to equal weight, because
an empty rebalance is a worse failure than an unsophisticated one.
"""

from __future__ import annotations

import math
from typing import Sequence

import pandas as pd


def equal_weights(symbols: Sequence[str]) -> dict[str, float]:
    if not symbols:
        return {}
    share = 1.0 / len(symbols)
    return {str(symbol): share for symbol in symbols}


def realized_volatility(closes: pd.Series, days: int) -> float:
    """Standard deviation of daily returns over the trailing `days` returns."""
    if days <= 0:
        raise ValueError("days must be positive")
    returns = closes.dropna().pct_change().dropna().tail(days)
    if len(returns) < days:
        return float("nan")
    return float(returns.std(ddof=0))


def inverse_volatility_weights(volatilities: dict[str, float]) -> dict[str, float]:
    """1/sigma weights, normalized. Unmeasurable symbols are excluded."""
    if not volatilities:
        return {}
    inverses = {
        symbol: 1.0 / vol
        for symbol, vol in volatilities.items()
        if not math.isnan(vol) and vol > 0
    }
    if not inverses:
        return equal_weights(list(volatilities))
    total = sum(inverses.values())
    return {symbol: value / total for symbol, value in inverses.items()}


def scale_weights(weights: dict[str, float], factor: float) -> dict[str, float]:
    """Scale every weight by `factor` (e.g. regime-based exposure)."""
    if factor < 0:
        raise ValueError("factor must be non-negative")
    return {symbol: weight * factor for symbol, weight in weights.items()}
