from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.engine.engine import BacktestResult
from tradingbot.research.evaluation import WindowResult, run_walk_forward, win_rate
from tradingbot.research.walk_forward import WalkForwardWindow

WINDOWS = [
    WalkForwardWindow(date(2019, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 12, 31)),
    WalkForwardWindow(date(2020, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
]

def result_returning(pct: float) -> BacktestResult:
    """A BacktestResult whose return_pct is exactly `pct`."""
    initial = 100.0
    final = initial * (1 + pct / 100)
    curve = pd.DataFrame(
        {"date": [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")],
         "equity": [initial, final]}
    )
    return BacktestResult(
        initial_cash=initial,
        final_equity=final,
        equity_curve=curve,
        fills=[],
        rejected_orders=[],
        expired_orders=[],
    )


def runner_for(returns: dict[tuple[str, str], float]):
    """Fake runner keyed by (config marker, window start)."""

    def run(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
        marker = config["marker"]
        return result_returning(returns[(marker, start)])

    return run


class TestRunWalkForward:
    def test_one_result_per_window_with_both_returns(self):
        runner = runner_for(
            {
                ("strategy", "2022-01-01"): 10.0,
                ("benchmark", "2022-01-01"): 5.0,
                ("strategy", "2023-01-01"): 2.0,
                ("benchmark", "2023-01-01"): 8.0,
            }
        )
        results = run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            windows=WINDOWS,
            runner=runner,
        )
        assert len(results) == 2
        assert results[0].strategy_return_pct == pytest.approx(10.0)
        assert results[0].benchmark_return_pct == pytest.approx(5.0)
        assert results[0].won is True
        assert results[1].won is False

    def test_backtests_only_the_test_segment(self):
        seen: list[tuple[str, str | None]] = []

        def recording(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            seen.append((start, end))
            return result_returning(1.0)

        run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=WINDOWS[:1],
            runner=recording,
        )
        # Train segments are never backtested — nothing is fitted, and running
        # them would just burn time.
        assert seen == [("2022-01-01", "2022-12-31"), ("2022-01-01", "2022-12-31")]

    def test_a_tie_is_not_a_win(self):
        runner = runner_for(
            {("strategy", "2022-01-01"): 5.0, ("benchmark", "2022-01-01"): 5.0}
        )
        results = run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=WINDOWS[:1],
            runner=runner,
        )
        assert results[0].won is False

    def test_a_failing_window_is_recorded_not_swallowed(self):
        def flaky(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            if start == "2023-01-01":
                raise RuntimeError("no data for 2023")
            return result_returning(1.0 if config["marker"] == "strategy" else 0.0)

        results = run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=WINDOWS,
            runner=flaky,
        )
        assert len(results) == 2
        assert results[0].won is True
        assert results[1].won is None
        assert "no data for 2023" in results[1].error

    def test_no_windows_returns_empty(self):
        assert run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=[],
            runner=runner_for({}),
        ) == []

    def test_data_root_reaches_every_runner_call(self):
        # --data-root is a real CLI flag (see cli.py); it must not be
        # silently dropped once it reaches the walk-forward loop.
        seen: list[str | None] = []

        def recording(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            seen.append(data_root)
            return result_returning(1.0)

        run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=WINDOWS,
            data_root="/custom/data/root",
            runner=recording,
        )
        # 2 windows x (strategy + benchmark) = 4 calls, every one carrying it.
        assert seen == ["/custom/data/root"] * 4

    def test_data_root_defaults_to_none(self):
        seen: list[str | None] = []

        def recording(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            seen.append(data_root)
            return result_returning(1.0)

        run_walk_forward(
            config={"marker": "strategy"},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            windows=WINDOWS[:1],
            runner=recording,
        )
        assert seen == [None, None]


def window_result(won: bool | None, error: str = "") -> WindowResult:
    return WindowResult(
        test_start=date(2022, 1, 1),
        test_end=date(2022, 12, 31),
        strategy_return_pct=1.0,
        benchmark_return_pct=0.0,
        won=won,
        error=error,
    )


class TestWinRate:
    def test_all_wins(self):
        assert win_rate([window_result(True), window_result(True)]) == pytest.approx(1.0)

    def test_half(self):
        assert win_rate([window_result(True), window_result(False)]) == pytest.approx(0.5)

    def test_failed_windows_leave_the_denominator(self):
        # A failed window is unmeasured, not a loss — but it must not inflate
        # the rate either, so it leaves both numerator and denominator.
        results = [window_result(True), window_result(None, "boom"), window_result(False)]
        assert win_rate(results) == pytest.approx(0.5)

    def test_all_windows_failed_is_nan_not_zero(self):
        # Nothing was measured; reporting 0.0 would read as "lost every
        # window" and reporting 1.0 would be worse.
        assert math.isnan(win_rate([window_result(None, "boom")]))

    def test_empty_is_nan(self):
        assert math.isnan(win_rate([]))
