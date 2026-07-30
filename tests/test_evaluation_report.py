from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.cli import build_parser, cmd_research_evaluate
from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide
from tradingbot.research.evaluation import evaluate_strategy, render_markdown

RESEARCH = {
    "promotion": {
        "min_excess_return": 0.0,
        "min_sharpe": 0.5,
        "max_mdd": 0.25,
        "max_annual_turnover": 6.0,
        "min_walk_forward_win_rate": 0.6,
        "cost_multiplier_check": 2.0,
    },
    "walk_forward": {"train_years": 3, "test_years": 1, "step_years": 1},
}

CONFIG = {"marker": "strategy", "fees": {"US": {"commission_rate": 0.001}}, "execution": {"slippage_bps": 5}}
BENCHMARK = {"marker": "benchmark", "fees": {"US": {"commission_rate": 0.001}}, "execution": {"slippage_bps": 5}}


def make_result(total_return_pct: float, buys: float = 0.0) -> BacktestResult:
    initial = 100000.0
    final = initial * (1 + total_return_pct / 100)
    dates = pd.date_range(start="2015-01-01", end="2024-12-31", freq="ME")
    equity = pd.Series(
        [initial + (final - initial) * i / max(len(dates) - 1, 1) for i in range(len(dates))]
    )
    curve = pd.DataFrame({"date": dates, "equity": equity})
    fills = []
    if buys:
        fills.append(
            Fill(
                order_id="x",
                symbol="SPY",
                side=OrderSide.BUY,
                qty=1,
                price=buys,
                fee=0.0,
                dt=date(2015, 1, 2),
            )
        )
    return BacktestResult(
        initial_cash=initial,
        final_equity=final,
        equity_curve=curve,
        fills=fills,
        rejected_orders=[],
        expired_orders=[],
    )


def runner(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
    """Strategy beats benchmark; doubled costs shrink both."""
    doubled = config["execution"]["slippage_bps"] > 5
    base = 60.0 if config["marker"] == "strategy" else 30.0
    return make_result(base * (0.5 if doubled else 1.0), buys=50000.0)


def flaky_runner(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
    """Like `runner`, but the walk-forward window starting 2013-01-01 fails.

    Used to verify a failed window is surfaced in the win-rate counts instead
    of quietly vanishing from the denominator.
    """
    if start == "2013-01-01":
        raise RuntimeError("no cached data for this window")
    doubled = config["execution"]["slippage_bps"] > 5
    base = 60.0 if config["marker"] == "strategy" else 30.0
    return make_result(base * (0.5 if doubled else 1.0), buys=50000.0)


class TestEvaluateStrategy:
    @pytest.fixture
    def report(self):
        return evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=runner,
        )

    def test_reports_the_headline_comparison(self, report):
        assert report["strategy"]["total_return_pct"] == pytest.approx(60.0)
        assert report["benchmark"]["total_return_pct"] == pytest.approx(30.0)

    def test_measures_turnover(self, report):
        assert report["strategy"]["annual_turnover"] > 0

    def test_runs_the_doubled_cost_variant(self, report):
        # Both sides pay the higher costs; excess return is what must survive.
        assert report["cost_2x"]["strategy_total_return_pct"] == pytest.approx(30.0)
        assert report["cost_2x"]["benchmark_total_return_pct"] == pytest.approx(15.0)

    def test_produces_walk_forward_windows(self, report):
        assert report["walk_forward"]["windows"]
        assert 0.0 <= report["walk_forward"]["win_rate"] <= 1.0

    def test_includes_a_verdict_over_all_six_criteria(self, report):
        assert len(report["verdict"]["criteria"]) == 6
        assert isinstance(report["verdict"]["promoted"], bool)

    def test_records_that_train_segments_are_unused(self, report):
        # The report must not let a reader think parameters were fitted.
        assert report["walk_forward"]["train_segments_used"] is False

    def test_walk_forward_counts_are_consistent(self, report):
        # A window is either evaluated or failed, never both or neither, and
        # the two must add up to every window that was run.
        wf = report["walk_forward"]
        assert wf["evaluated"] + wf["failed"] == wf["total"]
        assert wf["total"] == len(wf["windows"])

    def test_a_failed_window_is_counted_as_failed_not_evaluated(self):
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=flaky_runner,
        )
        wf = report["walk_forward"]
        assert wf["failed"] == 1
        assert wf["evaluated"] + wf["failed"] == wf["total"]
        assert wf["total"] == len(wf["windows"])


class TestRenderMarkdown:
    def test_leads_with_a_plain_language_verdict(self):
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=runner,
        )
        markdown = render_markdown(report)
        # The person deciding whether to fund this has to be able to read it.
        assert markdown.startswith("# ")
        assert "## 결론" in markdown
        assert "승격" in markdown
        assert "| 기준 |" in markdown

    def test_shows_the_failed_window_count_under_the_section_heading(self):
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=flaky_runner,
        )
        markdown = render_markdown(report)
        wf = report["walk_forward"]
        assert wf["failed"] == 1
        # The win rate must not be reported as a bare number that hides the
        # fact that a window was dropped from the walk-forward run.
        heading = "## 구간별 결과 (롤링 독립구간)"
        assert heading in markdown
        section = markdown.split(heading, 1)[1]
        assert f"전체 {wf['total']}구간" in section
        assert f"{wf['evaluated']}구간 평가" in section
        assert f"{wf['failed']}구간 측정 실패" in section


class TestCli:
    def test_parser_wires_research_evaluate(self):
        parser = build_parser()
        args = parser.parse_args(
            ["research", "evaluate", "--strategy", "theme_multifactor",
             "--market", "US", "--symbols", "SPY", "--start", "2010-01-01"]
        )
        assert args.handler is cmd_research_evaluate
        assert args.strategy == "theme_multifactor"
        assert args.symbols == ["SPY"]

    def test_existing_research_report_still_wired(self):
        from tradingbot.cli import cmd_research_report

        parser = build_parser()
        args = parser.parse_args(["research", "report", "--factors", "momentum_3m"])
        assert args.handler is cmd_research_report
