from __future__ import annotations

import math
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.labels import forward_returns


def quantile_assignments(factor_values: pd.Series, n_quantiles: int) -> pd.Series:
    """Assign 1 (lowest score) .. n_quantiles (highest). NaN scores excluded.

    Fewer valid scores than quantiles yields an empty assignment."""
    if n_quantiles < 2:
        raise ValueError("n_quantiles must be at least 2")
    clean = factor_values.dropna()
    if len(clean) < n_quantiles:
        return pd.Series(dtype=int)
    ranks = clean.rank(method="first")
    return pd.qcut(ranks, n_quantiles, labels=False).astype(int) + 1


def quantile_returns(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    horizon_days: int,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Equal-weight mean forward return per quantile, per date.

    Columns: q1..qN and 'spread' (top minus bottom). Dates without enough
    scored symbols are skipped."""
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for dt in dates:
        scores = factor.compute(dt, universe, store)
        assignments = quantile_assignments(scores, n_quantiles)
        if assignments.empty:
            continue
        forwards = forward_returns(store, list(assignments.index), dt, horizon_days)
        row: dict[str, float] = {}
        for quantile in range(1, n_quantiles + 1):
            members = assignments.index[assignments == quantile]
            row[f"q{quantile}"] = float(forwards.loc[members].mean())
        row["spread"] = row[f"q{n_quantiles}"] - row["q1"]
        rows[pd.Timestamp(dt)] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def monotonicity(quantile_means: Sequence[float]) -> float:
    """Share of adjacent quantile pairs whose mean return strictly increases."""
    pairs = [
        (low, high)
        for low, high in zip(quantile_means, quantile_means[1:])
        if not (math.isnan(low) or math.isnan(high))
    ]
    if not pairs:
        return float("nan")
    return sum(1 for low, high in pairs if high > low) / len(pairs)


def top_quantile_turnover(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    n_quantiles: int = 5,
) -> pd.Series:
    """1 - overlap of the top-quantile set with the previous date's set."""
    previous: set[str] | None = None
    values: dict[pd.Timestamp, float] = {}
    for dt in dates:
        assignments = quantile_assignments(factor.compute(dt, universe, store), n_quantiles)
        if assignments.empty:
            continue
        top = set(assignments.index[assignments == n_quantiles])
        if previous:
            values[pd.Timestamp(dt)] = 1.0 - len(top & previous) / len(previous)
        previous = top
    return pd.Series(values, name=f"turnover_{factor.name}", dtype=float)
