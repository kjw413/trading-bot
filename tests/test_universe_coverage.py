from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.data.universe import CoverageGap, Theme, ThemeMember, coverage_gaps

START = date(2024, 1, 2)
END = date(2024, 1, 10)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path / "cache"), "US")


def theme(*members: ThemeMember) -> Theme:
    return Theme(key="demo", name="Demo", market="US", members=members)


def write_prices(
    store: ParquetDataStore, symbol: str, start: date, end: date
) -> None:
    index = pd.bdate_range(start=start, end=end)
    values = [100.0] * len(index)
    store.cache.write(
        store.market,
        symbol,
        pd.DataFrame(
            {
                "open": values,
                "high": values,
                "low": values,
                "close": values,
                "volume": [1_000.0] * len(index),
            },
            index=index,
        ),
    )


def test_fully_covered_theme_yields_no_gaps(store):
    selected = theme(ThemeMember("COVERED", start=START))
    write_prices(store, "COVERED", START, END)

    assert coverage_gaps(selected, store, START, END) == []


def test_reports_symbol_absent_from_store(store):
    selected = theme(ThemeMember("MISSING", start=START))

    assert coverage_gaps(selected, store, START, END) == [
        CoverageGap("MISSING", "absent", START, END)
    ]


def test_reports_history_that_starts_late(store):
    selected = theme(ThemeMember("LATE", start=START))
    cached_start = date(2024, 1, 3)
    write_prices(store, "LATE", cached_start, END)

    assert coverage_gaps(selected, store, START, END) == [
        CoverageGap("LATE", "starts_late", START, END, cached_start, END)
    ]


def test_reports_history_that_ends_early(store):
    selected = theme(ThemeMember("STALE", start=START))
    cached_end = date(2024, 1, 9)
    write_prices(store, "STALE", START, cached_end)

    assert coverage_gaps(selected, store, START, END) == [
        CoverageGap("STALE", "ends_early", START, END, START, cached_end)
    ]


@pytest.mark.parametrize(
    ("required_start", "required_end", "cached_start", "cached_end"),
    [
        (
            date(2024, 1, 2),
            date(2024, 1, 6),  # Saturday
            date(2024, 1, 2),
            date(2024, 1, 5),
        ),
        (
            date(2024, 7, 1),
            date(2024, 7, 4),  # NYSE Independence Day holiday
            date(2024, 7, 1),
            date(2024, 7, 3),
        ),
        (
            date(2024, 1, 7),  # Sunday
            date(2024, 1, 10),
            date(2024, 1, 8),
            date(2024, 1, 10),
        ),
        (
            date(2024, 1, 1),  # NYSE New Year's Day holiday
            date(2024, 1, 5),
            date(2024, 1, 2),
            date(2024, 1, 5),
        ),
    ],
)
def test_non_trading_range_boundaries_do_not_create_gaps(
    store, required_start, required_end, cached_start, cached_end
):
    selected = theme(ThemeMember("COVERED", start=required_start))
    write_prices(store, "COVERED", cached_start, cached_end)

    assert coverage_gaps(selected, store, required_start, required_end) == []


def test_range_without_trading_sessions_has_no_gaps(store):
    weekend_start = date(2024, 1, 6)
    weekend_end = date(2024, 1, 7)
    selected = theme(ThemeMember("NO_SESSION", start=weekend_start))

    assert coverage_gaps(selected, store, weekend_start, weekend_end) == []


def test_ignores_members_outside_requested_range(store):
    selected = theme(
        ThemeMember("PAST", start=date(2020, 1, 1), end=date(2023, 12, 31)),
        ThemeMember("FUTURE", start=date(2025, 1, 1)),
    )

    assert coverage_gaps(selected, store, START, END) == []
