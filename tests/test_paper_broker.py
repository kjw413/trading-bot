from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from tradingbot.broker.fees import FeeModel
from tradingbot.broker.paper import PaperBroker
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.data.polling import PollingDataFeed
from tradingbot.engine.clock import TradingSessionClock
from tradingbot.engine.paper import PaperTradingEngine
from tradingbot.models import Bar, Order, OrderPhase, OrderSide, OrderType
from tradingbot.strategies.base import Strategy, StrategyContext


class BuyOnceOnOpenStrategy(Strategy):
    name = "buy_once_on_open"

    def __init__(self) -> None:
        super().__init__()
        self.sent = False

    def on_open(self, ctx: StrategyContext, dt, opens: dict[str, float]) -> None:
        if not self.sent:
            ctx.buy("AAA", qty=1, order_type=OrderType.MARKET)
            self.sent = True

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        pass


class StaticFetcher:
    def __init__(self, price: float) -> None:
        self.price = price
        self.calls = 0

    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        self.calls += 1
        return {symbol: self.price for symbol in symbols}


def test_paper_broker_persists_account_state(tmp_path):
    broker = PaperBroker(
        name="demo",
        state_dir=tmp_path,
        initial_cash=1_000,
        market="KR",
        fee_model=FeeModel("KR"),
        slippage_bps=0,
    )
    broker.set_metadata("last_open_date", "2020-01-02")
    broker.submit(
        Order(
            id="O1",
            symbol="aaa",
            side=OrderSide.BUY,
            qty=2,
            order_type=OrderType.MARKET,
            created_at=date(2020, 1, 1),
            created_phase=OrderPhase.CLOSE,
        )
    )

    fills = broker.on_session_open(date(2020, 1, 2), {"AAA": 100})

    assert len(fills) == 1
    assert broker.position("AAA").qty == 2
    assert broker.cash == 800

    restored = PaperBroker(
        name="demo",
        state_dir=tmp_path,
        initial_cash=999,
        market="KR",
        fee_model=FeeModel("KR"),
        slippage_bps=0,
    )

    assert restored.portfolio.initial_cash == 1_000
    assert restored.cash == 800
    assert restored.position("AAA").qty == 2
    assert restored.fills[0].order_id == "O1"
    assert restored.metadata["last_open_date"] == "2020-01-02"
    assert restored.next_order_number() == 2


def test_paper_trading_engine_runs_with_injected_clock_and_prices(tmp_path):
    cache = ParquetCache(tmp_path / "cache")
    df = pd.DataFrame(
        {
            "open": [100, 110],
            "high": [101, 111],
            "low": [99, 109],
            "close": [100, 110],
            "volume": [1000, 1000],
        },
        index=pd.to_datetime(["2020-01-02", "2020-01-03"]),
    )
    cache.write("KR", "AAA", df)

    clock = TradingSessionClock("KR", poll_interval=timedelta(minutes=5))
    fetcher = StaticFetcher(100)
    polling_feed = PollingDataFeed("KR", ["AAA"], clock, price_fetcher=fetcher)
    history_feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-02")
    broker = PaperBroker(
        name="engine",
        state_dir=tmp_path / "state",
        initial_cash=1_000,
        market="KR",
        fee_model=FeeModel("KR"),
        slippage_bps=0,
    )
    engine = PaperTradingEngine(history_feed, polling_feed, broker, BuyOnceOnOpenStrategy())
    tz = ZoneInfo("Asia/Seoul")

    first = engine.run_once(datetime(2020, 1, 2, 9, 0, tzinfo=tz))
    assert first["actions"] == ["open", "poll"]
    assert first["open_orders"] == 1
    assert broker.position("AAA").qty == 0

    restored = PaperBroker(
        name="engine",
        state_dir=tmp_path / "state",
        initial_cash=1_000,
        market="KR",
        fee_model=FeeModel("KR"),
        slippage_bps=0,
    )
    restarted_engine = PaperTradingEngine(
        history_feed,
        PollingDataFeed("KR", ["AAA"], clock, price_fetcher=StaticFetcher(100)),
        restored,
        BuyOnceOnOpenStrategy(),
    )

    second = restarted_engine.run_once(datetime(2020, 1, 3, 9, 0, tzinfo=tz))
    assert "open" in second["actions"]
    assert restored.position("AAA").qty == 1
    assert restored.fills[0].dt == date(2020, 1, 3)
    assert [order.id for order in restored.open_orders()] == ["O00000002"]

    final = PaperBroker(
        name="engine",
        state_dir=tmp_path / "state",
        initial_cash=1_000,
        market="KR",
        fee_model=FeeModel("KR"),
        slippage_bps=0,
    )
    assert final.position("AAA").qty == 1
    assert final.metadata["last_open_date"] == "2020-01-03"

