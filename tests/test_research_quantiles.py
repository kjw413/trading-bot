from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.research.quantiles import (
    monotonicity,
    quantile_assignments,
    quantile_returns,
    top_quantile_turnover,
)


class TestQuantileAssignments:
    def test_two_buckets(self):
        scores = pd.Series({"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0})
        q = quantile_assignments(scores, 2)
        assert q.loc["A"] == 2 and q.loc["B"] == 2
        assert q.loc["C"] == 1 and q.loc["D"] == 1

    def test_nan_excluded_and_too_few_yields_empty(self):
        scores = pd.Series({"A": 1.0, "B": float("nan")})
        assert quantile_assignments(scores, 2).empty

    def test_invalid_n_quantiles_raises(self):
        with pytest.raises(ValueError):
            quantile_assignments(pd.Series({"A": 1.0}), 1)


class TestQuantileReturns:
    def test_spread(self, us_store, write_prices, fixed_factor):
        # WIN jumps to 110 after row 20, LOSE stays flat at 100.
        write_prices(us_store.cache, "US", "WIN", [100.0] * 20 + [110.0] * 20, start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "LOSE", [100.0] * 40, start=date(2020, 1, 1))
        factor = fixed_factor({"WIN": 2.0, "LOSE": 1.0})
        dt = pd.bdate_range(start="2020-01-01", periods=40)[19].date()

        frame = quantile_returns(factor, us_store, ["WIN", "LOSE"], [dt], horizon_days=5, n_quantiles=2)
        row = frame.loc[pd.Timestamp(dt)]
        assert row["q2"] == pytest.approx(0.10)
        assert row["q1"] == pytest.approx(0.0)
        assert row["spread"] == pytest.approx(0.10)

    def test_dates_without_enough_scores_are_skipped(self, us_store, fixed_factor):
        factor = fixed_factor({})  # nothing scored
        frame = quantile_returns(factor, us_store, ["AAA", "BBB"], [date(2020, 1, 31)], 5, n_quantiles=2)
        assert frame.empty


class TestMonotonicity:
    def test_values(self):
        assert monotonicity([0.0, 0.01, 0.02]) == pytest.approx(1.0)
        assert monotonicity([0.02, 0.01, 0.0]) == pytest.approx(0.0)
        assert monotonicity([0.0, 0.01, 0.005]) == pytest.approx(0.5)
        assert math.isnan(monotonicity([]))


class TestTopQuantileTurnover:
    def test_full_swap_is_one(self, us_store, scheduled_factor):
        d1, d2 = date(2020, 1, 15), date(2020, 2, 14)
        factor = scheduled_factor(
            {
                d1: {"AAA": 4.0, "BBB": 3.0, "CCC": 2.0, "DDD": 1.0},
                d2: {"CCC": 4.0, "DDD": 3.0, "AAA": 2.0, "BBB": 1.0},
            }
        )
        universe = ["AAA", "BBB", "CCC", "DDD"]
        turnover = top_quantile_turnover(factor, us_store, universe, [d1, d2], n_quantiles=2)
        assert list(turnover) == [1.0]

    def test_no_change_is_zero(self, us_store, scheduled_factor):
        scores = {"AAA": 4.0, "BBB": 3.0, "CCC": 2.0, "DDD": 1.0}
        d1, d2 = date(2020, 1, 15), date(2020, 2, 14)
        factor = scheduled_factor({d1: scores, d2: scores})
        turnover = top_quantile_turnover(factor, us_store, list(scores), [d1, d2], n_quantiles=2)
        assert list(turnover) == [0.0]
