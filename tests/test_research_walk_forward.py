from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.research.walk_forward import (
    WalkForwardWindow,
    walk_forward_ic,
    walk_forward_windows,
    window_win_rate,
)


class TestWalkForwardWindows:
    def test_three_windows_2010_to_2015(self):
        windows = walk_forward_windows(
            date(2010, 1, 1), date(2015, 12, 31), train_years=3, test_years=1, step_years=1
        )
        assert len(windows) == 3
        first = windows[0]
        assert first.train_start == date(2010, 1, 1)
        assert first.train_end == date(2012, 12, 31)
        assert first.test_start == date(2013, 1, 1)
        assert first.test_end == date(2013, 12, 31)
        assert windows[-1].test_end == date(2015, 12, 31)

    def test_last_window_test_end_capped_at_end(self):
        windows = walk_forward_windows(
            date(2010, 1, 1), date(2013, 6, 30), train_years=3, test_years=1, step_years=1
        )
        assert len(windows) == 1
        assert windows[0].test_end == date(2013, 6, 30)

    def test_invalid_years_raise(self):
        with pytest.raises(ValueError):
            walk_forward_windows(date(2010, 1, 1), date(2015, 1, 1), train_years=0, test_years=1, step_years=1)


class TestWalkForwardIC:
    def test_single_window_perfect_ic(self, us_store, write_prices, fixed_factor):
        n = 300
        write_prices(us_store.cache, "US", "AAA", [100.0 + 2 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BBB", [100.0 + 1 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "CCC", [100.0] * n, start=date(2020, 1, 1))
        windows = [
            WalkForwardWindow(date(2019, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 6, 30))
        ]
        factor = fixed_factor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})

        results = walk_forward_ic(
            factor, us_store, ["AAA", "BBB", "CCC"], market="US", horizon_days=5, windows=windows
        )
        assert len(results) == 1
        assert results.loc[0, "test_ic_mean"] == pytest.approx(1.0)
        assert results.loc[0, "n_periods"] == 6  # Jan..Jun month-ends
        assert window_win_rate(results) == pytest.approx(1.0)

    def test_win_rate_of_empty_results_is_nan(self):
        assert math.isnan(window_win_rate(pd.DataFrame()))
