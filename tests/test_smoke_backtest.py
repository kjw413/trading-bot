from __future__ import annotations

import pandas as pd

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.strategies.ma_cross import MovingAverageCrossStrategy


def test_ma_cross_smoke_backtest(tmp_path):
    cache = ParquetCache(tmp_path / "cache")
    dates = pd.bdate_range("2020-01-01", periods=90)
    closes = [100 - i * 0.3 for i in range(35)] + [89.5 + i * 0.9 for i in range(55)]
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [price * 1.01 for price in closes],
            "low": [price * 0.99 for price in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        },
        index=dates,
    )
    cache.write("KR", "005930", df)

    feed = HistoricalDataFeed(cache, "KR", ["005930"], start="2020-01-01")
    broker = BacktestBroker(initial_cash=10_000_000, commission_rate=0.00015)
    strategy = MovingAverageCrossStrategy(fast=3, slow=8, weight=0.95)

    result = BacktestEngine(feed, broker, strategy).run()

    assert result.final_equity > 0
    assert result.trade_count >= 1
