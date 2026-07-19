from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors.base import Factor


@pytest.fixture
def us_store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path), "US")


@pytest.fixture
def write_prices():
    """Write a synthetic business-day OHLCV series to a cache."""

    def _write(
        cache: ParquetCache,
        market: str,
        symbol: str,
        closes: list[float],
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> None:
        if (start is None) == (end is None):
            raise ValueError("pass exactly one of start/end")
        if start is not None:
            index = pd.bdate_range(start=pd.Timestamp(start), periods=len(closes))
        else:
            index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
        frame = pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0] * len(closes),
            },
            index=index,
        )
        cache.write(market, symbol, frame)

    return _write


class FixedFactor(Factor):
    """Deterministic factor for tests: fixed score per symbol."""

    def __init__(self, scores: dict[str, float], name: str = "fixed") -> None:
        self.scores = scores
        self.name = name

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol in values.index:
            if symbol in self.scores:
                values.loc[symbol] = self.scores[symbol]
        return values


class ScheduledFactor(Factor):
    """Different fixed scores per date, for turnover tests."""

    name = "scheduled"

    def __init__(self, by_date: dict[date, dict[str, float]]) -> None:
        self.by_date = by_date

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol, score in self.by_date.get(dt, {}).items():
            values.loc[symbol.upper()] = score
        return values


@pytest.fixture
def fixed_factor():
    """The FixedFactor class; instantiate inside the test."""
    return FixedFactor


@pytest.fixture
def scheduled_factor():
    """The ScheduledFactor class; instantiate inside the test."""
    return ScheduledFactor
