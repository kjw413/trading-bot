from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor

TREND_CONSISTENCY_DAYS = 126
TREND_RETURN_DAYS = 20


class TrendConsistencyFactor(Factor):
    """Share of the last 126 trading days with a positive 20-day trend."""

    name = "trend_consistency_6m"

    def compute(self, dt: date, universe: Sequence[str], data_store: PriceDataStore) -> pd.Series:
        # Producing 126 trailing 20-day returns requires 126 + 20 closes.
        lookback = TREND_CONSISTENCY_DAYS + TREND_RETURN_DAYS
        values = self._empty(universe)
        for symbol in values.index:
            try:
                history = data_store.price_history(symbol, dt, lookback)
            except (FileNotFoundError, KeyError):
                continue
            closes = history["close"].dropna()
            if len(closes) < lookback:
                continue
            trend_returns = closes.pct_change(periods=TREND_RETURN_DAYS).dropna()
            if len(trend_returns) < TREND_CONSISTENCY_DAYS:
                continue
            values.loc[symbol] = float((trend_returns.tail(TREND_CONSISTENCY_DAYS) > 0).mean())
        return values
