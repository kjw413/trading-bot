from __future__ import annotations

import math

import pandas as pd
import pytest

from tradingbot.allocation.weights import (
    equal_weights,
    inverse_volatility_weights,
    realized_volatility,
    scale_weights,
)


class TestEqualWeights:
    def test_splits_evenly(self):
        assert equal_weights(["AAA", "BBB"]) == {"AAA": 0.5, "BBB": 0.5}

    def test_empty_is_empty(self):
        assert equal_weights([]) == {}


class TestRealizedVolatility:
    def test_constant_prices_have_zero_volatility(self):
        closes = pd.Series([100.0] * 30)
        assert realized_volatility(closes, 20) == pytest.approx(0.0)

    def test_wilder_swings_mean_higher_volatility(self):
        calm = pd.Series([100.0 + (i % 2) * 0.1 for i in range(30)])
        wild = pd.Series([100.0 + (i % 2) * 10.0 for i in range(30)])
        assert realized_volatility(wild, 20) > realized_volatility(calm, 20)

    def test_insufficient_history_is_nan(self):
        assert math.isnan(realized_volatility(pd.Series([100.0] * 5), 20))

    def test_invalid_days_rejected(self):
        with pytest.raises(ValueError):
            realized_volatility(pd.Series([100.0] * 30), 0)


class TestInverseVolatilityWeights:
    def test_lower_volatility_gets_more_weight(self):
        result = inverse_volatility_weights({"CALM": 0.01, "WILD": 0.04})
        assert result["CALM"] == pytest.approx(0.8)
        assert result["WILD"] == pytest.approx(0.2)

    def test_weights_sum_to_one(self):
        result = inverse_volatility_weights({"A": 0.02, "B": 0.03, "C": 0.05})
        assert sum(result.values()) == pytest.approx(1.0)

    def test_nan_volatility_symbol_is_excluded(self):
        result = inverse_volatility_weights({"A": 0.02, "B": float("nan")})
        assert set(result) == {"A"}
        assert result["A"] == pytest.approx(1.0)

    def test_zero_volatility_symbol_is_excluded_not_infinite(self):
        result = inverse_volatility_weights({"A": 0.02, "B": 0.0})
        assert set(result) == {"A"}

    def test_no_valid_volatility_falls_back_to_equal(self):
        # A brand-new theme member with short history must not sink the whole
        # rebalance — fall back to equal weight rather than empty.
        result = inverse_volatility_weights({"A": float("nan"), "B": float("nan")})
        assert result == {"A": 0.5, "B": 0.5}

    def test_empty_is_empty(self):
        assert inverse_volatility_weights({}) == {}


class TestScaleWeights:
    def test_scales_every_weight(self):
        assert scale_weights({"A": 0.6, "B": 0.4}, 0.5) == {"A": 0.3, "B": 0.2}

    def test_negative_factor_rejected(self):
        with pytest.raises(ValueError):
            scale_weights({"A": 1.0}, -0.1)
