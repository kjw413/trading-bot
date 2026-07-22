from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.factors.transform import combine, standardize, winsorize, zscore


class TestWinsorize:
    def test_clips_extremes_to_the_boundary(self):
        values = pd.Series({f"S{i}": float(i) for i in range(100)})
        values.loc["OUTLIER"] = 1e9
        clipped = winsorize(values, limit=0.05)
        assert clipped.max() < 1e9
        assert clipped.loc["OUTLIER"] == clipped.drop("OUTLIER").max()

    def test_preserves_the_middle(self):
        values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})
        clipped = winsorize(values, limit=0.0)
        pd.testing.assert_series_equal(clipped, values)

    def test_nan_is_preserved_not_clipped(self):
        values = pd.Series({"A": 1.0, "B": float("nan"), "C": 3.0})
        assert np.isnan(winsorize(values).loc["B"])

    def test_all_nan_returns_all_nan(self):
        values = pd.Series({"A": float("nan")})
        assert np.isnan(winsorize(values).loc["A"])

    def test_invalid_limit_rejected(self):
        with pytest.raises(ValueError):
            winsorize(pd.Series({"A": 1.0}), limit=0.6)


class TestZscore:
    def test_centers_and_scales(self):
        values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})
        scored = zscore(values)
        assert scored.mean() == pytest.approx(0.0)
        assert scored.loc["B"] == pytest.approx(0.0)
        assert scored.loc["C"] > 0

    def test_constant_input_is_all_zero_not_nan(self):
        # Every name is equally (un)attractive; zero is the honest score.
        scored = zscore(pd.Series({"A": 5.0, "B": 5.0}))
        assert (scored == 0).all()

    def test_nan_stays_nan_and_is_excluded_from_stats(self):
        values = pd.Series({"A": 1.0, "B": 3.0, "C": float("nan")})
        scored = zscore(values)
        assert np.isnan(scored.loc["C"])
        assert scored.loc["A"] == pytest.approx(-scored.loc["B"])

    def test_single_value_is_zero(self):
        assert zscore(pd.Series({"A": 7.0})).loc["A"] == pytest.approx(0.0)

    def test_empty_series(self):
        assert zscore(pd.Series(dtype=float)).empty


class TestStandardize:
    def test_applies_winsorize_then_zscore(self):
        values = pd.Series({f"S{i}": float(i) for i in range(50)})
        values.loc["OUTLIER"] = 1e9
        scored = standardize(values, limit=0.05)
        # Without winsorizing, the outlier would compress everything else to ~0.
        assert scored.loc["OUTLIER"] < 10
        assert scored.std() == pytest.approx(1.0, rel=0.2)


class TestCombine:
    def test_weighted_average_of_standardized_scores(self):
        scores = {
            "a": pd.Series({"X": 1.0, "Y": -1.0}),
            "b": pd.Series({"X": -1.0, "Y": 1.0}),
        }
        combined = combine(scores, {"a": 0.75, "b": 0.25})
        assert combined.loc["X"] == pytest.approx(0.5)
        assert combined.loc["Y"] == pytest.approx(-0.5)

    def test_weights_are_renormalized_over_present_factors(self):
        # Y is missing factor b; its score must use a alone, not be dragged
        # toward zero by treating the missing factor as 0.
        scores = {
            "a": pd.Series({"X": 1.0, "Y": 2.0}),
            "b": pd.Series({"X": 1.0, "Y": float("nan")}),
        }
        combined = combine(scores, {"a": 0.5, "b": 0.5})
        assert combined.loc["Y"] == pytest.approx(2.0)
        assert combined.loc["X"] == pytest.approx(1.0)

    def test_symbol_below_min_factors_is_nan(self):
        scores = {
            "a": pd.Series({"X": 1.0, "Y": 1.0}),
            "b": pd.Series({"X": 1.0, "Y": float("nan")}),
        }
        combined = combine(scores, {"a": 0.5, "b": 0.5}, min_factors=2)
        assert np.isnan(combined.loc["Y"])
        assert combined.loc["X"] == pytest.approx(1.0)

    def test_unknown_weight_key_rejected(self):
        with pytest.raises(ValueError, match="weight"):
            combine({"a": pd.Series({"X": 1.0})}, {"nope": 1.0})

    def test_zero_total_weight_rejected(self):
        with pytest.raises(ValueError):
            combine({"a": pd.Series({"X": 1.0})}, {"a": 0.0})

    def test_empty_scores_returns_empty(self):
        assert combine({}, {}).empty

    def test_union_of_symbols_is_covered(self):
        scores = {"a": pd.Series({"X": 1.0}), "b": pd.Series({"Y": 1.0})}
        combined = combine(scores, {"a": 0.5, "b": 0.5})
        assert set(combined.index) == {"X", "Y"}
