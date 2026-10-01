"""Turn a selected list of names into portfolio weights.

Inverse-volatility weighting sizes positions so each contributes similar
risk — a theme's calmest name gets more capital than its wildest. A symbol
whose volatility cannot be measured is excluded rather than guessed; if no
symbol can be measured the whole basket falls back to equal weight, because
an empty rebalance is a worse failure than an unsophisticated one.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

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


def tilt_weights(
    base: dict[str, float], scores: Mapping[str, float], strength: float
) -> dict[str, float]:
    """Exponentially tilt base weights by standardized cross-sectional scores.

    Scores are clipped to +/-3 before applying the tilt. Zero base weights
    stay zero, which lets an eligibility filter express an exclusion without
    removing the symbol from the target map. At strength zero the input is
    returned exactly, without a normalization round trip.
    """
    if not math.isfinite(strength) or strength < 0:
        raise ValueError("strength must be a finite non-negative number")
    if not base:
        return {}
    if strength == 0:
        return dict(base)

    tilted: dict[str, float] = {}
    for symbol, weight in base.items():
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("base weights must be finite and non-negative")
        if weight == 0:
            tilted[symbol] = 0.0
            continue
        score = float(scores.get(symbol, float("nan")))
        if not math.isfinite(score):
            tilted[symbol] = 0.0
            continue
        clipped = max(-3.0, min(3.0, score))
        tilted[symbol] = weight * math.exp(strength * clipped)

    total = sum(tilted.values())
    if total <= 0:
        return {}
    return {symbol: weight / total for symbol, weight in tilted.items()}


def realized_portfolio_volatility(
    closes: Mapping[str, pd.Series], weights: Mapping[str, float], days: int
) -> float:
    """Annualized trailing volatility of a fixed-weight portfolio."""
    if days <= 0:
        raise ValueError("days must be positive")

    active = {
        symbol: float(weight)
        for symbol, weight in weights.items()
        if math.isfinite(float(weight)) and float(weight) > 0
    }
    if not active:
        return float("nan")

    returns: dict[str, pd.Series] = {}
    for symbol in active:
        series = closes.get(symbol)
        if series is None:
            return float("nan")
        trailing = series.dropna().pct_change(fill_method=None).dropna().tail(days)
        if len(trailing) < days:
            return float("nan")
        returns[symbol] = trailing

    aligned = pd.concat(returns, axis=1, join="inner").dropna().tail(days)
    if len(aligned) < days:
        return float("nan")
    portfolio_returns = aligned.mul(pd.Series(active), axis="columns").sum(axis=1)
    return float(portfolio_returns.std(ddof=0) * math.sqrt(252.0))


def volatility_target_exposure(realized_vol: float, target_vol: float) -> float:
    """Scale exposure toward an annualized target without adding leverage."""
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise ValueError("target_vol must be a finite positive number")
    if math.isnan(realized_vol):
        return 1.0
    if not math.isfinite(realized_vol) or realized_vol < 0:
        raise ValueError("realized_vol must be finite and non-negative")
    if realized_vol == 0:
        return 1.0
    return min(1.0, target_vol / realized_vol)


def scale_weights(weights: dict[str, float], factor: float) -> dict[str, float]:
    """Scale every weight by `factor` (e.g. regime-based exposure)."""
    if factor < 0:
        raise ValueError("factor must be non-negative")
    return {symbol: weight * factor for symbol, weight in weights.items()}
