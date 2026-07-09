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



class RecordingCloseStrategy(Strategy):
    name = "recording_close"

    def __init__(self) -> None:
        super().__init__()
        self.bars: list[Bar] = []
        self.history_highs: list[float] = []

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        self.bars.append(bar)
        self.history_highs.append(float(ctx.history(bar.symbol, 1).iloc[-1]["high"]))


class UpdatingCache(ParquetCache):
    def __init__(self, root, daily_rows: dict[str, dict[str, float]]) -> None:
        super().__init__(root)
        self.daily_rows = daily_rows
        self.updates: list[tuple[str, str, object, object]] = []

    def update(self, market: str, symbol: str, start=None, end=None):
        self.updates.append((market, symbol, start, end))
        df = self.read(market, symbol)
        row = self.daily_rows[symbol]
        fresh = pd.DataFrame([row], index=[pd.Timestamp(start)])
        combined = pd.concat([df, fresh]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        self.write(market, symbol, combined)
        return self.read(market, symbol)


class FailingUpdateCache(ParquetCache):
    def update(self, market: str, symbol: str, start=None, end=None):
        raise RuntimeError("daily source unavailable")


class RaisingFetcher:
    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        raise AssertionError("polling fetcher should not be called")


def seed_cache(cache: ParquetCache) -> None:
    df = pd.DataFrame(
        {
            "open": [100],
            "high": [105],
            "low": [95],
            "close": [100],
            "volume": [1000],
        },
        index=pd.to_datetime(["2020-01-02"]),
    )
    cache.write("KR", "AAA", df)


def test_paper_close_refreshes_confirmed_daily_bar_and_reloads_history(tmp_path):
    cache = UpdatingCache(
        tmp_path / "cache",
        {"AAA": {"open": 111, "high": 130, "low": 90, "close": 120, "volume": 2000}},
    )
    seed_cache(cache)
    history_feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-02")
    clock = TradingSessionClock("KR", poll_interval=timedelta(minutes=5))
    polling_feed = PollingDataFeed("KR", ["AAA"], clock, price_fetcher=RaisingFetcher())
    broker = PaperBroker("close", tmp_path / "state", 1_000, market="KR", fee_model=FeeModel("KR"), slippage_bps=0)
    strategy = RecordingCloseStrategy()
    engine = PaperTradingEngine(history_feed, polling_feed, broker, strategy)

    snapshot = engine.run_once(datetime(2020, 1, 3, 15, 31, tzinfo=ZoneInfo("Asia/Seoul")))

    assert snapshot["actions"] == ["close"]
    assert cache.updates == [("KR", "AAA", date(2020, 1, 3), date(2020, 1, 3))]
    assert strategy.bars[0].open == 111
    assert strategy.bars[0].high == 130
    assert strategy.bars[0].low == 90
    assert strategy.bars[0].close == 120
    assert strategy.history_highs == [130]


def test_paper_close_falls_back_to_polling_snapshot_when_daily_update_fails(tmp_path):
    cache = FailingUpdateCache(tmp_path / "cache")
    seed_cache(cache)
    history_feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-02")
    clock = TradingSessionClock("KR", poll_interval=timedelta(minutes=5))
    polling_feed = PollingDataFeed("KR", ["AAA"], clock, price_fetcher=StaticFetcher(123))
    broker = PaperBroker("fallback", tmp_path / "state", 1_000, market="KR", fee_model=FeeModel("KR"), slippage_bps=0)
    strategy = RecordingCloseStrategy()
    engine = PaperTradingEngine(history_feed, polling_feed, broker, strategy)

    snapshot = engine.run_once(datetime(2020, 1, 3, 15, 31, tzinfo=ZoneInfo("Asia/Seoul")))

    assert snapshot["actions"] == ["close_fallback"]
    assert strategy.bars[0].open == 123
    assert strategy.bars[0].high == 123
    assert strategy.bars[0].low == 123
    assert strategy.bars[0].close == 123
    assert broker.metadata["last_close_date"] == "2020-01-03"
