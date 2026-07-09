from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradingbot.data.polling import PollingDataFeed
from tradingbot.engine.clock import TradingSessionClock


class FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        self.calls.append((market, list(symbols)))
        return {symbol: 100.0 + len(self.calls) for symbol in symbols}


def dt(hour: int, minute: int = 0):
    return datetime(2020, 1, 2, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))


def test_polling_feed_honors_session_and_interval():
    fetcher = FakeFetcher()
    clock = TradingSessionClock("KR", poll_interval=timedelta(minutes=5))
    feed = PollingDataFeed("KR", ["aaa"], clock, price_fetcher=fetcher)

    assert feed.poll(dt(8, 59)) is None
    assert fetcher.calls == []

    first = feed.poll(dt(9, 0))
    assert first is not None
    assert first.prices == {"AAA": 101.0}
    assert fetcher.calls == [("KR", ["AAA"])]

    assert feed.poll(dt(9, 4)) is None
    assert len(fetcher.calls) == 1

    second = feed.poll(dt(9, 5))
    assert second is not None
    assert second.prices == {"AAA": 102.0}
    assert len(fetcher.calls) == 2

    assert feed.poll(dt(15, 30)) is None
    assert len(fetcher.calls) == 2


def test_fetch_prices_normalizes_symbol_keys():
    clock = TradingSessionClock("KR")
    feed = PollingDataFeed("KR", ["aaa"], clock, price_fetcher=lambda market, symbols: {"aaa": 123})

    assert feed.fetch_prices() == {"AAA": 123.0}
