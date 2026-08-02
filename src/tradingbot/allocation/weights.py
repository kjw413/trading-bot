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


def rank_tilt_weights(scores: pd.Series, strength: float) -> dict[str, float]:
    """Hold every scoreable name, tilting weight toward the better-ranked ones.

    Selecting the top few names throws away the diversification the universe
    was giving for free, and only pays for that if the signal is strong. The
    US review measured what happens when it isn't: the strategy trailed an
    equal-weight basket of its own universe by ~3%p a year. A tilt keeps the
    basket and adds the signal on top, so a weak signal costs little instead
    of costing the diversification too.

    `strength` interpolates: 0.0 is equal weight (the benchmark, signal
    ignored), 1.0 puts the best name at twice equal weight and the worst at
    zero. Ranks are used rather than raw scores so one extreme value cannot
    take over the portfolio — the same reason the factor layer winsorizes.

    NaN scores are unscoreable names and are excluded, matching `select_top`.
    """
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be in [0, 1]")
    clean = scores.dropna()
    if clean.empty:
        return {}

    n = len(clean)
    if n == 1:
        return {str(clean.index[0]): 1.0}

    # Ranks scaled to [-1, +1]: worst -> -1, best -> +1, ties share a rank.
    # `average` keeps two tied names on the same weight instead of letting
    # symbol order decide which one gets more capital.
    ranks = clean.rank(method="average", ascending=True)
    centred = (ranks - 1.0) / (n - 1.0) * 2.0 - 1.0

    base = 1.0 / n
    weights = base * (1.0 + strength * centred)
    weights = weights.clip(lower=0.0)
    total = float(weights.sum())
    if total <= 0:
        return equal_weights([str(symbol) for symbol in clean.index])
    return {str(symbol): float(weight) / total for symbol, weight in weights.items()}
