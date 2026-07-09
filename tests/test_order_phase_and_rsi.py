from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.models import Bar, OrderType
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.strategies.rsi_reversion import _wilder_rsi
from tradingbot.strategies.vol_breakout import VolatilityBreakoutStrategy


class OpenMarketBuyStrategy(Strategy):
    name = "open_market_buy"

    def __init__(self) -> None:
        super().__init__()
        self.sent = False

    def on_open(self, ctx: StrategyContext, dt, opens: dict[str, float]) -> None:
        if not self.sent:
            ctx.buy("AAA", qty=1, order_type=OrderType.MARKET)
            self.sent = True

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        pass


def write_cache(tmp_path, rows):
    cache = ParquetCache(tmp_path / "cache")
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    cache.write("KR", "AAA", df)
    return cache


def test_vol_breakout_next_open_exit_survives_same_day_expiry(tmp_path):
    cache = write_cache(
        tmp_path,
        [
            {"Date": "2020-01-02", "open": 10, "high": 10, "low": 8, "close": 9, "volume": 1000},
            {"Date": "2020-01-03", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 1000},
            {"Date": "2020-01-06", "open": 12, "high": 12, "low": 11, "close": 12, "volume": 1000},
        ],
    )
    feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-02")
    broker = BacktestBroker(100_000, market="KR", fee_model=FeeModel("KR"), slippage_bps=0)
    strategy = VolatilityBreakoutStrategy(k=0.5, weight=0.20, exit="next_open")

    result = BacktestEngine(feed, broker, strategy).run()

    assert result.trade_count == 2
    assert broker.position("AAA").qty == 0
    assert not [order for order in result.expired_orders if order.side.value == "SELL"]


def test_market_order_created_on_open_gets_next_open_chance(tmp_path):
    cache = write_cache(
        tmp_path,
        [
            {"Date": "2020-01-02", "open": 10, "high": 10, "low": 9, "close": 10, "volume": 1000},
            {"Date": "2020-01-03", "open": 11, "high": 11, "low": 10, "close": 11, "volume": 1000},
        ],
    )
    feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-02")
    broker = BacktestBroker(100_000, market="KR", fee_model=FeeModel("KR"), slippage_bps=0)

    result = BacktestEngine(feed, broker, OpenMarketBuyStrategy()).run()

    assert result.trade_count == 1
    assert result.fills[0].dt == pd.Timestamp("2020-01-03").date()
    assert result.expired_orders == []


def test_wilder_rsi_matches_reference_series():
    closes = pd.Series(
        [
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
            46.00,
            46.03,
            46.41,
            46.22,
            45.64,
            46.21,
            46.25,
            45.71,
            46.45,
            45.78,
            45.35,
            44.03,
            44.18,
            44.22,
            44.57,
            43.42,
            42.66,
            43.13,
        ]
    )

    assert _wilder_rsi(closes, 14) == pytest.approx(37.7888, abs=1e-4)
