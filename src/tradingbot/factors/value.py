"""Valuation factors from KRX's published daily ratios.

Ratios are inverted (1/PER, 1/PBR) for two reasons: the inverted form points
the same way as every other factor here — higher is better — and it stays
bounded as earnings approach zero, where PER itself explodes and would
dominate any cross-sectional ranking.

Non-positive ratios yield NaN rather than a negative score: a loss-making
company has no meaningful earnings yield, and ranking it between two
profitable companies would be worse than not ranking it at all.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor


class _InverseRatioFactor(Factor):
    """Shared shape for 1/ratio valuation factors."""

    dataset = "valuation"

    def __init__(self, name: str, column: str) -> None:
        self.name = name
        self.column = column

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values

        latest = data_store.panel_latest(self.dataset, dt, list(values.index), self.column)
        for symbol, ratio in latest.items():
            if pd.isna(ratio) or ratio <= 0:
                continue
            values.loc[symbol] = 1.0 / float(ratio)
        return values


class EarningsYieldFactor(_InverseRatioFactor):
    """1/PER — higher means cheaper relative to earnings."""

    def __init__(self) -> None:
        super().__init__("earnings_yield", "per")


class BookToMarketFactor(_InverseRatioFactor):
    """1/PBR — higher means cheaper relative to book value."""

    def __init__(self) -> None:
        super().__init__("book_to_market", "pbr")
