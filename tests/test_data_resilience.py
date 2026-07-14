from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.data.sources import _fetch_us, normalize_ohlcv


def test_empty_ohlcv_uses_datetime_index():
    result = normalize_ohlcv(pd.DataFrame())

    assert result.empty
    assert isinstance(result.index, pd.DatetimeIndex)


def test_us_fetch_uses_system_trust_and_adjusts_ohlc(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []
    trust_calls: list[bool] = []
    raw = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [110.0],
            "Low": [90.0],
            "Close": [100.0],
            "Volume": [1000.0],
            "Adj Close": [50.0],
        },
        index=pd.to_datetime(["2024-01-02"]),
    )

    def data_reader(symbol, start, end):
        calls.append((symbol, str(start), str(end) if end else None))
        return raw

    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=lambda: trust_calls.append(True)))
    monkeypatch.setitem(sys.modules, "FinanceDataReader", SimpleNamespace(DataReader=data_reader))

    result = _fetch_us("SOXL", "2024-01-01", "2024-01-03")

    assert trust_calls == [True]
    assert calls == [("SOXL", "2024-01-01", "2024-01-03")]
    assert result.iloc[0][["open", "high", "low", "close"]].tolist() == [50.0, 55.0, 45.0, 50.0]


def test_new_empty_response_is_not_written_to_cache(monkeypatch, tmp_path):
    cache = ParquetCache(tmp_path / "cache")
    empty = normalize_ohlcv(pd.DataFrame())
    monkeypatch.setattr("tradingbot.data.cache.fetch_ohlcv", lambda *args, **kwargs: empty)

    with pytest.raises(ValueError, match="empty response was not cached"):
        cache.update("US", "SOXL", start="2020-01-01")

    assert not cache.path("US", "SOXL").exists()


def test_legacy_empty_cache_raises_clear_feed_error(tmp_path):
    cache = ParquetCache(tmp_path / "cache")
    path = cache.path("US", "SOXL")
    path.parent.mkdir(parents=True)
    pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).to_parquet(path)

    with pytest.raises(ValueError, match="No cached bars"):
        HistoricalDataFeed(cache, "US", ["SOXL"], start="2020-01-01")
