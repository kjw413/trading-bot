from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.research.ic import ic_series, spearman_ic, summarize_ic


class TestSpearmanIC:
    def test_perfect_positive(self):
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert spearman_ic(scores, forward) == pytest.approx(1.0)

    def test_perfect_negative(self):
        scores = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert spearman_ic(scores, forward) == pytest.approx(-1.0)

    def test_nan_pairs_dropped(self):
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": float("nan")})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04})
        assert spearman_ic(scores, forward) == pytest.approx(1.0)

    def test_fewer_than_three_pairs_is_nan(self):
        scores = pd.Series({"A": 1.0, "B": 2.0})
        forward = pd.Series({"A": 0.01, "B": 0.02})
        assert math.isnan(spearman_ic(scores, forward))

    def test_constant_scores_is_nan(self):
        scores = pd.Series({"A": 1.0, "B": 1.0, "C": 1.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert math.isnan(spearman_ic(scores, forward))

    def test_spearman_does_not_require_scipy(self):
        import sys

        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
        forward = pd.Series({"A": 0.03, "B": 0.01, "C": 0.02, "D": 0.04})
        spearman_ic(scores, forward)
        assert "scipy" not in sys.modules


class TestICSeries:
    def test_series_and_summary(self, us_store, write_prices, fixed_factor):
        n = 60
        # AAA rises fastest, BBB medium, CCC flat -> IC = 1.0 on every date
        write_prices(us_store.cache, "US", "AAA", [100.0 + 2 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BBB", [100.0 + 1 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "CCC", [100.0] * n, start=date(2020, 1, 1))
        factor = fixed_factor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})

        ics = ic_series(factor, us_store, ["AAA", "BBB", "CCC"], [date(2020, 1, 31), date(2020, 2, 28)], 5)
        assert len(ics) == 2
        assert ics.iloc[0] == pytest.approx(1.0)
        assert ics.iloc[1] == pytest.approx(1.0)

        summary = summarize_ic(ics)
        assert summary.mean == pytest.approx(1.0)
        assert summary.n_periods == 2
        assert summary.positive_share == pytest.approx(1.0)
        assert math.isnan(summary.ir)  # std of constant series is 0 -> IR undefined

    def test_summary_of_empty_series(self):
        summary = summarize_ic(pd.Series(dtype=float))
        assert summary.n_periods == 0
        assert math.isnan(summary.mean)
