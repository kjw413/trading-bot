from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore


def forward_return(closes: pd.Series, dt: date, horizon_days: int) -> float:
    """Return from the last close at/before `dt` to the close `horizon_days`
    trading rows later. NaN when the base or target close is unavailable."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    clean = closes.dropna()
    if clean.empty:
        return float("nan")
    base_count = int((clean.index <= pd.Timestamp(dt)).sum())
    if base_count == 0:
        return float("nan")
    base_idx = base_count - 1
    target_idx = base_idx + horizon_days
    if target_idx >= len(clean):
        return float("nan")
    base = float(clean.iloc[base_idx])
    if base <= 0:
        return float("nan")
    return float(clean.iloc[target_idx]) / base - 1.0


def forward_returns(
    store: ResearchDataStore, universe: Sequence[str], dt: date, horizon_days: int
) -> pd.Series:
    """Forward returns indexed by upper-cased symbol; missing symbols get NaN."""
    values = pd.Series(
        [float("nan")] * len(universe),
        index=[symbol.upper() for symbol in universe],
        name=f"fwd_{horizon_days}d",
        dtype=float,
    )
    for symbol in values.index:
        try:
            closes = store.close_series(symbol)
        except (FileNotFoundError, KeyError):
            continue
        values.loc[symbol] = forward_return(closes, dt, horizon_days)
    return values


def excess_forward_returns(
    store: ResearchDataStore,
    universe: Sequence[str],
    dt: date,
    horizon_days: int,
    benchmark: str,
) -> pd.Series:
    """Forward returns minus the benchmark's forward return.

    A missing benchmark series is a configuration error and raises."""
    benchmark_return = forward_return(store.close_series(benchmark), dt, horizon_days)
    values = forward_returns(store, universe, dt, horizon_days) - benchmark_return
    values.name = f"excess_{horizon_days}d"
    return values
