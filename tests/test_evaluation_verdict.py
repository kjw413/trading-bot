from __future__ import annotations

import pytest

from tradingbot.research.evaluation import judge

PROMOTION = {
    "min_excess_return": 0.0,
    "min_sharpe": 0.5,
    "max_mdd": 0.25,
    "max_annual_turnover": 6.0,
    "min_walk_forward_win_rate": 0.6,
    "min_walk_forward_windows": 3,
    "cost_multiplier_check": 2.0,
}

PASSING = {
    "excess_return": 2.0,
    "sharpe": 1.2,
    "mdd": 0.18,
    "turnover": 3.0,
    "wf_win_rate": 0.7,
    "wf_windows_evaluated": 3,
    "excess_return_2x": 1.0,
}


def verdict(**overrides):
    values = {**PASSING, **overrides}
    return judge(promotion=PROMOTION, **values)


class TestJudge:
    def test_all_criteria_met_is_promoted(self):
        result = verdict()
        assert result.promoted is True
        assert all(criterion.passed for criterion in result.criteria)

    def test_reports_all_six_criteria(self):
        assert len(verdict().criteria) == 6

    def test_negative_excess_return_blocks_promotion(self):
        result = verdict(excess_return=-1.0)
        assert result.promoted is False
        assert any(c.name == "excess_return" and c.passed is False for c in result.criteria)

    def test_low_sharpe_blocks(self):
        assert verdict(sharpe=0.4).promoted is False

    def test_deep_drawdown_blocks(self):
        assert verdict(mdd=0.33).promoted is False

    def test_excessive_turnover_blocks(self):
        assert verdict(turnover=7.0).promoted is False

    def test_low_win_rate_blocks(self):
        assert verdict(wf_win_rate=0.4).promoted is False

    def test_failing_the_doubled_cost_check_blocks(self):
        assert verdict(excess_return_2x=-0.5).promoted is False

    def test_unmeasured_criterion_is_not_a_pass(self):
        # The whole point of this tool: NaN must never read as "passed".
        result = verdict(wf_win_rate=float("nan"))
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None
        assert result.promoted is False

    def test_unmeasured_criteria_are_listed(self):
        result = verdict(turnover=float("nan"), wf_win_rate=float("nan"))
        assert set(result.unmeasured) == {"annual_turnover", "walk_forward_win_rate"}

    def test_no_unmeasured_when_everything_measured(self):
        assert verdict().unmeasured == []

    def test_boundary_values_pass(self):
        # Thresholds are inclusive: exactly at the limit is acceptable.
        result = verdict(sharpe=0.5, mdd=0.25, turnover=6.0, wf_win_rate=0.6, excess_return=0.0)
        assert result.promoted is True


class TestWalkForwardWindowCount:
    """A win rate from too few windows says nothing about consistency.

    The Korean strategy's data starts in 2023, so `walk_forward_windows`
    yields exactly one window; without this check, winning that single
    7-month sample would read as a passed consistency criterion.
    """

    def test_a_single_window_is_not_a_pass_even_at_a_perfect_rate(self):
        result = verdict(wf_windows_evaluated=1, wf_win_rate=1.0)
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None
        assert result.promoted is False

    def test_exactly_the_minimum_with_a_passing_rate_is_a_pass(self):
        result = verdict(wf_windows_evaluated=3, wf_win_rate=0.7)
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is True

    def test_one_below_the_minimum_is_not_a_pass(self):
        result = verdict(wf_windows_evaluated=2, wf_win_rate=1.0)
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None

    def test_a_low_rate_still_fails_once_there_are_enough_windows(self):
        # Above the evidence floor, a bad rate is a normal miss, not "unmeasured".
        result = verdict(wf_windows_evaluated=5, wf_win_rate=0.2)
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is False

    def test_missing_threshold_in_config_defaults_to_three(self):
        promotion = {k: v for k, v in PROMOTION.items() if k != "min_walk_forward_windows"}
        result = judge(promotion=promotion, **{**PASSING, "wf_windows_evaluated": 2})
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None

    def test_custom_threshold_from_config_is_honored(self):
        promotion = {**PROMOTION, "min_walk_forward_windows": 5}
        result = judge(promotion=promotion, **{**PASSING, "wf_windows_evaluated": 4})
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None

    def test_reason_says_too_few_windows_not_a_crash(self):
        # The reader must be able to tell "not enough evidence" apart from
        # "the backtest failed" — both currently render as unmeasured.
        result = verdict(wf_windows_evaluated=1, wf_win_rate=1.0)
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert "구간" in criterion.reason

    def test_nan_rate_with_enough_windows_is_still_unmeasured(self):
        # NaN can only arise with zero evaluated windows, which is already
        # below any sane threshold, but the existing NaN route must still work.
        result = verdict(wf_windows_evaluated=0, wf_win_rate=float("nan"))
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None
