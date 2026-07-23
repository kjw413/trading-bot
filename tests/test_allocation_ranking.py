from __future__ import annotations

import pandas as pd

from tradingbot.allocation.ranking import select_top


class TestSelectTop:
    def test_takes_highest_scores(self):
        scores = pd.Series({"AAA": 1.0, "BBB": 3.0, "CCC": 2.0})
        assert select_top(scores, 2) == ["BBB", "CCC"]

    def test_nan_scores_are_excluded(self):
        scores = pd.Series({"AAA": 1.0, "BBB": float("nan"), "CCC": 2.0})
        assert select_top(scores, 3) == ["CCC", "AAA"]

    def test_ties_break_by_symbol_for_reproducibility(self):
        scores = pd.Series({"BBB": 1.0, "AAA": 1.0, "CCC": 2.0})
        assert select_top(scores, 2) == ["CCC", "AAA"]

    def test_fewer_valid_than_top_n_returns_what_exists(self):
        scores = pd.Series({"AAA": 1.0, "BBB": float("nan")})
        assert select_top(scores, 5) == ["AAA"]

    def test_all_nan_returns_empty(self):
        scores = pd.Series({"AAA": float("nan")})
        assert select_top(scores, 3) == []

    def test_empty_scores_returns_empty(self):
        assert select_top(pd.Series(dtype=float), 3) == []

    def test_non_positive_top_n_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            select_top(pd.Series({"AAA": 1.0}), 0)
