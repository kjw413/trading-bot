from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.risk import RiskLimits, RiskManager
from tradingbot.strategies.pid_reversion import (
    PidParams,
    PidReversionStrategy,
    pid_terms,
    target_weights,
    trend_zscore,
)
from tradingbot.strategies.registry import get_strategy


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2015-01-01", periods=n)


def _trend(n: int, *, drift: float = 0.0005, noise: float = 0.01, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(drift + noise * rng.standard_normal(n))), index=_dates(n))


def test_a_price_exactly_on_its_trend_has_no_error():
    closes = pd.Series(100 * np.exp(0.001 * np.arange(120)), index=_dates(120))

    z = trend_zscore(closes, trend_days=60, z_cap=4.0)

    assert z.iloc[:60].isna().all()
    assert z.iloc[60:].abs().max() < 1e-3


def test_a_jump_off_a_quiet_trend_reads_as_a_capped_positive_error():
    closes = _trend(200, noise=0.002)
    closes.iloc[-1] *= 1.10

    z = trend_zscore(closes, trend_days=120, z_cap=4.0)

    assert z.iloc[-1] == pytest.approx(4.0)


def test_error_at_a_date_does_not_change_when_later_prices_arrive():
    closes = _trend(400)
    early = target_weights(closes.iloc[:300], PidParams(trend_days=120, integral_days=40))
    late = target_weights(closes, PidParams(trend_days=120, integral_days=40))

    pd.testing.assert_frame_equal(early, late.iloc[:300])


def test_todays_close_does_not_enter_its_own_trend_line():
    closes = _trend(200)
    moved = closes.copy()
    moved.iloc[-1] *= 1.05
    # Only the last row may differ: if the fit used today's close, the line
    # itself would move and the residual would not equal the full 5% jump.
    base = trend_zscore(closes, 120, 100.0)
    bumped = trend_zscore(moved, 120, 100.0)

    pd.testing.assert_series_equal(base.iloc[:-1], bumped.iloc[:-1])
    assert bumped.iloc[-1] > base.iloc[-1]


def test_terms_are_level_average_and_change():
    z = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0])

    terms = pid_terms(z, integral_days=3, derivative_days=2)

    assert terms["p"].tolist() == z.tolist()
    assert terms["i"].iloc[-1] == pytest.approx(3.0)
    assert terms["d"].iloc[-1] == pytest.approx(2.0)
    assert terms["i"].iloc[:2].isna().all()


def test_overshoot_on_a_quiet_trend_cuts_the_target():
    closes = _trend(400, noise=0.003)
    params = PidParams(trend_days=120, integral_days=60, kp=0.2, ki=0.0, kd=0.2, base_weight=0.75)
    calm = target_weights(closes, params)["target"].iloc[-1]
    spiked = closes.copy()
    spiked.iloc[-1] *= 1.08

    after = target_weights(spiked, params)["target"].iloc[-1]

    assert after < calm
    assert after == pytest.approx(0.0)  # P and D both at the cap: 0.75 - 0.2*4 - 0.2*(4 - z_before)


def test_a_bounce_inside_a_long_slump_is_held_through_by_the_integral():
    # A slow decline below an older, rising trend, then one sharp up day.
    rising = 100 * np.exp(0.001 * np.arange(300))
    slump = rising[-1] * np.exp(-0.002 * np.arange(1, 121))
    rng = np.random.default_rng(3)
    values = np.concatenate([rising, slump]) * np.exp(0.004 * rng.standard_normal(420))
    values[-1] *= 1.04
    closes = pd.Series(values, index=_dates(420))

    without_i = PidParams(trend_days=250, integral_days=60, kp=0.1, ki=0.0, kd=0.3, base_weight=0.5)
    with_i = PidParams(trend_days=250, integral_days=60, kp=0.1, ki=0.3, kd=0.3, base_weight=0.5)
    frame = target_weights(closes, with_i)

    assert frame["i"].iloc[-1] < 0 < frame["d"].iloc[-1]
    assert frame["target"].iloc[-1] > target_weights(closes, without_i)["target"].iloc[-1]


def test_a_term_with_zero_gain_does_not_delay_the_first_target():
    closes = _trend(140)
    p_only = PidParams(trend_days=120, integral_days=100, kp=0.2, ki=0.0, kd=0.0)
    with_i = PidParams(trend_days=120, integral_days=100, kp=0.2, ki=0.2, kd=0.0)

    assert p_only.warmup_days == 121
    assert with_i.warmup_days == 220
    assert target_weights(closes, p_only)["target"].notna().sum() == 20
    assert target_weights(closes, with_i)["target"].notna().sum() == 0


def test_target_stays_inside_the_weight_limits():
    closes = _trend(600, noise=0.03)
    params = PidParams(trend_days=120, kp=2.0, ki=2.0, kd=2.0, min_weight=0.1, max_weight=0.9)

    target = target_weights(closes, params)["target"].dropna()

    assert target.min() >= 0.1 and target.max() <= 0.9
    assert target.min() == pytest.approx(0.1) and target.max() == pytest.approx(0.9)


def test_unknown_parameter_is_refused_rather_than_ignored():
    with pytest.raises(ValueError, match="kpp"):
        PidParams.from_mapping({"kpp": 0.3})
    with pytest.raises(ValueError, match="kpp"):
        PidReversionStrategy(kpp=0.3)


@pytest.mark.parametrize(
    "bad",
    [{"trend_days": 2}, {"integral_days": 0}, {"min_weight": 0.6, "max_weight": 0.5}, {"band": -0.1}],
)
def test_invalid_parameters_are_refused(bad):
    with pytest.raises(ValueError):
        PidParams(**bad)


def test_strategy_is_registered_under_its_name():
    assert get_strategy("pid_reversion") is PidReversionStrategy


def _run_engine(tmp_path, frame: pd.DataFrame, strategy: PidReversionStrategy, start: str):
    cache = ParquetCache(tmp_path / "cache")
    cache.write("US", "AAA", frame)
    feed = HistoricalDataFeed(cache, "US", ["AAA"], start=start)
    broker = BacktestBroker(100_000, market="US", fee_model=FeeModel("US"), slippage_bps=0)
    limits = RiskLimits(
        max_position_pct=1.0, max_positions=1, max_daily_loss_pct=1.0,
        stop_loss_pct=0.0, min_cash_buffer_pct=0.02,
    )
    return BacktestEngine(feed, broker, strategy, risk_manager=RiskManager(limits)).run(), broker


def _ohlcv(closes: pd.Series) -> pd.DataFrame:
    opens = closes.shift(1).fillna(closes.iloc[0])
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": 1_000_000.0,
        }
    )


def test_engine_builds_the_base_position_then_sells_into_an_overshoot(tmp_path):
    closes = _trend(260, noise=0.002)
    closes.iloc[-20:] *= np.exp(0.01 * np.arange(1, 21))  # a run far above the quiet trend
    strategy = PidReversionStrategy(
        trend_days=120, integral_days=40, kp=0.3, ki=0.0, kd=0.0, base_weight=0.6, band=0.10
    )

    result, broker = _run_engine(tmp_path, _ohlcv(closes), strategy, start=str(closes.index[0].date()))

    sides = [fill.side.value for fill in result.fills]
    assert sides[0] == "BUY"
    assert "SELL" in sides
    assert not result.rejected_orders
    final_weight = broker.position("AAA").qty * closes.iloc[-1] / broker.equity
    assert final_weight < 0.3  # well under the 0.6 base


def test_engine_does_not_trade_before_the_trend_window_is_full(tmp_path):
    closes = _trend(100)
    strategy = PidReversionStrategy(trend_days=120)

    result, _ = _run_engine(tmp_path, _ohlcv(closes), strategy, start=str(closes.index[0].date()))

    assert result.trade_count == 0
