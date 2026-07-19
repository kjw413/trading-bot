from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.labels import forward_returns


def spearman_ic(factor_values: pd.Series, forward: pd.Series) -> float:
    """Spearman rank correlation between factor scores and forward returns.

    NaN pairs are dropped; fewer than 3 remaining pairs or a constant column
    yields NaN (correlation undefined — not zero)."""
    frame = pd.concat([factor_values, forward], axis=1, join="inner").dropna()
    if len(frame) < 3:
        return float("nan")
    scores, returns = frame.iloc[:, 0], frame.iloc[:, 1]
    if scores.nunique() < 2 or returns.nunique() < 2:
        return float("nan")
    return float(frame.corr(method="spearman").iloc[0, 1])


def ic_series(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    horizon_days: int,
) -> pd.Series:
    """Per-date cross-sectional IC, indexed by date."""
    values = {
        pd.Timestamp(dt): spearman_ic(
            factor.compute(dt, universe, store),
            forward_returns(store, universe, dt, horizon_days),
        )
        for dt in dates
    }
    return pd.Series(values, name=f"ic_{factor.name}_{horizon_days}d", dtype=float)


@dataclass(frozen=True)
class ICSummary:
    mean: float
    std: float
    ir: float
    positive_share: float
    n_periods: int


def summarize_ic(ics: pd.Series) -> ICSummary:
    clean = ics.dropna()
    n = len(clean)
    if n == 0:
        nan = float("nan")
        return ICSummary(nan, nan, nan, nan, 0)
    mean = float(clean.mean())
    std = float(clean.std(ddof=1)) if n > 1 else float("nan")
    ir = mean / std if std and std > 0 else float("nan")
    return ICSummary(mean, std, ir, float((clean > 0).mean()), n)
