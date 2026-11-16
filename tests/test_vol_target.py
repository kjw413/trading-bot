from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.research.exposure_test import (
    calibrate_level,
    comparison,
    design_report,
    final_test,
    highest_level_within,
    render_calibration_markdown,
    render_design_markdown,
    render_final_markdown,
    simulate_rule,
    yearly_table,
)
from tradingbot.research.pid_sweep import ExecutionModel
from tradingbot.risk import RiskLimits, RiskManager
from tradingbot.strategies.registry import get_strategy
from tradingbot.strategies.vol_target import (
    VolTargetParams,
    VolTargetStrategy,
    realized_volatility,
    vol_target_weights,
)

CONFIG = {
    "backtest": {"initial_cash_us": 100_000},
    "execution": {"slippage_bps": 5},
    "fees": {"US": {"commission_rate": 0.0, "sec_fee_rate": 0.0000278,
                    "finra_taf_per_share": 0.000166, "finra_taf_cap": 8.30}},
    "risk": {"max_position_pct": 1.0, "max_positions": 1, "max_daily_loss_pct": 1.0,
             "stop_loss_pct": 0.0, "min_cash_buffer_pct": 0.02},
}
EXECUTION = ExecutionModel.from_config(CONFIG, "US")


def _prices(n: int, *, seed: int = 2, start: str = "2010-01-04") -> pd.DataFrame:
    """Calm for the first half, three times as volatile in the second."""
    rng = np.random.default_rng(seed)
    scale = np.where(np.arange(n) < n // 2, 0.008, 0.024)
    closes = 80 * np.exp(np.cumsum(0.0004 + scale * rng.standard_normal(n)))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * np.exp(0.004 * rng.standard_normal(n))
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) * 1.001,
         "low": np.minimum(opens, closes) * 0.999, "close": closes, "volume": 1e6},
        index=pd.bdate_range(start, periods=n),
    )


def _targets(params: VolTargetParams):
    return lambda closes: vol_target_weights(closes, params)["target"]


def test_realised_volatility_matches_its_definition_and_waits_for_a_full_window():
    closes = _prices(60)["close"]

    vol = realized_volatility(closes, 20)

    assert vol.iloc[:20].isna().all() and vol.iloc[20:].notna().all()
    expected = np.log(closes).diff().iloc[-20:].std(ddof=1) * np.sqrt(252)
    assert vol.iloc[-1] == pytest.approx(expected)


def test_weight_is_target_over_realised_volatility_until_the_cap():
    closes = _prices(400)["close"]
    params = VolTargetParams(vol_days=20, target_vol=0.20, max_weight=0.98)

    frame = vol_target_weights(closes, params).dropna()

    inside = frame[frame["target"] < 0.98]
    assert len(inside) > 50
    np.testing.assert_allclose(inside["target"], 0.20 / inside["vol"])
    assert frame["target"].max() == pytest.approx(0.98)


def test_position_is_smaller_when_the_instrument_is_more_volatile():
    closes = _prices(600)["close"]

    target = vol_target_weights(closes, VolTargetParams(target_vol=0.15))["target"]

    assert target.iloc[100:280].mean() > 1.8 * target.iloc[340:].mean()


def test_weight_at_a_date_does_not_change_when_later_prices_arrive():
    closes = _prices(300)["close"]
    params = VolTargetParams()

    pd.testing.assert_frame_equal(
        vol_target_weights(closes.iloc[:200], params), vol_target_weights(closes, params).iloc[:200]
    )


def test_a_flat_window_gives_no_view_instead_of_the_maximum():
    closes = pd.Series(100.0, index=pd.bdate_range("2020-01-01", periods=40))

    assert vol_target_weights(closes, VolTargetParams())["target"].isna().all()


@pytest.mark.parametrize(
    "bad", [{"vol_days": 1}, {"target_vol": 0.0}, {"min_weight": 0.7, "max_weight": 0.5}, {"band": -0.1}]
)
def test_invalid_parameters_are_refused(bad):
    with pytest.raises(ValueError):
        VolTargetParams(**bad)


def test_unknown_parameter_is_refused_rather_than_ignored():
    with pytest.raises(ValueError, match="target_volatility"):
        VolTargetStrategy(target_volatility=0.2)


def test_strategy_is_registered_under_its_name():
    assert get_strategy("vol_target") is VolTargetStrategy


@pytest.mark.parametrize(
    "params",
    [
        VolTargetParams(vol_days=20, target_vol=0.20),
        VolTargetParams(vol_days=60, target_vol=0.35, band=0.03),  # pinned at the cap in the calm half
        VolTargetParams(vol_days=10, target_vol=0.08, band=0.02),
    ],
)
def test_simulator_reproduces_the_engine_to_the_cent(tmp_path, params):
    prices = _prices(700)
    start = prices.index[100].date()
    cache = ParquetCache(tmp_path / "cache")
    cache.write("US", "AAA", prices)
    broker = BacktestBroker(
        EXECUTION.initial_cash, market="US", fee_model=FeeModel.from_config("US", CONFIG),
        slippage_bps=EXECUTION.slippage_bps,
    )
    strategy = VolTargetStrategy(**{k: getattr(params, k) for k in VolTargetStrategy.default_params})
    engine = BacktestEngine(
        HistoricalDataFeed(cache, "US", ["AAA"], start=start), broker, strategy,
        risk_manager=RiskManager(RiskLimits.from_config(CONFIG)),
    ).run()

    path = simulate_rule(prices, _targets(params), start=start, band=params.band, execution=EXECUTION)

    assert engine.trade_count > 5
    assert int(path.fills.sum()) == engine.trade_count
    np.testing.assert_allclose(path.equity[0], engine.equity_curve["equity"].to_numpy(), rtol=0, atol=0.01)


def _report_kwargs(prices, params, cutoff):
    return dict(
        symbol="AAA", label="test", make_targets=_targets(params),
        variants={"low": _targets(VolTargetParams(target_vol=0.10)), "high": _targets(VolTargetParams(target_vol=0.30))},
        band=params.band, sim_start=prices.index[60].date(),
        windows={"first": (prices.index[60].date(), prices.index[400].date()),
                 "design": (prices.index[60].date(), cutoff)},
        execution=EXECUTION,
    )


def test_design_report_refuses_prices_past_its_cutoff():
    prices = _prices(900)
    cutoff = prices.index[700].date()

    with pytest.raises(ValueError, match="past the design cutoff"):
        design_report(prices, placebo_n=0, **_report_kwargs(prices, VolTargetParams(), cutoff))


def test_design_report_measures_the_rule_its_benchmarks_and_the_placebo():
    prices = _prices(900)
    cutoff = prices.index[700].date()
    cut = prices.loc[: pd.Timestamp(cutoff)]

    report = design_report(cut, placebo_n=5, **_report_kwargs(cut, VolTargetParams(), cutoff))
    text = render_design_markdown(report)

    design = report["windows"]["design"]
    assert design["fixed"]["weight"] == pytest.approx(design["strategy"]["avg_weight"])
    assert report["design_avg_weight"] == pytest.approx(design["strategy"]["avg_weight"])
    assert list(report["variants"]["variant"]) == ["low", "high"]
    assert report["variants"].set_index("variant")["avg_weight"]["low"] < report["variants"].set_index("variant")["avg_weight"]["high"]
    assert set(report["placebo"]) == {"sign_flip", "shuffle"} and report["placebo"]["sign_flip"]["n"] == 5
    assert "시험 구간은 읽지 않았다" in text


def test_yearly_table_compounds_to_the_window_return():
    prices = _prices(900)
    path = simulate_rule(prices, _targets(VolTargetParams()), start=prices.index[60].date(), band=0.10, execution=EXECUTION)
    start, end = prices.index[60].date(), prices.index[-1].date()

    table = yearly_table(path, start, end)

    whole = comparison(path, start, end)["strategy"]["total_return"]
    assert np.prod(1 + table["strategy_return"]) - 1 == pytest.approx(whole)


def test_final_test_reads_one_number_against_the_bar_and_says_what_holding_did():
    prices = _prices(900)
    params = VolTargetParams()
    test_start = prices.index[700].date()
    kwargs = dict(
        make_targets=_targets(params), variants={"low": _targets(VolTargetParams(target_vol=0.10))},
        band=params.band, sim_start=prices.index[60].date(), test_start=test_start,
        execution=EXECUTION, fixed_weight=0.6,
    )

    result = final_test(prices, min_total_return=0.10, **kwargs)
    total = result["strategy"]["total_return"]

    assert result["verdict"] == ("pass" if total >= 0.10 else "fail")
    assert final_test(prices, min_total_return=total - 0.01, **kwargs)["verdict"] == "pass"
    assert final_test(prices, min_total_return=total + 0.01, **kwargs)["verdict"] == "fail"
    assert result["fixed"]["weight"] == 0.6  # the weight handed in, not one taken from the test window
    # Both are measured from the last close before the window, so its first day counts.
    hold = prices["close"].iloc[-1] / prices["close"].iloc[699] - 1
    assert result["buy_and_hold"]["total_return"] == pytest.approx(hold)
    assert result["buy_and_hold_passes"] == (hold >= 0.10)
    assert "판정" in render_final_markdown(result, symbol="AAA", label="test")


def test_final_test_enters_the_window_holding_what_the_rule_had_built():
    prices = _prices(900)
    params = VolTargetParams()
    test_start = prices.index[700].date()
    path = simulate_rule(prices, _targets(params), start=prices.index[60].date(), band=params.band, execution=EXECUTION)

    result = final_test(
        prices, make_targets=_targets(params), variants={}, band=params.band,
        sim_start=prices.index[60].date(), test_start=test_start, execution=EXECUTION,
        min_total_return=0.10, fixed_weight=0.6,
    )

    last_before = 700 - 60 - 1  # the path starts at row 60 of the price frame
    before = path.equity[0][last_before]
    assert result["strategy"]["total_return"] == pytest.approx(path.equity[0][-1] / before - 1)
    assert path.weight[0][last_before] > 0  # not a fresh start from cash


def test_highest_level_within_stops_at_the_first_breach():
    table = pd.DataFrame(
        {"level": [0.05, 0.10, 0.15, 0.20], "max_drawdown": [-0.10, -0.25, -0.34, -0.29]}
    )

    # 0.20 is back inside the limit, but only above a level that broke it.
    assert highest_level_within(table, 0.30) == 0.10
    assert highest_level_within(table, 0.35) == 0.20
    assert highest_level_within(table, 0.05) is None
    assert highest_level_within(table.iloc[::-1], 0.30) == 0.10  # order of rows does not matter


def test_a_drawdown_exactly_on_the_limit_counts_as_within():
    table = pd.DataFrame({"level": [0.05, 0.10], "max_drawdown": [-0.30, -0.3001]})

    assert highest_level_within(table, 0.30) == 0.05


def _calibration_kwargs(prices, end):
    return dict(
        targets_for=lambda level: _targets(VolTargetParams(target_vol=level)),
        levels=[0.04, 0.08, 0.12, 0.16, 0.20, 0.30],
        band=0.10, start=prices.index[60].date(), end=end, execution=EXECUTION,
    )


def test_calibration_picks_the_level_the_limit_allows_and_the_next_one_breaks_it():
    prices = _prices(900)
    end = prices.index[-1].date()

    _, loose = calibrate_level(prices, max_drawdown=1.0, **_calibration_kwargs(prices, end))
    drawdowns = loose.set_index("level")["max_drawdown"]
    limit = -(drawdowns[0.12] + drawdowns[0.16]) / 2  # between two levels
    selected, table = calibrate_level(prices, max_drawdown=limit, **_calibration_kwargs(prices, end))

    assert drawdowns[0.04] > drawdowns[0.30]  # more risk, deeper drawdown
    assert selected == 0.12
    assert table.set_index("level")["within_limit"].to_dict() == {
        0.04: True, 0.08: True, 0.12: True, 0.16: False, 0.20: False, 0.30: False,
    }
    text = render_calibration_markdown(selected, table, symbol="AAA", max_drawdown=limit, start=end, end=end)
    assert "12% **← 선택**" in text


def test_calibration_says_so_when_no_level_fits():
    prices = _prices(900)
    end = prices.index[-1].date()

    selected, table = calibrate_level(prices, max_drawdown=0.0001, **_calibration_kwargs(prices, end))

    assert selected is None
    assert "한도 안에 들어온 수준이 없다" in render_calibration_markdown(
        selected, table, symbol="AAA", max_drawdown=0.0001, start=end, end=end
    )


def test_calibration_refuses_prices_past_its_cutoff():
    prices = _prices(900)

    with pytest.raises(ValueError, match="past the design cutoff"):
        calibrate_level(prices, max_drawdown=0.30, **_calibration_kwargs(prices, prices.index[700].date()))


def test_paper_engine_sizes_the_position_at_the_close_and_fills_it_at_the_next_open(tmp_path):
    """The path a live paper session takes: confirmed daily bar in, order out, fill next morning."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from tradingbot.broker.paper import PaperBroker
    from tradingbot.data.polling import PollingDataFeed
    from tradingbot.engine.calendar import WeekdayCalendar
    from tradingbot.engine.clock import TradingSessionClock
    from tradingbot.engine.paper import PaperTradingEngine

    history = _prices(40, start="2024-01-01")  # ends Fri 2024-02-23
    today = pd.Timestamp("2024-02-26")
    bar = {"open": 100.0, "high": 101.0, "low": 99.0, "close": float(history["close"].iloc[-1]) * 1.01, "volume": 1e6}

    class TodayCache(ParquetCache):
        def update(self, market, symbol, start=None, end=None):
            frame = pd.concat([self.read(market, symbol), pd.DataFrame([bar], index=[today])])
            self.write(market, symbol, frame[~frame.index.duplicated(keep="last")])
            return self.read(market, symbol)

    cache = TodayCache(tmp_path / "cache")
    cache.write("US", "AAA", history)
    clock = TradingSessionClock("US", poll_interval=timedelta(minutes=5), calendar=WeekdayCalendar("US"))
    next_open = bar["close"] * 1.002

    def build(strategy):
        broker = PaperBroker("vol", tmp_path / "state", 100_000, market="US",
                             fee_model=FeeModel.from_config("US", CONFIG), slippage_bps=5)
        feed = HistoricalDataFeed(cache, "US", ["AAA"], start="2024-01-01")
        polling = PollingDataFeed("US", ["AAA"], clock, price_fetcher=lambda market, symbols: {s: next_open for s in symbols})
        return PaperTradingEngine(feed, polling, broker, strategy,
                                  risk_manager=RiskManager(RiskLimits.from_config(CONFIG))), broker

    params = VolTargetParams(vol_days=20, target_vol=0.20)
    engine, broker = build(VolTargetStrategy())
    tz = ZoneInfo("America/New_York")

    after_close = engine.run_once(datetime(2024, 2, 26, 16, 5, tzinfo=tz))

    closes = pd.concat([history["close"], pd.Series([bar["close"]], index=[today])])
    target = float(vol_target_weights(closes, params)["target"].iloc[-1])
    expected_qty = int(min(100_000 * target, 100_000 * 0.98) // bar["close"])
    assert after_close["actions"] == ["close"]
    assert [order.qty for order in broker.open_orders()] == [expected_qty]

    # A restart before the open must not lose or duplicate the order: the rule keeps no state.
    engine, broker = build(VolTargetStrategy())
    assert [order.qty for order in broker.open_orders()] == [expected_qty]

    at_open = engine.run_once(datetime(2024, 2, 27, 9, 31, tzinfo=tz))

    assert "open" in at_open["actions"]
    assert at_open["positions"] == {"AAA": expected_qty}
    assert at_open["open_orders"] == 0


def _crash_prices() -> pd.DataFrame:
    """A calm climb, a sharp fall of about 45%, then a full recovery."""
    rng = np.random.default_rng(9)
    calm = 0.0005 + 0.005 * rng.standard_normal(300)
    fall = np.full(12, np.log(0.95))
    recovery = np.full(120, 0.01) + 0.005 * rng.standard_normal(120)
    closes = 80 * np.exp(np.cumsum(np.concatenate([calm, fall, recovery])))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) * 1.001,
         "low": np.minimum(opens, closes) * 0.999, "close": closes, "volume": 1e6},
        index=pd.bdate_range("2015-01-01", periods=len(closes)),
    )


def _run_engine(tmp_path, prices, strategy, start):
    cache = ParquetCache(tmp_path / "cache")
    cache.write("US", "AAA", prices)
    broker = BacktestBroker(
        EXECUTION.initial_cash, market="US", fee_model=FeeModel.from_config("US", CONFIG),
        slippage_bps=EXECUTION.slippage_bps,
    )
    result = BacktestEngine(
        HistoricalDataFeed(cache, "US", ["AAA"], start=start), broker, strategy,
        risk_manager=RiskManager(RiskLimits.from_config(CONFIG)),
    ).run()
    return result, broker


STOP = VolTargetParams(vol_days=20, target_vol=0.50, band=0.10, max_drawdown_stop=0.15)


def test_stop_sells_out_once_equity_falls_past_the_limit_and_never_buys_again(tmp_path):
    prices = _crash_prices()
    strategy = VolTargetStrategy(**{k: getattr(STOP, k) for k in VolTargetStrategy.default_params})

    result, broker = _run_engine(tmp_path, prices, strategy, prices.index[30].date())

    assert strategy.halted
    halted_on = pd.Timestamp(strategy.halted_on)
    assert prices.index[300] < halted_on < prices.index[312]  # during the fall
    assert strategy.halted_equity < strategy.peak_equity * (1 - 0.15)
    last_fill = result.fills[-1]
    assert last_fill.side.value == "SELL" and pd.Timestamp(last_fill.dt) > halted_on
    assert not [f for f in result.fills if f.side.value == "BUY" and pd.Timestamp(f.dt) > halted_on]
    assert broker.position("AAA").qty == 0
    # It stayed out through a recovery that a running rule would have bought back into.
    assert prices["close"].iloc[-1] > prices["close"].loc[halted_on] * 2


def test_without_a_stop_the_same_fall_is_ridden_through(tmp_path):
    prices = _crash_prices()
    strategy = VolTargetStrategy(**{**{k: getattr(STOP, k) for k in VolTargetStrategy.default_params}, "max_drawdown_stop": 0.0})

    _, broker = _run_engine(tmp_path, prices, strategy, prices.index[30].date())

    assert not strategy.halted
    assert broker.position("AAA").qty > 0


def test_simulator_reproduces_the_engine_through_a_stop(tmp_path):
    prices = _crash_prices()
    start = prices.index[30].date()
    strategy = VolTargetStrategy(**{k: getattr(STOP, k) for k in VolTargetStrategy.default_params})

    result, _ = _run_engine(tmp_path, prices, strategy, start)
    path = simulate_rule(
        prices, _targets(STOP), start=start, band=STOP.band, execution=EXECUTION,
        stop_drawdown=STOP.max_drawdown_stop,
    )

    assert strategy.halted
    assert int(path.fills.sum()) == result.trade_count
    np.testing.assert_allclose(path.equity[0], result.equity_curve["equity"].to_numpy(), rtol=0, atol=0.01)


def test_stop_and_peak_survive_a_restart(tmp_path):
    from tradingbot.strategies.state import JsonStateStore

    prices = _crash_prices()
    store = JsonStateStore(tmp_path / "state" / "vol.strategy.json")
    params = {k: getattr(STOP, k) for k in VolTargetStrategy.default_params}
    first = VolTargetStrategy(**params)
    first.bind_state_store(store)
    _run_engine(tmp_path / "a", prices.iloc[:320], first, prices.index[30].date())
    assert first.halted

    restarted = VolTargetStrategy(**params)
    restarted.bind_state_store(store)

    assert restarted.halted and restarted.halted_on == first.halted_on
    assert restarted.peak_equity == pytest.approx(first.peak_equity)
    # A fresh account on the same store still does nothing: the stop is the operator's to clear.
    result, broker = _run_engine(tmp_path / "b", prices, restarted, prices.index[30].date())
    assert result.trade_count == 0 and broker.position("AAA").qty == 0


def test_peak_is_remembered_across_a_restart_before_any_stop(tmp_path):
    from tradingbot.strategies.state import JsonStateStore

    store = JsonStateStore(tmp_path / "state.json")
    strategy = VolTargetStrategy(max_drawdown_stop=0.30)
    strategy.bind_state_store(store)
    strategy.peak_equity = 123_456.0
    strategy.persist_state()

    restarted = VolTargetStrategy(max_drawdown_stop=0.30)
    restarted.bind_state_store(store)

    assert restarted.peak_equity == 123_456.0 and not restarted.halted


@pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5])
def test_stop_outside_zero_to_one_is_refused(bad):
    with pytest.raises(ValueError, match="max_drawdown_stop"):
        VolTargetParams(max_drawdown_stop=bad)


def test_calibration_measures_each_level_without_the_stop():
    prices = _crash_prices()
    end = prices.index[-1].date()

    _, table = calibrate_level(
        prices, targets_for=lambda level: _targets(VolTargetParams(target_vol=level)),
        levels=[0.50], max_drawdown=0.15, band=0.10, start=prices.index[30].date(), end=end,
        execution=EXECUTION,
    )

    # With the stop the drawdown would be cut near 15%; the calibration must see the full fall.
    assert table["max_drawdown"].iloc[0] < -0.30
