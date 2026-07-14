from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor

TRADING_DAYS_PER_MONTH = 21


class MomentumFactor(Factor):
    """Price momentum: total return over the past `months` months.

    `skip_months` excludes the most recent months from the window
    (e.g. months=12, skip_months=1 is the classic 12-1 momentum that avoids
    short-term reversal). Windows are measured in trading days
    (21 per month), using close prices as of the computation date.
    """

    def __init__(self, months: int, skip_months: int = 0) -> None:
        if months <= 0:
            raise ValueError("months must be positive")
        if skip_months < 0:
            raise ValueError("skip_months cannot be negative")
        self.months = months
        self.skip_months = skip_months
        suffix = f"_ex{skip_months}m" if skip_months else ""
        self.name = f"momentum_{months}m{suffix}"

    def compute(self, dt: date, universe: Sequence[str], data_store: PriceDataStore) -> pd.Series:
        window_days = self.months * TRADING_DAYS_PER_MONTH
        skip_days = self.skip_months * TRADING_DAYS_PER_MONTH
        lookback = window_days + skip_days + 1

        values = self._empty(universe)
        for symbol in values.index:
            try:
                history = data_store.price_history(symbol, dt, lookback)
            except (FileNotFoundError, KeyError):
                continue
            closes = history["close"].dropna()
            if len(closes) < lookback:
                continue
            end_price = float(closes.iloc[-1 - skip_days])
            start_price = float(closes.iloc[-lookback])
            if start_price <= 0:
                continue
            values.loc[symbol] = end_price / start_price - 1.0
        return values
