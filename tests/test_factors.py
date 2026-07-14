from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors import get_factor, list_factors, register_factor
from tradingbot.factors.momentum import TRADING_DAYS_PER_MONTH, MomentumFactor


def write_prices(cache: ParquetCache, symbol: str, closes: list[float], end: date) -> None:
    """Write a synthetic daily series ending exactly at `end` (business days)."""
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        },
        index=index,
    )
    cache.write("US", symbol, df)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path), "US")


AS_OF = date(2020, 6, 30)


class TestMomentumFactor:
    def test_three_month_momentum_value(self, store):
        lookback = 3 * TRADING_DAYS_PER_MONTH + 1
        closes = list(np.linspace(100.0, 150.0, lookback))
        write_prices(store.cache, "AAA", closes, AS_OF)

        result = MomentumFactor(3).compute(AS_OF, ["AAA"], store)
        assert result.name == "momentum_3m"
        assert result.loc["AAA"] == pytest.approx(150.0 / 100.0 - 1.0)

    def test_skip_month_variant_excludes_recent_window(self, store):
        months, skip = 12, 1
        lookback = (months + skip) * TRADING_DAYS_PER_MONTH + 1
        # Flat at 100 until the last month, which doubles: 12-1 momentum must be 0.
        closes = [100.0] * (lookback - TRADING_DAYS_PER_MONTH) + [200.0] * TRADING_DAYS_PER_MONTH
        write_prices(store.cache, "AAA", closes, AS_OF)

        result = MomentumFactor(months, skip_months=skip).compute(AS_OF, ["AAA"], store)
        assert result.name == "momentum_12m_ex1m"
        assert result.loc["AAA"] == pytest.approx(0.0)

    def test_no_lookahead_beyond_computation_date(self, store):
        lookback = 3 * TRADING_DAYS_PER_MONTH + 1
        closes = list(np.linspace(100.0, 150.0, lookback))
        # Huge jump *after* the as-of date must not change the result.
        future = closes + [1000.0] * 30
        end_with_future = (pd.Timestamp(AS_OF) + pd.tseries.offsets.BDay(30)).date()
        write_prices(store.cache, "AAA", future, end_with_future)

        result = MomentumFactor(3).compute(AS_OF, ["AAA"], store)
        assert result.loc["AAA"] == pytest.approx(150.0 / 100.0 - 1.0)

    def test_insufficient_history_is_nan(self, store):
        write_prices(store.cache, "AAA", [100.0] * 10, AS_OF)
        result = MomentumFactor(12).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_unknown_symbol_is_nan_not_error(self, store):
        result = MomentumFactor(3).compute(AS_OF, ["ZZZ"], store)
        assert np.isnan(result.loc["ZZZ"])

    def test_wrong_market_store_yields_all_nan(self, tmp_path):
        cache = ParquetCache(tmp_path)
        lookback = 3 * TRADING_DAYS_PER_MONTH + 1
        write_prices(cache, "AAA", list(np.linspace(100, 150, lookback)), AS_OF)

        kr_store = ParquetDataStore(cache, "KR")  # data exists only under US
        result = MomentumFactor(3).compute(AS_OF, ["AAA"], kr_store)
        assert np.isnan(result.loc["AAA"])

    def test_empty_universe_returns_empty_series(self, store):
        result = MomentumFactor(3).compute(AS_OF, [], store)
        assert result.empty
        assert result.name == "momentum_3m"

    def test_mixed_universe_scores_available_symbols_only(self, store):
        lookback = 3 * TRADING_DAYS_PER_MONTH + 1
        write_prices(store.cache, "AAA", list(np.linspace(100, 150, lookback)), AS_OF)
        result = MomentumFactor(3).compute(AS_OF, ["aaa", "MISSING"], store)
        assert result.loc["AAA"] == pytest.approx(0.5)
        assert np.isnan(result.loc["MISSING"])

    def test_weekend_computation_date_uses_last_close(self, store):
        lookback = 3 * TRADING_DAYS_PER_MONTH + 1
        closes = list(np.linspace(100.0, 150.0, lookback))
        write_prices(store.cache, "AAA", closes, date(2020, 6, 26))  # Friday

        weekend = date(2020, 6, 28)  # Sunday
        result = MomentumFactor(3).compute(weekend, ["AAA"], store)
        assert result.loc["AAA"] == pytest.approx(0.5)

    def test_invalid_parameters_raise(self):
        with pytest.raises(ValueError):
            MomentumFactor(0)
        with pytest.raises(ValueError):
            MomentumFactor(3, skip_months=-1)


class TestFactorRegistry:
    def test_default_momentum_factors_registered(self):
        names = list_factors()
        for expected in ["momentum_3m", "momentum_6m", "momentum_12m", "momentum_12m_ex1m"]:
            assert expected in names

    def test_get_factor_builds_fresh_instances(self):
        first = get_factor("momentum_3m")
        second = get_factor("momentum_3m")
        assert first is not second
        assert first.name == "momentum_3m"

    def test_unknown_factor_lists_available(self):
        with pytest.raises(ValueError, match="Available:"):
            get_factor("nope")

    def test_duplicate_registration_rejected(self):
        with pytest.raises(ValueError, match="already registered"):
            register_factor("momentum_3m", lambda: MomentumFactor(3))
