from __future__ import annotations

from datetime import date

import pandas as pd

from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed


def test_feed_keeps_warmup_data_but_limits_events(tmp_path):
    cache = ParquetCache(tmp_path / "cache")
    dates = pd.bdate_range("2020-01-01", periods=10)
    df = pd.DataFrame(
        {
            "open": range(10, 20),
            "high": range(11, 21),
            "low": range(9, 19),
            "close": range(10, 20),
            "volume": [1000] * 10,
        },
        index=dates,
    )
    cache.write("KR", "AAA", df)

    feed = HistoricalDataFeed(cache, "KR", ["AAA"], start="2020-01-08")

    assert feed.dates[0] == date(2020, 1, 8)
    assert len(feed.history("AAA", date(2020, 1, 8), 10)) == 6
    assert len(feed.history("AAA", date(2020, 1, 8), 10, include_current=False)) == 5
