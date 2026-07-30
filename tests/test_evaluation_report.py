from __future__ import annotations

import json
import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.cli import build_parser, cmd_research_evaluate
from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide
from tradingbot.research.evaluation import _verdict_sentence, evaluate_strategy, render_markdown

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

    def test_benchmark_not_separately_configured_is_flagged(self):
        # Omitting --benchmark-config makes benchmark_config literally the
        # same object as config, which makes excess return exactly 0.0 by
        # construction. The report must say so.
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=CONFIG,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=runner,
        )
        assert report["benchmark_separately_configured"] is False

    def test_separately_configured_benchmark_is_not_flagged(self):
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
        assert report["benchmark_separately_configured"] is True

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


class TestDataRootThreading:
    """--data-root is a real CLI flag; a fake runner must see whatever value
    evaluate_strategy was given, for every backtest it runs (the full-period
    pair, the doubled-cost pair, and every walk-forward window)."""

    def test_data_root_reaches_every_backtest_call(self):
        seen: list[str | None] = []

        def recording_runner(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            seen.append(data_root)
            base = 10.0 if config["marker"] == "strategy" else 5.0
            return make_result(base)

        evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            data_root="/custom/data",
            runner=recording_runner,
        )
        # 4 full/doubled-cost backtests, plus 2 per walk-forward window.
        assert len(seen) > 4
        assert all(root == "/custom/data" for root in seen)

    def test_data_root_defaults_to_none(self):
        seen: list[str | None] = []

        def recording_runner(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            seen.append(data_root)
            base = 10.0 if config["marker"] == "strategy" else 5.0
            return make_result(base)

        evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=recording_runner,
        )
        assert seen
        assert all(root is None for root in seen)


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
        # Asserting "승격" alone would be vacuous — the title line
        # ("# ... 승격 평가 (...)") contains that word regardless of the
        # verdict, so this must check the actual conclusion sentence.
        assert markdown.startswith("# ")
        assert "## 결론" in markdown
        assert _verdict_sentence(report) in markdown
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

    def test_reproduction_command_section_reproduces_the_report(self):
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY", "QQQ"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            runner=runner,
        )
        markdown = render_markdown(report)
        heading = "## 재현 명령"
        assert heading in markdown
        section = markdown.split(heading, 1)[1]
        assert "research evaluate" in section
        assert "theme_multifactor" in section
        assert "US" in section
        assert "SPY" in section
        assert "QQQ" in section
        assert "2010-01-01" in section
        assert "2024-12-31" in section

    def test_reproduction_command_names_the_configs_that_produced_the_numbers(self):
        # Excess return is measured against whatever --benchmark-config
        # supplied. A command that omits it reproduces a *different* report:
        # the benchmark becomes the strategy itself and the excess is 0.0 by
        # construction. Dropping the configs makes the section a lie.
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2024-12-31",
            config_path="config/us_etf_rotation.toml",
            benchmark_config_path="config/us_etf_benchmark.toml",
            runner=runner,
        )
        section = render_markdown(report).split("## 재현 명령", 1)[1]
        assert "--config config/us_etf_rotation.toml" in section
        assert "--benchmark-config config/us_etf_benchmark.toml" in section
        # --config is a global flag: it must precede the subcommand.
        assert section.index("--config config/us_etf_rotation.toml") < section.index(
            "research evaluate"
        )

    def test_reproduction_command_omits_config_flags_that_were_not_used(self):
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
        section = render_markdown(report).split("## 재현 명령", 1)[1]
        assert "--benchmark-config" not in section
        assert "--data-root" not in section

    def test_nan_in_the_performance_table_reads_as_unmeasured_not_bare_nan(self):
        # annual_turnover is NaN whenever the run cannot be measured (see
        # report/metrics.py). The 성과 table must say 측정 불가 like every
        # other unmeasured value in this report, not the bare string "nan".
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
        # Force the strategy side of the already-computed report into the
        # single case this fix targets, isolating the table-formatting
        # concern from how a NaN turnover actually arises.
        report["strategy"] = {**report["strategy"], "annual_turnover": float("nan")}
        markdown = render_markdown(report)
        performance_section = markdown.split("## 성과", 1)[1].split("## 승격 기준", 1)[0]
        assert "nan" not in performance_section.lower()
        assert "측정 불가" in performance_section

    def test_nan_win_rate_reads_as_unmeasured_in_the_windows_section(self):
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
        report["walk_forward"] = {**report["walk_forward"], "win_rate": float("nan")}
        markdown = render_markdown(report)
        heading = "## 구간별 결과 (롤링 독립구간)"
        section = markdown.split(heading, 1)[1]
        assert "- 승률 측정 불가 —" in section
        assert "nan" not in section.lower()

    def test_unmeasured_criterion_marks_the_table_row(self):
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
        criteria = [dict(c) for c in report["verdict"]["criteria"]]
        criteria[0] = {**criteria[0], "measured": float("nan"), "passed": None, "reason": ""}
        report["verdict"] = {**report["verdict"], "criteria": criteria}
        markdown = render_markdown(report)
        name = criteria[0]["name"]
        threshold = criteria[0]["threshold"]
        assert f"| {name} | {threshold} | 측정 불가 | **측정 불가** |" in markdown

    def test_single_walk_forward_window_reads_as_insufficient_evidence_not_a_crash(self):
        # Live consequence this guards: the Korean strategy's data starts in
        # 2023, so exactly one walk-forward window is produced. Winning that
        # single 7-month sample must not read as a passed consistency
        # criterion, and the report must say *why* in terms a reader
        # understands as "not enough windows", not "the backtest crashed".
        report = evaluate_strategy(
            config=CONFIG,
            benchmark_config=BENCHMARK,
            research=RESEARCH,
            market="US",
            symbols=["SPY"],
            strategy_name="theme_multifactor",
            start="2010-01-01",
            end="2013-12-31",
            runner=runner,
        )
        assert report["walk_forward"]["evaluated"] == 1
        criterion = next(
            c for c in report["verdict"]["criteria"] if c["name"] == "walk_forward_win_rate"
        )
        assert criterion["passed"] is None
        assert "구간" in criterion["reason"]

        markdown = render_markdown(report)
        assert f"**측정 불가 ({criterion['reason']})**" in markdown
        assert criterion["reason"] in _verdict_sentence(report)
        assert "백테스트가 실패했다는 뜻이 아닙니다" in _verdict_sentence(report)


class TestVerdictSentenceUnmeasured:
    """Direct unit tests of `_verdict_sentence`'s unmeasured paragraph — the
    regression guard for the rule this whole branch exists to enforce."""

    def _report(self, criteria):
        return {
            "verdict": {
                "promoted": False,
                "unmeasured": [c["name"] for c in criteria if c["passed"] is None],
                "criteria": criteria,
            }
        }

    def test_names_the_unmeasured_criterion(self):
        report = self._report(
            [
                {
                    "name": "annual_turnover",
                    "threshold": "<= 6.0",
                    "measured": float("nan"),
                    "passed": None,
                    "reason": "",
                },
            ]
        )
        sentence = _verdict_sentence(report)
        assert "측정하지 못한 항목" in sentence
        assert "annual_turnover" in sentence
        assert "확인이 안 된 것이며" in sentence

    def test_never_says_promoted_when_something_is_unmeasured(self):
        report = self._report(
            [
                {
                    "name": "annual_turnover",
                    "threshold": "<= 6.0",
                    "measured": float("nan"),
                    "passed": None,
                    "reason": "",
                },
            ]
        )
        assert "승격 기준 6개를 모두 충족" not in _verdict_sentence(report)

    def test_surfaces_a_specific_reason_when_present(self):
        reason = "평가된 구간 1개 — 일관성을 판단하려면 최소 3개 구간이 필요합니다"
        report = self._report(
            [
                {
                    "name": "walk_forward_win_rate",
                    "threshold": ">= 0.6",
                    "measured": 1.0,
                    "passed": None,
                    "reason": reason,
                },
            ]
        )
        sentence = _verdict_sentence(report)
        assert reason in sentence
        assert "백테스트가 실패했다는 뜻이 아닙니다" in sentence

    def test_reasonless_unmeasured_criteria_do_not_get_a_fabricated_reason(self):
        report = self._report(
            [
                {
                    "name": "annual_turnover",
                    "threshold": "<= 6.0",
                    "measured": float("nan"),
                    "passed": None,
                    "reason": "",
                },
            ]
        )
        assert "백테스트가 실패했다는 뜻이 아닙니다" not in _verdict_sentence(report)


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

    def test_data_root_defaults_to_none(self):
        parser = build_parser()
        args = parser.parse_args(
            ["research", "evaluate", "--strategy", "theme_multifactor",
             "--market", "US", "--symbols", "SPY", "--start", "2010-01-01"]
        )
        assert args.data_root is None

    def test_parser_accepts_data_root(self):
        # Every sibling subcommand honors --data-root; research evaluate
        # declares it too (cli.py) and must not silently drop it.
        parser = build_parser()
        args = parser.parse_args(
            ["research", "evaluate", "--strategy", "theme_multifactor",
             "--market", "US", "--symbols", "SPY", "--start", "2010-01-01",
             "--data-root", "/custom/root"]
        )
        assert args.data_root == "/custom/root"


class TestCmdResearchEvaluateWiring:
    """Exercises cmd_research_evaluate itself, not just parser wiring.

    A spy is substituted for evaluate_strategy so the real function still
    runs (with a fake backtest runner injected) — this is real report
    plumbing, not a hand-built fixture — while record_experiment is
    intercepted so nothing is written under the real (gitignored)
    data/experiments directory. --out is redirected to tmp_path.
    """

    @pytest.fixture
    def invoke(self, tmp_path, monkeypatch):
        import tradingbot.research.evaluation as evaluation_module

        state: dict[str, object] = {"data_roots": [], "metrics": None}
        real_evaluate_strategy = evaluation_module.evaluate_strategy

        def spy_runner(config, *, market, symbols, strategy_name, start, end=None, data_root=None):
            state["data_roots"].append(data_root)  # type: ignore[union-attr]
            # A single-row curve keeps annual_turnover NaN too (see
            # report/metrics.py) without needing a contrived fixture.
            curve = pd.DataFrame({"date": [pd.Timestamp(start)], "equity": [100000.0]})
            return BacktestResult(
                initial_cash=100000.0,
                final_equity=100000.0,
                equity_curve=curve,
                fills=[],
                rejected_orders=[],
                expired_orders=[],
            )

        def spy_evaluate_strategy(**kwargs):
            return real_evaluate_strategy(runner=spy_runner, **kwargs)

        def fake_record_experiment(root, *, kind, params, metrics, created_at=None):
            state["metrics"] = metrics
            return tmp_path / "experiment.json"

        monkeypatch.setattr(evaluation_module, "evaluate_strategy", spy_evaluate_strategy)
        monkeypatch.setattr(
            "tradingbot.research.experiment.record_experiment", fake_record_experiment
        )

        def run(*, data_root=None, start="2023-01-01", end="2023-06-30"):
            parser = build_parser()
            argv = [
                "research", "evaluate",
                "--strategy", "theme_multifactor",
                "--market", "US",
                "--symbols", "SPY",
                "--start", start,
                "--end", end,
                "--out", str(tmp_path / "out"),
            ]
            if data_root is not None:
                argv += ["--data-root", data_root]
            args = parser.parse_args(argv)
            args.config = None
            cmd_research_evaluate(args)
            return state

        return run

    def test_data_root_reaches_the_runner(self, invoke, tmp_path):
        custom_root = str(tmp_path / "custom-data")
        state = invoke(data_root=custom_root)
        assert state["data_roots"]
        assert all(root == custom_root for root in state["data_roots"])

    def test_unmeasured_metrics_are_recorded_as_none_not_bare_nan(self, invoke):
        # A 6-month range is shorter than the real config's
        # train_years=3/test_years=1, so zero walk-forward windows are
        # produced and win_rate is NaN. Bare NaN survives Python's
        # json.dumps as an invalid `NaN` token that `jq`/`JSON.parse`
        # reject; it must become None before it reaches record_experiment.
        state = invoke()
        metrics = state["metrics"]
        assert metrics is not None
        assert metrics["walk_forward_win_rate"] is None
        assert metrics["annual_turnover"] is None
        assert "NaN" not in json.dumps(metrics)
