from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol, Sequence

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


class PanelDataStore(Protocol):
    """Point-in-time access to the non-price panels the pipeline collects."""

    def panel(
        self,
        dataset: str,
        as_of: date,
        symbols: Sequence[str] | None = None,
        *,
        start: date | None = None,
    ) -> pd.DataFrame:
        ...


class ParquetDataStore:
    """Local-only store: Parquet price cache plus the point-in-time panels.

    `processed_root` is optional so existing price-only callers keep working;
    without it the panel methods return empty results rather than failing,
    which lets a price-only factor run on a machine that has never run the
    data pipeline.
    """

    def __init__(
        self, cache: ParquetCache, market: str, processed_root: str | Path | None = None
    ) -> None:
        self.cache = cache
        self.market = market.upper()
        self.processed_root = Path(processed_root) if processed_root else None

    def price_history(self, symbol: str, end: date, lookback: int) -> pd.DataFrame:
        df = self.cache.read(self.market, symbol)
        cutoff = pd.Timestamp(end)
        return df.loc[df.index <= cutoff].tail(lookback)

    def close_series(self, symbol: str) -> pd.Series:
        """Full close history for research labels (look-ahead by design)."""
        return self.cache.read(self.market, symbol)["close"].dropna()

    def panel(
        self,
        dataset: str,
        as_of: date,
        symbols: Sequence[str] | None = None,
        *,
        start: date | None = None,
    ) -> pd.DataFrame:
        """Panel rows knowable at `as_of`. Empty when the dataset is absent."""
        if self.processed_root is None:
            return pd.DataFrame()
        from tradingbot.data.panel import PanelStore

        return PanelStore(self.processed_root, dataset, self.market).read(
            as_of=as_of, start=start, symbols=symbols
        )

    def panel_latest(
        self, dataset: str, as_of: date, symbols: Sequence[str], column: str
    ) -> pd.Series:
        """Each symbol's most recent knowable value of `column`.

        Symbols with no observation get NaN so callers can tell "no data" from
        a real value.
        """
        result = pd.Series(
            [float("nan")] * len(symbols),
            index=[str(s).upper() for s in symbols],
            dtype=float,
        )
        frame = self.panel(dataset, as_of, symbols)
        if frame.empty:
            return result
        if column not in frame.columns:
            raise KeyError(f"Panel {dataset} has no column {column}: {list(frame.columns)}")
        newest = frame.sort_values("date").groupby("symbol")[column].last()
        for symbol, value in newest.items():
            if symbol in result.index:
                result.loc[symbol] = float(value)
        return result


class ResearchDataStore(PriceDataStore, Protocol):
    """PriceDataStore + full-history close access for research labels.

    close_series intentionally sees past any as-of date — labels are
    evaluation targets, never factor inputs. Factor code must keep using
    price_history, which enforces the point-in-time cutoff.
    """

    def close_series(self, symbol: str) -> pd.Series:
        ...
