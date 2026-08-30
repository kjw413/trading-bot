from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

import tradingbot.factors.momentum as momentum_module
from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors import get_factor, list_factors
from tradingbot.factors.momentum import (
    RISK_ADJUSTED_VOLATILITY_DAYS,
    TRADING_DAYS_PER_MONTH,
    MomentumFactor,
    ReversalFactor,
    RiskAdjustedMomentumFactor,
)
from tradingbot.factors.quality import (
    TREND_CONSISTENCY_DAYS,
    TREND_RETURN_DAYS,
    TrendConsistencyFactor,
)

AS_OF = date(2020, 6, 30)
NEXT_BAR = (pd.Timestamp(AS_OF) + pd.tseries.offsets.BDay()).date()


def write_prices(cache: ParquetCache, symbol: str, closes: list[float], end: date) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [close * 1.01 for close in closes],
            "low": [close * 0.99 for close in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        },
        index=index,
    )
    cache.write("US", symbol, frame)


def prices_from_returns(returns: list[float], start: float = 100.0) -> list[float]:
    prices = [start]
    for daily_return in returns:
        prices.append(prices[-1] * (1.0 + daily_return))
    return prices


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path), "US")


class TestRiskAdjustedMomentumFactor:
    def test_known_return_and_volatility(self, store):
        returns = [0.012 if day % 3 == 0 else -0.003 for day in range(126)]
        closes = prices_from_returns(returns)
        write_prices(store.cache, "AAA", closes, AS_OF)

        expected_momentum = float(np.prod(1.0 + np.asarray(returns)) - 1.0)
        expected_volatility = float(np.std(returns[-RISK_ADJUSTED_VOLATILITY_DAYS:], ddof=0))
        result = RiskAdjustedMomentumFactor().compute(AS_OF, ["AAA"], store)

        assert result.name == "momentum_6m_risk_adj"
        assert result.loc["AAA"] == pytest.approx(expected_momentum / expected_volatility)

    @pytest.mark.parametrize("volatility", [0.0, float("nan")])
    def test_zero_or_nan_volatility_is_nan(self, store, monkeypatch, volatility):
        closes = list(np.linspace(100.0, 150.0, 6 * TRADING_DAYS_PER_MONTH + 1))
        write_prices(store.cache, "AAA", closes, AS_OF)
        monkeypatch.setattr(
            momentum_module,
            "realized_volatility",
            lambda _closes, _days: volatility,
        )

        result = RiskAdjustedMomentumFactor().compute(AS_OF, ["AAA"], store)

        assert np.isnan(result.loc["AAA"])

    def test_higher_score_is_the_better_oriented_path(self, store):
        up = [0.012 if day % 2 == 0 else -0.002 for day in range(126)]
        down = [-0.012 if day % 2 == 0 else 0.002 for day in range(126)]
        write_prices(store.cache, "UP", prices_from_returns(up), AS_OF)
        write_prices(store.cache, "DOWN", prices_from_returns(down), AS_OF)

        result = RiskAdjustedMomentumFactor().compute(AS_OF, ["UP", "DOWN"], store)

        assert result.loc["UP"] > result.loc["DOWN"]

    def test_next_bar_cannot_enter_trailing_window(self, store):
        returns = [0.012 if day % 3 == 0 else -0.003 for day in range(126)]
        closes = prices_from_returns(returns)
        write_prices(store.cache, "AAA", closes + [1_000_000.0], NEXT_BAR)
        expected_momentum = float(np.prod(1.0 + np.asarray(returns)) - 1.0)
        expected_volatility = float(np.std(returns[-RISK_ADJUSTED_VOLATILITY_DAYS:], ddof=0))

        result = RiskAdjustedMomentumFactor().compute(AS_OF, ["AAA"], store)

        assert result.loc["AAA"] == pytest.approx(expected_momentum / expected_volatility)


class TestTrendConsistencyFactor:
    def test_monotonic_paths_have_known_answers_and_direction(self, store):
        lookback = TREND_CONSISTENCY_DAYS + TREND_RETURN_DAYS
        rising = list(np.linspace(100.0, 200.0, lookback))
        falling = list(np.linspace(200.0, 100.0, lookback))
        write_prices(store.cache, "UP", rising, AS_OF)
        write_prices(store.cache, "DOWN", falling, AS_OF)

        result = TrendConsistencyFactor().compute(AS_OF, ["UP", "DOWN"], store)

        assert result.name == "trend_consistency_6m"
        assert result.loc["UP"] == pytest.approx(1.0)
        assert result.loc["DOWN"] == pytest.approx(0.0)
        assert result.loc["UP"] > result.loc["DOWN"]

    def test_missing_one_required_close_is_nan(self, store):
        lookback = TREND_CONSISTENCY_DAYS + TREND_RETURN_DAYS
        write_prices(store.cache, "AAA", list(np.linspace(100.0, 200.0, lookback - 1)), AS_OF)

        result = TrendConsistencyFactor().compute(AS_OF, ["AAA"], store)

        assert np.isnan(result.loc["AAA"])

    def test_next_bar_cannot_enter_trailing_window(self, store):
        lookback = TREND_CONSISTENCY_DAYS + TREND_RETURN_DAYS
        rising = list(np.linspace(100.0, 200.0, lookback))
        write_prices(store.cache, "AAA", rising + [0.01], NEXT_BAR)

        result = TrendConsistencyFactor().compute(AS_OF, ["AAA"], store)

        assert result.loc["AAA"] == pytest.approx(1.0)


class TestReversalFactor:
    def test_is_exact_negative_of_one_month_momentum_and_direction(self, store):
        lookback = TRADING_DAYS_PER_MONTH + 1
        rising = list(np.linspace(100.0, 121.0, lookback))
        falling = list(np.linspace(100.0, 79.0, lookback))
        write_prices(store.cache, "UP", rising, AS_OF)
        write_prices(store.cache, "DOWN", falling, AS_OF)

        momentum = MomentumFactor(1).compute(AS_OF, ["UP", "DOWN"], store)
        reversal = ReversalFactor().compute(AS_OF, ["UP", "DOWN"], store)

        assert reversal.name == "reversal_1m"
        assert reversal.loc["UP"] == pytest.approx(-momentum.loc["UP"])
        assert reversal.loc["DOWN"] == pytest.approx(-momentum.loc["DOWN"])
        assert reversal.loc["DOWN"] > reversal.loc["UP"]

    def test_next_bar_cannot_enter_trailing_window(self, store):
        lookback = TRADING_DAYS_PER_MONTH + 1
        rising = list(np.linspace(100.0, 121.0, lookback))
        write_prices(store.cache, "AAA", rising + [1_000_000.0], NEXT_BAR)

        result = ReversalFactor().compute(AS_OF, ["AAA"], store)

        assert result.loc["AAA"] == pytest.approx(-(121.0 / 100.0 - 1.0))


@pytest.mark.parametrize(
    ("name", "factor_type"),
    [
        ("momentum_6m_risk_adj", RiskAdjustedMomentumFactor),
        ("trend_consistency_6m", TrendConsistencyFactor),
        ("reversal_1m", ReversalFactor),
    ],
)
def test_p2_factors_are_registered(name, factor_type):
    assert name in list_factors()
    assert isinstance(get_factor(name), factor_type)
