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
from tradingbot.research.pid_sweep import (
    AXES,
    ExecutionModel,
    SelectionRule,
    buy_and_hold_metrics,
    decide,
    default_grid,
    evaluate_window,
    grid_configs,
    params_at,
    plateau_scores,
    run_grid,
    select,
    simulate,
    walk_forward,
    walk_forward_summary,
    window_metrics,
)
from tradingbot.research.walk_forward import walk_forward_windows
from tradingbot.risk import RiskLimits, RiskManager
from tradingbot.strategies.pid_reversion import PidParams, PidReversionStrategy, target_weights

CONFIG = {
    "backtest": {"initial_cash_us": 100_000},
    "execution": {"slippage_bps": 5},
    "fees": {
        "US": {
            "commission_rate": 0.0,
            "sec_fee_rate": 0.0000278,
            "finra_taf_per_share": 0.000166,
            "finra_taf_cap": 8.30,
        }
    },
    "risk": {
        "max_position_pct": 1.0,
        "max_positions": 1,
        "max_daily_loss_pct": 1.0,
        "stop_loss_pct": 0.0,
        "min_cash_buffer_pct": 0.02,
    },
}
EXECUTION = ExecutionModel.from_config(CONFIG, "US")


def _prices(n: int, *, seed: int = 11, noise: float = 0.02, start: str = "2010-01-04") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 80 * np.exp(np.cumsum(0.0004 + noise * rng.standard_normal(n)))
    gaps = np.exp(0.006 * rng.standard_normal(n))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * gaps
    index = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) * 1.002,
            "low": np.minimum(opens, closes) * 0.998,
            "close": closes,
            "volume": 1_000_000.0,
        },
        index=index,
    )


def _engine_equity(tmp_path, prices: pd.DataFrame, params: PidParams, start: date) -> pd.DataFrame:
    cache = ParquetCache(tmp_path / "cache")
    cache.write("US", "AAA", prices)
    feed = HistoricalDataFeed(cache, "US", ["AAA"], start=start)
    broker = BacktestBroker(
        EXECUTION.initial_cash,
        market="US",
        fee_model=FeeModel.from_config("US", CONFIG),
        slippage_bps=EXECUTION.slippage_bps,
    )
    strategy = PidReversionStrategy(**{k: getattr(params, k) for k in PidReversionStrategy.default_params})
    result = BacktestEngine(
        feed, broker, strategy, risk_manager=RiskManager(RiskLimits.from_config(CONFIG))
    ).run()
    return result


@pytest.mark.parametrize(
    "params",
    [
        PidParams(trend_days=120, integral_days=60, kp=0.2, ki=0.2, kd=0.2, base_weight=0.75),
        # Pinned at the cap, where the cash buffer and gap-up rejections bite.
        PidParams(trend_days=120, integral_days=60, kp=0.4, ki=0.0, kd=0.4, base_weight=0.98, band=0.05),
        PidParams(trend_days=60, integral_days=20, kp=0.1, ki=0.4, kd=0.0, base_weight=0.5, band=0.02),
    ],
)
def test_simulator_reproduces_the_engine_to_the_cent(tmp_path, params):
    prices = _prices(700)
    start = prices.index[150].date()

    engine = _engine_equity(tmp_path, prices, params, start)
    window = prices.loc[pd.Timestamp(start) :]
    targets = target_weights(prices["close"], params)["target"].to_numpy()[150:]
    sim = simulate(
        window["open"].tolist(), window["close"].tolist(), targets.tolist(),
        band=params.band, execution=EXECUTION,
    )

    assert engine.trade_count > 5  # the comparison is not vacuous
    assert int(sim.fills.sum()) == engine.trade_count
    np.testing.assert_allclose(sim.equity, engine.equity_curve["equity"].to_numpy(), rtol=0, atol=0.01)


def test_search_refuses_prices_past_its_cutoff():
    prices = _prices(400)

    with pytest.raises(ValueError, match="past the search cutoff"):
        run_grid(
            prices, start=prices.index[200].date(), end=prices.index[300].date(),
            grid=default_grid(), base=PidParams(), execution=EXECUTION,
        )


def _small_grid() -> dict[str, tuple[float, ...]]:
    return {
        "trend_days": (60, 120),
        "integral_days": (20, 40),
        "kp": (0.0, 0.2, 0.4),
        "ki": (0.0, 0.2),
        "kd": (0.0, 0.2),
        "base_weight": (0.5, 1.0),
    }


@pytest.fixture(scope="module")
def sweep():
    prices = _prices(1500, start="2008-01-01")
    start = prices.index[200].date()
    end = prices.index[-1].date()
    result = run_grid(
        prices, start=start, end=end, grid=_small_grid(), base=PidParams(band=0.05), execution=EXECUTION
    )
    return prices, result, start, end


def test_grid_covers_every_combination_once(sweep):
    _, result, _, _ = sweep

    assert len(result.positions) == 2 * 2 * 3 * 2 * 2 * 2
    assert len(set(result.positions)) == len(result.positions)
    assert result.equity.shape == (len(result.positions), len(result.dates))


def test_base_weight_above_the_cap_is_held_at_the_cap():
    params = params_at((0, 0, 0, 0, 0, 2), default_grid(), PidParams(max_weight=0.98))

    assert params.base_weight == pytest.approx(0.98)


def test_a_controller_with_no_gain_is_a_fixed_position_with_no_alpha(sweep):
    _, result, start, end = sweep
    metrics = window_metrics(result, start, end)

    flat = metrics[(metrics["kp"] == 0) & (metrics["ki"] == 0) & (metrics["kd"] == 0)]

    # It only trades to pull a drifted position back inside the band, so it
    # is not *exactly* the daily-rebalanced mix it is compared with, but it
    # must be close and must not look skilled.
    assert len(flat) == 8
    assert flat["fills"].max() < 0.02 * metrics.attrs["days"]
    assert flat["alpha"].abs().max() < 0.02


def test_alpha_is_return_beyond_the_same_average_exposure(sweep):
    _, result, start, end = sweep
    metrics = window_metrics(result, start, end)
    row = int(metrics["fills"].idxmax())

    mask = (result.dates >= pd.Timestamp(start)) & (result.dates <= pd.Timestamp(end))
    equity = np.concatenate([[result.initial_cash], result.equity[row][mask]])
    returns = equity[1:] / equity[:-1] - 1
    expected = (returns - result.weight[row][mask].mean() * result.asset_return[mask]).mean() * 252

    assert metrics.loc[row, "alpha"] == pytest.approx(expected)


def test_window_metrics_measure_only_the_window(sweep):
    _, result, start, end = sweep
    mid = result.dates[len(result.dates) // 2].date()

    first = window_metrics(result, start, mid)
    whole = window_metrics(result, start, end)

    assert first.attrs["days"] < whole.attrs["days"]
    assert (first["fills"] <= whole["fills"]).all()


def test_buy_and_hold_matches_the_price_change(sweep):
    prices, result, start, end = sweep
    window = prices.loc[pd.Timestamp(start) : pd.Timestamp(end), "close"]

    hold = buy_and_hold_metrics(result, start, end)

    # The window's first day counts from its own close: the day before it is
    # outside the simulation.
    assert hold["total_return"] == pytest.approx(window.iloc[-1] / window.iloc[0] - 1)


def test_plateau_score_is_the_worse_of_a_cell_and_its_neighbourhood():
    positions = [(i,) for i in range(4)]
    metrics = pd.DataFrame({"alpha": [0.0, 9.0, 1.0, 2.0]})

    scores = plateau_scores(metrics, positions, (4,))

    # The spike at index 1 sits between 0 and 1, so its neighbourhood median
    # is 1, not 9. Index 0 has the spike for its only neighbour (median 4.5)
    # but is itself 0, and does not get to borrow it.
    assert scores.tolist() == [0.0, 1.0, 1.0, 1.5]


def test_selection_prefers_a_plateau_to_a_lone_spike():
    positions = [(i,) for i in range(6)]
    metrics = pd.DataFrame(
        {
            "alpha": [0.0, 9.0, 0.0, 3.0, 3.0, 3.0],
            "fills": 50, "turnover": 1.0, "kp": 0.1, "ki": 0.0, "kd": 0.0,
        }
    )

    # Not the spike (1), nor the cells leaning on it (0, 2): the first cell
    # of the flat run, since the three are tied and equally simple.
    assert select(metrics, positions, (6,), SelectionRule()) == 3


def test_selection_skips_configurations_that_barely_trade_or_churn():
    positions = [(i,) for i in range(3)]
    metrics = pd.DataFrame(
        {
            "alpha": [9.0, 8.0, 1.0],
            "fills": [2, 50, 50],
            "turnover": [1.0, 9.0, 1.0],
            "kp": 0.1, "ki": 0.0, "kd": 0.0,
        }
    )

    assert select(metrics, positions, (3,), SelectionRule(min_fills=10, max_turnover=6.0)) == 2


def test_selection_returns_nothing_when_nothing_is_eligible():
    positions = [(0,), (1,)]
    metrics = pd.DataFrame({"alpha": [1.0, 2.0], "fills": [1, 2], "turnover": 1.0, "kp": 0.1, "ki": 0.0, "kd": 0.0})

    assert select(metrics, positions, (2,), SelectionRule()) is None


def test_ties_go_to_the_smaller_total_gain():
    positions = [(0,), (1,)]
    metrics = pd.DataFrame({"alpha": [1.0, 1.0], "fills": 50, "turnover": 1.0, "kp": [0.4, 0.1], "ki": 0.0, "kd": 0.0})

    assert select(metrics, positions, (2,), SelectionRule()) == 1


def test_walk_forward_scores_each_pick_on_the_year_after_it(sweep):
    _, result, start, end = sweep
    windows = walk_forward_windows(start, end, train_years=2, test_years=1, step_years=1)

    table = walk_forward(result, windows, SelectionRule(min_fills=3))
    summary = walk_forward_summary(table)

    assert len(table) == len(windows) >= 2
    assert (pd.to_datetime(table["test_start"]) > pd.to_datetime(table["train_end"])).all()
    assert summary["windows"] == table["test_alpha"].notna().sum()
    for _, row in table.dropna(subset=["test_alpha"]).iterrows():
        test = window_metrics(result, row["test_start"], row["test_end"])
        assert row["test_alpha"] == pytest.approx(test.loc[int(row["selected"]), "alpha"])
        assert tuple(test.loc[int(row["selected"]), list(AXES)]) == tuple(row[list(AXES)])


def test_walk_forward_summary_of_nothing_is_unmeasured():
    summary = walk_forward_summary(pd.DataFrame({"selected": [None]}))

    assert summary["windows"] == 0
    assert np.isnan(summary["mean_test_alpha"])


def test_single_configuration_evaluation_matches_the_grid(sweep):
    prices, result, start, end = sweep
    row = int(window_metrics(result, start, end)["fills"].idxmax())
    params = params_at(result.positions[row], result.grid, result.base)
    mid = result.dates[len(result.dates) // 2].date()

    report = evaluate_window(prices, params, sim_start=start, start=mid, end=end, execution=EXECUTION)

    expected = window_metrics(result, mid, end).loc[row]
    assert report["strategy"]["alpha"] == pytest.approx(expected["alpha"])
    assert report["strategy"]["total_return"] == pytest.approx(expected["total_return"])
    assert report["mirror"]["alpha"] != pytest.approx(report["strategy"]["alpha"])


def test_evaluation_never_reads_past_its_end_date(sweep):
    prices, result, start, _ = sweep
    params = params_at(result.positions[-1], result.grid, result.base)
    cutoff = result.dates[900].date()
    poisoned = prices.copy()
    poisoned.loc[poisoned.index > pd.Timestamp(cutoff), ["open", "close"]] *= 50

    clean = evaluate_window(prices, params, sim_start=start, start=start, end=cutoff, execution=EXECUTION)
    dirty = evaluate_window(poisoned, params, sim_start=start, start=start, end=cutoff, execution=EXECUTION)

    assert clean == dirty


@pytest.mark.parametrize(
    "kwargs, verdict",
    [
        (dict(in_sample_alpha=0.02, validation_alpha=0.01, validation_mirror_alpha=-0.01, walk_forward_mean_alpha=0.005), "consistent"),
        (dict(in_sample_alpha=0.02, validation_alpha=-0.01, validation_mirror_alpha=0.0, walk_forward_mean_alpha=0.005), "rejected"),
        (dict(in_sample_alpha=0.02, validation_alpha=0.01, validation_mirror_alpha=-0.01, walk_forward_mean_alpha=-0.001), "rejected"),
        (dict(in_sample_alpha=-0.02, validation_alpha=0.01, validation_mirror_alpha=-0.01, walk_forward_mean_alpha=0.005), "rejected"),
        (dict(in_sample_alpha=0.02, validation_alpha=0.01, validation_mirror_alpha=0.03, walk_forward_mean_alpha=0.005), "rejected"),
        (dict(in_sample_alpha=0.02, validation_alpha=float("nan"), validation_mirror_alpha=0.0, walk_forward_mean_alpha=0.005), "unmeasured"),
    ],
)
def test_decision_needs_every_key_to_turn(kwargs, verdict):
    assert decide(**kwargs)[0] == verdict


def test_simulator_mirrors_us_execution_only():
    with pytest.raises(ValueError, match="US execution only"):
        ExecutionModel.from_config(CONFIG, "KR")


def test_default_grid_is_the_registered_size():
    assert len(grid_configs(default_grid())) == 768


@pytest.mark.parametrize("risk", [{"stop_loss_pct": 0.05}, {"max_daily_loss_pct": 0.03}])
def test_config_with_risk_rules_the_simulator_cannot_mirror_is_refused(risk):
    config = {**CONFIG, "risk": {**CONFIG["risk"], **risk}}

    with pytest.raises(ValueError, match="does not mirror"):
        ExecutionModel.from_config(config, "US")


def test_report_runs_the_whole_registered_procedure_on_synthetic_prices():
    from tradingbot.research.pid_sweep import build_report, render_markdown

    prices = _prices(2600, start="2004-01-01")
    in_sample = (date(2006, 1, 2), date(2010, 12, 31))
    validation = (date(2011, 1, 1), date(2012, 12, 31))
    windows = walk_forward_windows(in_sample[0], validation[1], train_years=3, test_years=1, step_years=1)

    report = build_report(
        prices, symbol="AAA", in_sample=in_sample, validation=validation,
        walk_forward_windows_=windows, grid=_small_grid(), base=PidParams(band=0.05),
        execution=EXECUTION, rule=SelectionRule(min_fills=5),
    )
    text = render_markdown(report)

    assert report["data"]["search_cutoff"] == validation[1]
    assert report["data"]["rows_used"] < len(prices)  # the tail past validation was never read
    assert report["verdict"] in {"consistent", "rejected", "unmeasured"}
    assert report["selected"] is not None
    assert "읽지 않았다" in text and "워크포워드" in text
    assert report["validation_eval"]["end"] == validation[1]
