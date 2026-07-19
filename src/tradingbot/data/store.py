from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

from tradingbot.data.cache import ParquetCache


class PriceDataStore(Protocol):
    """Point-in-time price access for factor computation.

    Implementations must never return rows dated after `end` — this is the
    central guard against look-ahead bias in cross-sectional research.
    """

    market: str

    def price_history(self, symbol: str, end: date, lookback: int) -> pd.DataFrame:
        ...


class ParquetDataStore:
    """PriceDataStore over the local Parquet cache. No network access."""

    def __init__(self, cache: ParquetCache, market: str) -> None:
        self.cache = cache
        self.market = market.upper()

    def price_history(self, symbol: str, end: date, lookback: int) -> pd.DataFrame:
        df = self.cache.read(self.market, symbol)
        cutoff = pd.Timestamp(end)
        return df.loc[df.index <= cutoff].tail(lookback)

    def close_series(self, symbol: str) -> pd.Series:
        """Full close history for research labels (look-ahead by design)."""
        return self.cache.read(self.market, symbol)["close"].dropna()


class ResearchDataStore(PriceDataStore, Protocol):
    """PriceDataStore + full-history close access for research labels.

    close_series intentionally sees past any as-of date — labels are
    evaluation targets, never factor inputs. Factor code must keep using
    price_history, which enforces the point-in-time cutoff.
    """

    def close_series(self, symbol: str) -> pd.Series:
        ...
