"""Investor-flow factors.

Raw net-buy amounts are dominated by company size — a large cap absorbs more
money on a quiet day than a small cap does on a frantic one. Dividing by the
traded value over the same window makes the number comparable across the
universe: "how much of this stock's turnover was this investor group buying".
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor

INVESTOR_COLUMNS = {
    "foreign": "foreign_net",
    "institution": "institution_net",
    "individual": "individual_net",
}

# Calendar days to span a given number of trading days, with slack for
# holidays. Over-fetching is harmless — the window is trimmed by row count.
_CALENDAR_SLACK = 2.0


class NetBuyIntensityFactor(Factor):
    """Cumulative net buying over `days`, scaled by traded value.

    Positive means the investor group was a net buyer relative to how much of
    the stock changed hands.
    """

    def __init__(self, investor: str, days: int) -> None:
        if investor not in INVESTOR_COLUMNS:
            available = ", ".join(sorted(INVESTOR_COLUMNS))
            raise ValueError(f"Unknown investor: {investor}. Available: {available}")
        if days <= 0:
            raise ValueError("days must be positive")
        self.investor = investor
        self.days = days
        self.column = INVESTOR_COLUMNS[investor]
        self.name = f"{investor}_net_{days}d"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values

        start = dt - timedelta(days=int(self.days * _CALENDAR_SLACK) + 7)
        flows = data_store.panel("flows", dt, list(values.index), start=start)
        if flows.empty:
            return values

        for symbol in values.index:
            rows = flows[flows["symbol"] == symbol].sort_values("date").tail(self.days)
            if rows.empty:
                continue
            try:
                prices = data_store.price_history(symbol, dt, self.days)
            except (FileNotFoundError, KeyError):
                continue
            traded_value = float((prices["close"] * prices["volume"]).sum())
            if traded_value <= 0:
                continue
            values.loc[symbol] = float(rows[self.column].sum()) / traded_value
        return values
