from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.data.universe import ThemeUniverse, Theme, ThemeMember
from tradingbot.data.universe_liquidity import LiquidityUniverse, period_start

DAYS = 500
END = date(2024, 6, 28)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path / "cache"), "US")


def write(store, symbol: str, price: float, volume: float, days: int = DAYS, end: date = END):
    index = pd.bdate_range(end=pd.Timestamp(end), periods=days)
    closes = np.full(days, price)
    store.cache.write(
        "US",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": np.full(days, volume),
            },
            index=index,
        ),
    )


def universe(store, candidates, **overrides) -> LiquidityUniverse:
    params = dict(
        market="US",
        candidates=candidates,
        data_store=store,
        top_n=2,
        min_listing_days=400,
    )
    params.update(overrides)
    return LiquidityUniverse(**params)


class TestRanking:
    def test_picks_the_most_traded_names(self, store):
        write(store, "BIG", 100.0, 1_000_000)
        write(store, "MID", 100.0, 100_000)
        write(store, "SMALL", 100.0, 1_000)
        assert universe(store, ["BIG", "MID", "SMALL"]).members(END) == ["BIG", "MID"]

    def test_ranks_on_traded_value_not_price_or_share_count(self, store):
        # A cheap, heavily traded name outranks an expensive, thin one.
        write(store, "CHEAP", 1.0, 10_000_000)     # $10M
        write(store, "PRICEY", 1_000.0, 1_000)     # $1M
        assert universe(store, ["CHEAP", "PRICEY"], top_n=1).members(END) == ["CHEAP"]

    def test_a_short_history_is_excluded(self, store):
        # A recent listing can be enormously liquid and still have too little
        # history for a momentum factor to score it.
        write(store, "OLD", 100.0, 1_000)
        write(store, "NEW", 100.0, 100_000_000, days=100)
        assert universe(store, ["OLD", "NEW"], top_n=2).members(END) == ["OLD"]

    def test_a_missing_symbol_is_skipped_not_ranked_zero(self, store):
        write(store, "REAL", 100.0, 1_000)
        assert universe(store, ["REAL", "GHOST"], top_n=2).members(END) == ["REAL"]

    def test_a_dollar_volume_floor_excludes(self, store):
        write(store, "BIG", 100.0, 1_000_000)
        write(store, "TINY", 1.0, 10)
        selected = universe(
            store, ["BIG", "TINY"], top_n=2, min_dollar_volume=1_000_000
        ).members(END)
        assert selected == ["BIG"]

    def test_result_is_sorted_for_reproducibility(self, store):
        for symbol in ("ZZZ", "AAA", "MMM"):
            write(store, symbol, 100.0, 1_000_000)
        assert universe(store, ["ZZZ", "AAA", "MMM"], top_n=3).members(END) == [
            "AAA",
            "MMM",
            "ZZZ",
        ]

    def test_ties_break_deterministically(self, store):
        # Identical liquidity must not depend on candidate ordering, or two
        # runs of the same backtest hold different portfolios.
        for symbol in ("AAA", "BBB", "CCC"):
            write(store, symbol, 100.0, 1_000_000)
        forward = universe(store, ["AAA", "BBB", "CCC"], top_n=2).members(END)
        backward = universe(store, ["CCC", "BBB", "AAA"], top_n=2).members(END)
        assert forward == backward


class TestPointInTime:
    def test_the_anchor_precedes_the_period(self, store):
        # June's universe is decided before June starts.
        anchor = universe(store, []).anchor_for(date(2024, 6, 17))
        assert anchor < date(2024, 6, 1)

    def test_every_day_in_a_period_gets_the_same_universe(self, store):
        write(store, "BIG", 100.0, 1_000_000)
        write(store, "MID", 100.0, 100_000)
        u = universe(store, ["BIG", "MID"], top_n=1)
        assert u.members(date(2024, 6, 3)) == u.members(date(2024, 6, 27))

    def test_membership_does_not_depend_on_when_it_was_first_asked(self, store):
        # The bug this design exists to prevent: caching from the first call
        # would make a backtest starting in June disagree with one starting in
        # January about June's universe.
        write(store, "BIG", 100.0, 1_000_000)
        write(store, "MID", 100.0, 100_000)

        early = universe(store, ["BIG", "MID"], top_n=1)
        early.members(date(2024, 1, 15))
        late = universe(store, ["BIG", "MID"], top_n=1)
        assert early.members(date(2024, 6, 17)) == late.members(date(2024, 6, 17))

    def test_data_after_the_anchor_cannot_change_the_answer(self, store):
        # SLEEPER trades nothing until June, then explodes. June's universe is
        # anchored in May and must not see it.
        write(store, "STEADY", 100.0, 500_000)
        index = pd.bdate_range(end=pd.Timestamp(END), periods=DAYS)
        volume = np.concatenate(
            [np.full(DAYS - 20, 1.0), np.full(20, 100_000_000.0)]
        )
        closes = np.full(DAYS, 100.0)
        store.cache.write(
            "US",
            "SLEEPER",
            pd.DataFrame(
                {"open": closes, "high": closes, "low": closes, "close": closes,
                 "volume": volume},
                index=index,
            ),
        )
        assert universe(store, ["STEADY", "SLEEPER"], top_n=1).members(date(2024, 6, 17)) == [
            "STEADY"
        ]


class TestPeriodStart:
    @pytest.mark.parametrize(
        "dt, expected",
        [
            (date(2024, 6, 17), date(2024, 6, 1)),
            (date(2024, 1, 1), date(2024, 1, 1)),
            (date(2024, 12, 31), date(2024, 12, 1)),
        ],
    )
    def test_monthly(self, dt, expected):
        assert period_start(dt, "monthly") == expected

    def test_quarterly(self):
        assert period_start(date(2024, 5, 20), "quarterly") == date(2024, 4, 1)
        assert period_start(date(2024, 12, 31), "quarterly") == date(2024, 10, 1)

    def test_weekly_starts_on_monday(self):
        # 2024-06-19 is a Wednesday.
        assert period_start(date(2024, 6, 19), "weekly") == date(2024, 6, 17)

    def test_an_unknown_period_is_rejected(self):
        with pytest.raises(ValueError, match="daily"):
            period_start(date(2024, 6, 19), "daily")


class TestValidation:
    def test_an_unknown_rebalance_period_fails_at_construction(self, store):
        with pytest.raises(ValueError, match="daily"):
            universe(store, [], rebalance="daily")

    def test_top_n_must_be_positive(self, store):
        with pytest.raises(ValueError, match="top_n"):
            universe(store, [], top_n=0)


class TestInterchangeability:
    """A theme and a liquidity screen must be usable through one interface."""

    def test_both_satisfy_the_same_call(self, store):
        write(store, "BIG", 100.0, 1_000_000)
        theme = ThemeUniverse(
            Theme(
                key="t",
                name="T",
                market="US",
                members=(ThemeMember(symbol="BIG", start=date(2020, 1, 1)),),
            )
        )
        liquidity = universe(store, ["BIG"], top_n=1)
        for candidate in (theme, liquidity):
            assert candidate.market == "US"
            assert candidate.members(END) == ["BIG"]

    def test_a_theme_still_respects_its_dates(self, store):
        theme = ThemeUniverse(
            Theme(
                key="t",
                name="T",
                market="US",
                members=(ThemeMember(symbol="LATE", start=date(2025, 1, 1)),),
            )
        )
        assert theme.members(END) == []
