from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.research.labels import excess_forward_returns, forward_return, forward_returns

INDEX = pd.bdate_range(start="2020-01-01", periods=30)
CLOSES = pd.Series([float(100 + i) for i in range(30)], index=INDEX)


class TestForwardReturn:
    def test_five_day_horizon(self):
        dt = INDEX[5].date()  # close 105; 5 rows later -> 110
        assert forward_return(CLOSES, dt, 5) == pytest.approx(110.0 / 105.0 - 1.0)

    def test_base_is_last_close_at_or_before_dt(self):
        saturday = date(2020, 1, 11)  # last close = Fri 2020-01-10 (107); +5 rows -> 112
        assert forward_return(CLOSES, saturday, 5) == pytest.approx(112.0 / 107.0 - 1.0)

    def test_runs_off_series_end_is_nan(self):
        assert np.isnan(forward_return(CLOSES, INDEX[-3].date(), 5))

    def test_before_series_start_is_nan(self):
        assert np.isnan(forward_return(CLOSES, date(2019, 12, 31), 5))

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError):
            forward_return(CLOSES, INDEX[0].date(), 0)


class TestForwardReturns:
    def test_missing_symbol_is_nan_not_error(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [float(100 + i) for i in range(30)], start=date(2020, 1, 1))
        result = forward_returns(us_store, ["aaa", "MISSING"], INDEX[5].date(), 5)
        assert result.name == "fwd_5d"
        assert result.loc["AAA"] == pytest.approx(110.0 / 105.0 - 1.0)
        assert np.isnan(result.loc["MISSING"])

    def test_close_series_returns_full_history(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [100.0, 101.0, 102.0], start=date(2020, 1, 1))
        assert list(us_store.close_series("AAA")) == [100.0, 101.0, 102.0]


class TestExcessForwardReturns:
    def test_subtracts_benchmark(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [float(100 + i) for i in range(30)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BENCH", [100.0] * 30, start=date(2020, 1, 1))
        result = excess_forward_returns(us_store, ["AAA"], INDEX[5].date(), 5, benchmark="BENCH")
        assert result.name == "excess_5d"
        assert result.loc["AAA"] == pytest.approx(110.0 / 105.0 - 1.0)

    def test_missing_benchmark_raises(self, us_store):
        with pytest.raises((FileNotFoundError, KeyError)):
            excess_forward_returns(us_store, ["AAA"], INDEX[5].date(), 5, benchmark="NOPE")
