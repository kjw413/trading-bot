"""In-sample parameter sensitivity.

The tests that matter here are the ones about discipline, not arithmetic:
the sweep must refuse to touch out-of-sample data, must report the whole
surface rather than the argmax, and must never quietly drop a grid point that
failed to run.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from tradingbot.cli import parse_param_grid
from tradingbot.engine.engine import BacktestResult
from tradingbot.research.sensitivity import (
    OutOfSampleError,
    guard_in_sample,
    marginals,
    parameter_grid,
    render_markdown,
    run_sweep,
    sweep_strategy,
)

PERIODS = {
    "in_sample_start": "2010-01-01",
    "in_sample_end": "2018-12-31",
    "validation_start": "2019-01-01",
    "out_of_sample_start": "2022-01-01",
}
RESEARCH = {"periods": PERIODS}


def result_with_cagr(pct: float) -> BacktestResult:
    """A ~one-year curve. CAGR lands within a few hundredths of `pct` — the
    curve spans 364 days, so annualizing nudges it; tolerances allow for it."""
    initial = 100.0
    final = initial * (1 + pct / 100)
    curve = pd.DataFrame(
        {
            "date": [pd.Timestamp("2010-01-01"), pd.Timestamp("2010-12-31")],
            "equity": [initial, final],
        }
    )
    return BacktestResult(
        initial_cash=initial,
        final_equity=final,
        equity_curve=curve,
        fills=[],
        rejected_orders=[],
        expired_orders=[],
    )


def runner_from(table, *, benchmark=0.0, fails_on=None):
    """Fake runner: benchmark config returns `benchmark`, others read `table`."""

    def run(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
        if config.get("marker") == "benchmark":
            return result_with_cagr(benchmark)
        params = config["strategies"][strategy_name]
        key = tuple(sorted((k, v) for k, v in params.items() if k != "baseline"))
        if fails_on is not None and fails_on(dict(key)):
            raise RuntimeError("no data for this configuration")
        return result_with_cagr(table[key])

    return run


class TestGuardInSample:
    def test_window_inside_the_in_sample_period_is_allowed(self):
        guard_in_sample("2010-01-01", "2018-12-31", PERIODS)

    def test_window_past_in_sample_end_is_refused(self):
        with pytest.raises(OutOfSampleError, match="past in_sample_end"):
            guard_in_sample("2010-01-01", "2019-06-30", PERIODS)

    def test_open_ended_window_is_refused(self):
        """`--end` omitted means 'to the newest bar', which is out-of-sample."""
        with pytest.raises(OutOfSampleError, match="--end is required"):
            guard_in_sample("2010-01-01", None, PERIODS)

    def test_missing_in_sample_end_is_refused(self):
        with pytest.raises(OutOfSampleError, match="no \\[periods\\] in_sample_end"):
            guard_in_sample("2010-01-01", "2018-12-31", {})

    def test_reversed_window_is_refused(self):
        with pytest.raises(OutOfSampleError, match="is after end"):
            guard_in_sample("2018-01-01", "2010-12-31", PERIODS)


class TestParameterGrid:
    def test_cross_product_in_declaration_order(self):
        grid = parameter_grid({"a": [1, 2], "b": ["x", "y"]})
        assert grid == [
            {"a": 1, "b": "x"},
            {"a": 1, "b": "y"},
            {"a": 2, "b": "x"},
            {"a": 2, "b": "y"},
        ]

    def test_empty_grid_is_rejected(self):
        with pytest.raises(ValueError, match="at least one parameter"):
            parameter_grid({})

    def test_parameter_with_no_values_is_rejected(self):
        with pytest.raises(ValueError, match="no values to try"):
            parameter_grid({"a": [1], "b": []})


class TestRunSweep:
    def test_one_point_per_combination_with_excess_against_the_benchmark(self):
        table = {(("bear_exposure", 0.5),): 4.0, (("bear_exposure", 1.0),): 9.0}
        points = run_sweep(
            config={"strategies": {"s": {"baseline": True}}},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            grid={"bear_exposure": [0.5, 1.0]},
            start="2010-01-01",
            end="2018-12-31",
            runner=runner_from(table, benchmark=6.0),
        )

        assert [p.params["bear_exposure"] for p in points] == [0.5, 1.0]
        assert points[0].excess_cagr_pct == pytest.approx(-2.0, abs=0.05)
        assert points[1].excess_cagr_pct == pytest.approx(3.0, abs=0.05)

    def test_baseline_config_is_not_mutated_between_points(self):
        config = {"strategies": {"s": {"baseline": True}}}
        table = {(("k", 1),): 1.0, (("k", 2),): 2.0}

        run_sweep(
            config=config,
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            grid={"k": [1, 2]},
            start="2010-01-01",
            end="2018-12-31",
            runner=runner_from(table),
        )

        assert config == {"strategies": {"s": {"baseline": True}}}

    def test_a_failing_point_is_recorded_not_dropped(self):
        """Silently thinning a grid down to the runs that worked would let a
        parameter look robust because its hard cases vanished."""
        table = {(("k", 1),): 5.0, (("k", 2),): 5.0}
        points = run_sweep(
            config={"strategies": {"s": {}}},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            grid={"k": [1, 2]},
            start="2010-01-01",
            end="2018-12-31",
            runner=runner_from(table, fails_on=lambda params: params.get("k") == 2),
        )

        assert len(points) == 2
        assert points[1].error
        assert not points[1].measured
        assert math.isnan(points[1].excess_cagr_pct)


class TestMarginals:
    def test_median_is_taken_across_the_other_parameters(self):
        """`a=1` wins big in exactly one corner and loses elsewhere; `a=2` is
        steady. The median must prefer the steady one."""
        table = {
            (("a", 1), ("b", 10)): 30.0,
            (("a", 1), ("b", 20)): 0.0,
            (("a", 1), ("b", 30)): 0.0,
            (("a", 2), ("b", 10)): 5.0,
            (("a", 2), ("b", 20)): 5.0,
            (("a", 2), ("b", 30)): 5.0,
        }
        grid = {"a": [1, 2], "b": [10, 20, 30]}
        points = run_sweep(
            config={"strategies": {"s": {}}},
            benchmark_config={"marker": "benchmark"},
            market="US",
            symbols=["SPY"],
            strategy_name="s",
            grid=grid,
            start="2010-01-01",
            end="2018-12-31",
            runner=runner_from(table),
        )

        by_name = {m.parameter: m for m in marginals(points, grid)}
        a_medians = {v.value: v.median_excess_pct for v in by_name["a"].values}

        assert a_medians[2] > a_medians[1]
        # ...even though a=1 owns the single best cell.
        assert max(p.excess_cagr_pct for p in points if p.params["a"] == 1) > 25

    def test_spread_is_near_zero_when_a_parameter_does_not_matter(self):
        table = {(("a", 1), ("b", 10)): 5.0, (("a", 1), ("b", 20)): 5.0,
                 (("a", 2), ("b", 10)): 5.0, (("a", 2), ("b", 20)): 5.0}
        grid = {"a": [1, 2], "b": [10, 20]}
        points = run_sweep(
            config={"strategies": {"s": {}}}, benchmark_config={"marker": "benchmark"},
            market="US", symbols=["SPY"], strategy_name="s", grid=grid,
            start="2010-01-01", end="2018-12-31", runner=runner_from(table),
        )

        for marginal in marginals(points, grid):
            assert marginal.spread_pct == pytest.approx(0.0, abs=1e-9)


class TestSweepStrategy:
    def _report(self, **overrides):
        table = {(("k", 1),): 4.0, (("k", 2),): 8.0}
        kwargs = dict(
            config={"strategies": {"s": {}}},
            benchmark_config={"marker": "benchmark"},
            research=RESEARCH,
            market="US",
            symbols=["SPY", "IEF"],
            strategy_name="s",
            grid={"k": [1, 2]},
            start="2010-01-01",
            end="2018-12-31",
            runner=runner_from(table, benchmark=6.0),
        )
        kwargs.update(overrides)
        return sweep_strategy(**kwargs)

    def test_reports_counts_and_summary(self):
        report = self._report()

        assert report["combinations"] == 2
        assert report["measured"] == 2
        assert report["failed"] == 0
        assert report["summary"]["best_excess_pct"] == pytest.approx(2.0, abs=0.05)
        assert report["summary"]["worst_excess_pct"] == pytest.approx(-2.0, abs=0.05)

    def test_refuses_an_out_of_sample_window(self):
        with pytest.raises(OutOfSampleError):
            self._report(end="2021-12-31")

    def test_markdown_names_the_grid_size_where_the_best_cell_is_shown(self):
        markdown = render_markdown(self._report())

        assert "조합 2개" in markdown
        assert "파라미터별 한계표" in markdown
        # The report must not present a winner as a conclusion.
        assert "research evaluate" in markdown

    def test_markdown_survives_a_failed_point(self):
        table = {(("k", 1),): 4.0, (("k", 2),): 8.0}
        report = self._report(
            runner=runner_from(table, benchmark=6.0, fails_on=lambda p: p.get("k") == 2)
        )

        markdown = render_markdown(report)

        assert report["failed"] == 1
        assert "측정 실패" in markdown


class TestParseParamGrid:
    def test_parses_names_and_coerces_types(self):
        grid = parse_param_grid(["bear_exposure=0.5,1.0", "abs_momentum_ma_days=0,200"])

        assert grid == {"bear_exposure": [0.5, 1.0], "abs_momentum_ma_days": [0, 200]}
        assert all(isinstance(v, float) for v in grid["bear_exposure"])
        assert all(isinstance(v, int) for v in grid["abs_momentum_ma_days"])

    def test_parses_strings_and_booleans(self):
        grid = parse_param_grid(["weighting=equal,inverse_volatility", "flag=true,false"])

        assert grid["weighting"] == ["equal", "inverse_volatility"]
        assert grid["flag"] == [True, False]

    @pytest.mark.parametrize("spec", ["novalues", "=1,2", "k="])
    def test_rejects_malformed_specs(self, spec):
        with pytest.raises(ValueError):
            parse_param_grid([spec])

    def test_rejects_a_repeated_parameter(self):
        with pytest.raises(ValueError, match="given twice"):
            parse_param_grid(["k=1,2", "k=3"])
