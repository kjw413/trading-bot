from __future__ import annotations

from datetime import date

import pytest

from tradingbot.cli import build_parser, cmd_research_report
from tradingbot.research.dates import month_end_trading_days
from tradingbot.research.gate import GateThresholds
from tradingbot.research.report import build_factor_report, render_markdown
from tradingbot.research.walk_forward import WalkForwardWindow


@pytest.fixture
def report(us_store, write_prices, fixed_factor):
    n = 300
    write_prices(us_store.cache, "US", "AAA", [100.0 + 2 * i for i in range(n)], start=date(2020, 1, 1))
    write_prices(us_store.cache, "US", "BBB", [100.0 + 1 * i for i in range(n)], start=date(2020, 1, 1))
    write_prices(us_store.cache, "US", "CCC", [100.0] * n, start=date(2020, 1, 1))
    thresholds = GateThresholds(
        horizon_days=5, n_quantiles=3, min_ic_mean=0.01, min_ic_ir=0.30, min_monotonicity=0.60
    )
    windows = [
        WalkForwardWindow(date(2019, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 6, 30))
    ]
    dates = month_end_trading_days("US", date(2020, 1, 1), date(2020, 6, 30))
    return build_factor_report(
        store=us_store,
        market="US",
        universe=["AAA", "BBB", "CCC"],
        factors=[fixed_factor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})],
        dates=dates,
        windows=windows,
        thresholds=thresholds,
    )


def test_report_metrics(report):
    data = report["factors"]["fixed"]
    assert data["ic"]["mean"] == pytest.approx(1.0)
    assert data["monotonicity"] == pytest.approx(1.0)
    assert data["spread_mean"] > 0
    assert data["turnover_mean"] == pytest.approx(0.0)  # fixed scores -> top set never changes
    assert data["walk_forward"]["win_rate"] == pytest.approx(1.0)


def test_gate_rejects_constant_ic_series(report):
    # IC is exactly 1.0 on every date -> std 0 -> IR NaN -> gate must FAIL loudly.
    data = report["factors"]["fixed"]
    assert data["gate"]["passed"] is False
    assert any("ic_ir" in reason for reason in data["gate"]["reasons"])


def test_render_markdown_contains_summary_table(report):
    markdown = render_markdown(report)
    assert "| factor |" in markdown
    assert "| fixed |" in markdown
    assert "FAIL" in markdown


def test_cli_parser_wires_research_report():
    parser = build_parser()
    args = parser.parse_args(["research", "report", "--factors", "momentum_3m"])
    assert args.handler is cmd_research_report
    assert args.factors == ["momentum_3m"]
