from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from tradingbot.data.sources import fetch_ohlcv, normalize_ohlcv


class ParquetCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, market: str, symbol: str) -> Path:
        return self.root / market.upper() / f"{symbol.upper()}.parquet"

    def exists(self, market: str, symbol: str) -> bool:
        return self.path(market, symbol).exists()

    def read(self, market: str, symbol: str) -> pd.DataFrame:
        path = self.path(market, symbol)
        if not path.exists():
            raise FileNotFoundError(
                f"Cache not found for {market.upper()} {symbol}: {path}. "
                "Run `python -m tradingbot data update ...` first."
            )
        df = pd.read_parquet(path)
        return normalize_ohlcv(df)

    def write(self, market: str, symbol: str, df: pd.DataFrame) -> Path:
        normalized = normalize_ohlcv(df)
        path = self.path(market, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_parquet(path)
        return path

    def update(
        self,
        market: str,
        symbol: str,
        start: str | date | None = None,
        end: str | date | None = None,
    ) -> pd.DataFrame:
        existing = self.read(market, symbol) if self.exists(market, symbol) else None
        fetch_start: str | date

        if existing is not None and not existing.empty:
            last_dt = existing.index.max().date()
            requested_start = pd.to_datetime(start).date() if start else None
            if requested_start and requested_start < existing.index.min().date():
                fetch_start = requested_start
            else:
                fetch_start = last_dt + timedelta(days=1)
        else:
            fetch_start = start or "2015-01-01"

        fresh = fetch_ohlcv(market, symbol, fetch_start, end)
        if existing is None:
            combined = fresh
        elif fresh.empty:
            combined = existing
        else:
            combined = pd.concat([existing, fresh]).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]

        self.write(market, symbol, combined)
        return combined
