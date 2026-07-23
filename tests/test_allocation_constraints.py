from __future__ import annotations

import pytest

from tradingbot.allocation.constraints import apply_constraints


class TestApplyConstraints:
    def test_caps_a_single_oversized_weight(self):
        result = apply_constraints({"A": 0.6, "B": 0.2}, max_weight=0.4, cash_buffer=0.0)
        assert result["A"] == pytest.approx(0.4)
        assert result["B"] == pytest.approx(0.2)

    def test_capped_excess_goes_to_cash_not_other_symbols(self):
        result = apply_constraints({"A": 0.8, "B": 0.2}, max_weight=0.4, cash_buffer=0.0)
        # B must not absorb A's excess — concentration limits exist to cap
        # risk, and redistribution would just move the concentration.
        assert result["B"] == pytest.approx(0.2)
        assert sum(result.values()) == pytest.approx(0.6)

    def test_total_scaled_down_to_respect_cash_buffer(self):
        result = apply_constraints({"A": 0.5, "B": 0.5}, max_weight=1.0, cash_buffer=0.1)
        assert sum(result.values()) == pytest.approx(0.9)
        assert result["A"] == pytest.approx(0.45)

    def test_within_limits_passes_through(self):
        original = {"A": 0.3, "B": 0.3}
        assert apply_constraints(original, max_weight=0.4, cash_buffer=0.1) == pytest.approx(original)

    def test_empty_weights(self):
        assert apply_constraints({}, max_weight=0.4, cash_buffer=0.1) == {}

    def test_invalid_limits_rejected(self):
        with pytest.raises(ValueError):
            apply_constraints({"A": 0.5}, max_weight=0.0, cash_buffer=0.1)
        with pytest.raises(ValueError):
            apply_constraints({"A": 0.5}, max_weight=0.4, cash_buffer=1.0)
