from __future__ import annotations

from datetime import date

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
        self.dates = self._build_dates()

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            df = self.cache.read(self.market, symbol)
            df = df.loc[df.index >= self.start]
            if self.end is not None:
                df = df.loc[df.index <= self.end]
            if df.empty:
                raise ValueError(f"No cached bars for {self.market} {symbol} in requested date range")
            frames[symbol] = df
        return frames

    def _build_dates(self) -> list[date]:
        all_dates: set[date] = set()
        for df in self.frames.values():
            all_dates.update(ts.date() for ts in df.index)
        return sorted(all_dates)

    def events(self):
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

    def history(self, symbol: str, current_dt: date, n: int) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol not in self.frames:
            raise KeyError(f"Unknown symbol: {symbol}")
        end = pd.Timestamp(current_dt)
        return self.frames[symbol].loc[:end].tail(n).copy()
