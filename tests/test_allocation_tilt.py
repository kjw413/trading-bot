"""Rank tilt: hold the whole basket, lean toward the better-ranked names.

Selecting the top few throws away diversification and only pays for it when
the signal is strong. The US review measured what happens when it isn't —
the strategy trailed an equal-weight basket of its own universe. These tests
pin the two endpoints (0.0 is the benchmark, 1.0 is maximum lean) and the
properties that make the middle safe.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.allocation.weights import rank_tilt_weights


def scores(**kwargs) -> pd.Series:
    return pd.Series(kwargs, dtype=float)


class TestEndpoints:
    def test_zero_strength_is_equal_weight(self):
        """The benchmark itself — the signal is ignored entirely."""
        weights = rank_tilt_weights(scores(A=3.0, B=1.0, C=-2.0), 0.0)

        assert set(weights) == {"A", "B", "C"}
        for weight in weights.values():
            assert weight == pytest.approx(1 / 3)

    def test_full_strength_zeroes_the_worst_and_doubles_the_best(self):
        weights = rank_tilt_weights(scores(A=3.0, B=1.0, C=-2.0), 1.0)

        assert weights["C"] == pytest.approx(0.0)
        assert weights["A"] == pytest.approx(2 / 3)
        assert weights["B"] == pytest.approx(1 / 3)

    def test_weights_always_sum_to_one(self):
        for strength in (0.0, 0.25, 0.5, 0.75, 1.0):
            weights = rank_tilt_weights(scores(A=3.0, B=1.0, C=-2.0, D=0.5), strength)
            assert sum(weights.values()) == pytest.approx(1.0)


class TestOrdering:
    def test_better_score_never_gets_less_weight(self):
        weights = rank_tilt_weights(scores(A=5.0, B=2.0, C=1.0, D=-1.0), 0.6)

        assert weights["A"] > weights["B"] > weights["C"] > weights["D"]

    def test_stronger_tilt_concentrates_more(self):
        gentle = rank_tilt_weights(scores(A=5.0, B=2.0, C=-1.0), 0.2)
        firm = rank_tilt_weights(scores(A=5.0, B=2.0, C=-1.0), 0.9)

        assert firm["A"] > gentle["A"]
        assert firm["C"] < gentle["C"]


class TestRobustness:
    def test_an_extreme_score_cannot_take_over_the_portfolio(self):
        """Ranks, not raw scores — one outlier must not become the portfolio."""
        weights = rank_tilt_weights(scores(A=1e9, B=2.0, C=1.0), 1.0)

        assert weights["A"] == pytest.approx(2 / 3)  # same as any top-of-3

    def test_ties_share_weight_rather_than_breaking_on_symbol_order(self):
        weights = rank_tilt_weights(scores(A=1.0, B=1.0, C=-5.0), 1.0)

        assert weights["A"] == pytest.approx(weights["B"])

    def test_unscoreable_names_are_excluded(self):
        weights = rank_tilt_weights(scores(A=2.0, B=float("nan"), C=1.0), 0.5)

        assert set(weights) == {"A", "C"}
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_single_name_takes_everything(self):
        assert rank_tilt_weights(scores(A=1.0), 1.0) == {"A": 1.0}

    def test_all_nan_returns_empty(self):
        assert rank_tilt_weights(scores(A=float("nan")), 0.5) == {}

    def test_empty_returns_empty(self):
        assert rank_tilt_weights(pd.Series(dtype=float), 0.5) == {}

    def test_identical_scores_give_equal_weight_at_any_strength(self):
        weights = rank_tilt_weights(scores(A=1.0, B=1.0, C=1.0), 1.0)

        for weight in weights.values():
            assert weight == pytest.approx(1 / 3)

    @pytest.mark.parametrize("strength", [-0.1, 1.1])
    def test_strength_outside_the_unit_interval_is_rejected(self, strength):
        with pytest.raises(ValueError, match=r"strength must be in \[0, 1\]"):
            rank_tilt_weights(scores(A=1.0, B=2.0), strength)


class TestAgainstSelection:
    def test_tilt_keeps_names_that_top_n_would_have_dropped(self):
        """The whole point: a weak signal costs weight, not the position."""
        from tradingbot.allocation.ranking import select_top

        universe = scores(A=3.0, B=2.0, C=1.0, D=0.0, E=-1.0)

        selected = select_top(universe, 2)
        tilted = rank_tilt_weights(universe, 0.5)

        assert set(selected) == {"A", "B"}
        assert set(tilted) == {"A", "B", "C", "D", "E"}
        assert all(weight > 0 for weight in tilted.values())
