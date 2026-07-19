from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.dates import month_end_trading_days
from tradingbot.research.ic import ic_series, summarize_ic


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def _add_years(day: date, years: int) -> date:
    return date(day.year + years, day.month, day.day)


def walk_forward_windows(
    start: date, end: date, *, train_years: int, test_years: int, step_years: int
) -> list[WalkForwardWindow]:
    """Rolling windows: train `train_years`, test `test_years`, advance by
    `step_years`. The last window's test end is capped at `end`; a window
    whose test period would start after `end` is dropped."""
    if min(train_years, test_years, step_years) <= 0:
        raise ValueError("train_years, test_years, and step_years must be positive")
    if start.month == 2 and start.day == 29:
        raise ValueError("start must not be Feb 29")
    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        train_end = _add_years(train_start, train_years) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = _add_years(test_start, test_years) - timedelta(days=1)
        if test_start > end:
            break
        windows.append(WalkForwardWindow(train_start, train_end, test_start, min(test_end, end)))
        train_start = _add_years(train_start, step_years)
    return windows


def walk_forward_ic(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    *,
    market: str,
    horizon_days: int,
    windows: Sequence[WalkForwardWindow],
) -> pd.DataFrame:
    """Test-segment IC summary per window (month-end evaluation dates)."""
    rows = []
    for window in windows:
        dates = month_end_trading_days(market, window.test_start, window.test_end)
        summary = summarize_ic(ic_series(factor, store, universe, dates, horizon_days))
        rows.append(
            {
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "test_ic_mean": summary.mean,
                "test_ic_ir": summary.ir,
                "n_periods": summary.n_periods,
            }
        )
    return pd.DataFrame(rows)


def window_win_rate(results: pd.DataFrame) -> float:
    """Share of windows with positive test IC mean; NaN when empty."""
    if results.empty:
        return float("nan")
    return float((results["test_ic_mean"] > 0).mean())
