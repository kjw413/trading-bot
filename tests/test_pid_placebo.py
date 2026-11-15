from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.research.pid_placebo import (
    decide_against_placebo,
    exceedance_p_value,
    placebo_prices,
    run_placebo,
)
from tradingbot.research.pid_sweep import ExecutionModel, SelectionRule
from tradingbot.research.walk_forward import walk_forward_windows
from tradingbot.strategies.pid_reversion import PidParams

EXECUTION = ExecutionModel(
    initial_cash=100_000, slippage_bps=5, commission_rate=0.0, sec_fee_rate=0.0000278,
    taf_per_share=0.000166, taf_cap=8.30, min_cash_buffer_pct=0.02, max_position_pct=1.0,
)


def _prices(n: int = 900, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 80 * np.exp(np.cumsum(0.0005 + 0.02 * rng.standard_normal(n)))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * np.exp(0.005 * rng.standard_normal(n))
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
         "close": closes, "volume": 1e6},
        index=pd.bdate_range("2004-01-01", periods=n),
    )


def _pairs(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    opens, closes = frame["open"].to_numpy(), frame["close"].to_numpy()
    return np.log(opens[1:] / closes[:-1]), np.log(closes[1:] / opens[1:])


def test_sign_flip_keeps_each_days_move_on_its_date_and_only_changes_direction():
    prices = _prices()
    real_gap, real_day = _pairs(prices)

    fake_gap, fake_day = _pairs(placebo_prices(prices, "sign_flip", seed=1))

    np.testing.assert_allclose(np.abs(fake_gap - real_gap.mean()), np.abs(real_gap - real_gap.mean()), atol=1e-12)
    np.testing.assert_allclose(np.abs(fake_day - real_day.mean()), np.abs(real_day - real_day.mean()), atol=1e-12)
    flipped = np.sign(fake_day - real_day.mean()) != np.sign(real_day - real_day.mean())
    assert 0.4 < flipped.mean() < 0.6
    # Gap and session flip together: the pair is one day.
    same = np.sign((fake_gap - real_gap.mean()) * (real_gap - real_gap.mean())) == np.sign(
        (fake_day - real_day.mean()) * (real_day - real_day.mean())
    )
    assert same.all()


def test_shuffle_keeps_the_same_days_in_a_different_order():
    prices = _prices()
    real_gap, real_day = _pairs(prices)

    fake_gap, fake_day = _pairs(placebo_prices(prices, "shuffle", seed=1))

    np.testing.assert_allclose(np.sort(fake_gap + fake_day), np.sort(real_gap + real_day), atol=1e-12)
    assert not np.allclose(fake_day, real_day)
    # Same days, so the same end point.
    fake = placebo_prices(prices, "shuffle", seed=1)
    assert fake["close"].iloc[-1] / fake["close"].iloc[0] == pytest.approx(
        prices["close"].iloc[-1] / prices["close"].iloc[0]
    )


def test_placebo_is_reproducible_and_keeps_the_real_dates():
    prices = _prices()

    first = placebo_prices(prices, "sign_flip", seed=3)

    pd.testing.assert_frame_equal(first, placebo_prices(prices, "sign_flip", seed=3))
    assert not first["close"].equals(placebo_prices(prices, "sign_flip", seed=4)["close"])
    assert first.index.equals(prices.index)
    assert (first[["open", "close"]] > 0).all().all()


def test_unknown_placebo_kind_is_refused():
    with pytest.raises(ValueError, match="Unknown placebo kind"):
        placebo_prices(_prices(), "bootstrap", seed=0)


def test_p_value_counts_how_often_the_placebo_did_as_well():
    assert exceedance_p_value(0.5, [0.1, 0.2, 0.6, 0.7]) == pytest.approx(3 / 5)
    assert exceedance_p_value(0.9, [0.1, 0.2, 0.6, 0.7]) == pytest.approx(1 / 5)  # never exactly zero
    assert exceedance_p_value(0.5, [0.1, float("nan"), 0.6]) == pytest.approx(2 / 4)  # NaN does not exceed
    assert np.isnan(exceedance_p_value(float("nan"), [0.1]))
    assert np.isnan(exceedance_p_value(0.5, []))


def _placebo_frame(wf, val, verdict="consistent"):
    return pd.DataFrame({"wf_mean_alpha": wf, "validation_alpha": val, "verdict": verdict})


def test_a_statistic_inside_the_placebo_range_is_rejected_however_large():
    placebo = _placebo_frame(np.linspace(0.02, 0.07, 99), np.linspace(-0.02, 0.06, 99))

    verdict, _, numbers = decide_against_placebo({"wf_mean_alpha": 0.048, "validation_alpha": 0.045}, placebo)

    assert verdict == "rejected"
    assert numbers["p_walk_forward"] > 0.05
    assert numbers["placebo_original_rule_pass_share"] == 1.0


def test_beyond_the_placebo_and_above_its_validation_median_is_consistent():
    placebo = _placebo_frame(np.linspace(0.0, 0.03, 99), np.linspace(-0.02, 0.04, 99))

    assert decide_against_placebo({"wf_mean_alpha": 0.05, "validation_alpha": 0.03}, placebo)[0] == "consistent"
    assert decide_against_placebo({"wf_mean_alpha": 0.05, "validation_alpha": 0.00}, placebo)[0] == "rejected"


def test_unmeasured_real_statistic_gives_no_verdict():
    placebo = _placebo_frame([0.01, 0.02], [0.0, 0.01])

    assert decide_against_placebo({"wf_mean_alpha": float("nan"), "validation_alpha": 0.01}, placebo)[0] == "unmeasured"


def test_placebo_runs_the_whole_search_and_never_reads_past_validation():
    prices = _prices(1500)
    in_sample = (date(2005, 1, 3), date(2007, 12, 31))
    validation = (date(2008, 1, 1), date(2008, 12, 31))
    windows = walk_forward_windows(in_sample[0], validation[1], train_years=2, test_years=1, step_years=1)
    grid = {
        "trend_days": (60,), "integral_days": (20,), "kp": (0.0, 0.2), "ki": (0.0, 0.2),
        "kd": (0.0, 0.2), "base_weight": (0.5,),
    }
    kwargs = dict(
        kind="sign_flip", seeds=[0, 1], in_sample=in_sample, validation=validation,
        walk_forward_windows_=windows, grid=grid, base=PidParams(band=0.05),
        execution=EXECUTION, rule=SelectionRule(min_fills=3),
    )
    poisoned = prices.copy()
    poisoned.loc[poisoned.index > pd.Timestamp(validation[1]), ["open", "close"]] *= 7

    runs = run_placebo(prices, **kwargs)

    assert list(runs["seed"]) == [0, 1]
    assert runs["wf_mean_alpha"].notna().all()
    pd.testing.assert_frame_equal(runs, run_placebo(poisoned, **kwargs))
    pd.testing.assert_frame_equal(runs, run_placebo(prices, **{**kwargs, "processes": 2}))
