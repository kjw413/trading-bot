from __future__ import annotations

from datetime import date
from typing import Iterator

import pandas as pd

from tradingbot.data.cache import ParquetCache
from tradingbot.models import Bar, SessionClose, SessionOpen


class HistoricalDataFeed:
    def __init__(
        self,
        cache: ParquetCache,
        market: str,
        symbols: list[str],
        start: str | date,
        end: str | date | None = None,
    ) -> None:
        self.cache = cache
        self.market = market.upper()
        self.symbols = [symbol.upper() for symbol in symbols]
        self.start = pd.to_datetime(start).normalize()
        self.end = pd.to_datetime(end).normalize() if end else None
        self.frames = self._load_frames()
        self.dates = self._build_event_dates()

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            df = self.cache.read(self.market, symbol)
            if self.end is not None:
                df = df.loc[df.index <= self.end]
            event_df = df.loc[df.index >= self.start]
            if event_df.empty:
                raise ValueError(f"No cached bars for {self.market} {symbol} in requested date range")
            frames[symbol] = df
        return frames

    def _build_event_dates(self) -> list[date]:
        all_dates: set[date] = set()
        for df in self.frames.values():
            event_df = df.loc[df.index >= self.start]
            if self.end is not None:
                event_df = event_df.loc[event_df.index <= self.end]
            all_dates.update(ts.date() for ts in event_df.index)
        return sorted(all_dates)

    def events(self) -> Iterator[SessionOpen | SessionClose]:
        for dt in self.dates:
            opens: dict[str, float] = {}
            bars: dict[str, Bar] = {}

            for symbol, df in self.frames.items():
                key = pd.Timestamp(dt)
                if key not in df.index:
                    continue
                row = df.loc[key]
                opens[symbol] = float(row["open"])
                bars[symbol] = Bar(
                    symbol=symbol,
                    dt=dt,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )

            if opens:
                yield SessionOpen(dt=dt, opens=opens)
            if bars:
                yield SessionClose(dt=dt, bars=bars)

    def history(
        self,
        symbol: str,
        current_dt: date,
        n: int,
        *,
        include_current: bool = True,
    ) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol not in self.frames:
            raise KeyError(f"Unknown symbol: {symbol}")
        end = pd.Timestamp(current_dt)
        df = self.frames[symbol]
        if include_current:
            history = df.loc[:end]
        else:
            history = df.loc[df.index < end]
        return history.tail(n).copy()
