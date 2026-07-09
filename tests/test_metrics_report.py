from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide
from tradingbot.report.metrics import calculate_metrics
from tradingbot.report.report import generate_backtest_report


def sample_result():
    fills = [
        Fill("B1", "AAA", OrderSide.BUY, 10, 100, 1, date(2020, 1, 2)),
        Fill("S1", "AAA", OrderSide.SELL, 10, 110, 1, date(2020, 1, 3)),
        Fill("B2", "AAA", OrderSide.BUY, 10, 100, 1, date(2020, 1, 4)),
        Fill("S2", "AAA", OrderSide.SELL, 10, 90, 1, date(2020, 1, 5)),
    ]
    curve = pd.DataFrame(
        {
            "date": [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4), date(2020, 1, 5)],
            "equity": [1000, 1100, 1050, 990],
        }
    )
    return BacktestResult(
        initial_cash=1000,
        final_equity=990,
        equity_curve=curve,
        fills=fills,
        rejected_orders=[],
        expired_orders=[],
    )


def test_metrics_calculate_win_rate_profit_factor_and_drawdown():
    metrics, trades, drawdown = calculate_metrics(sample_result())

    assert metrics.total_return_pct == pytest.approx(-1.0)
    assert metrics.closed_trades == 2
    assert metrics.win_rate_pct == pytest.approx(50.0)
    assert metrics.profit_factor == pytest.approx(98 / 102)
    assert metrics.exposure_pct == pytest.approx(50.0)
    assert metrics.max_drawdown_pct == pytest.approx((990 / 1100 - 1) * 100)
    assert len(trades) == 2
    assert not drawdown.empty



def test_mdd_includes_initial_capital_anchor():
    result = BacktestResult(
        initial_cash=1000,
        final_equity=900,
        equity_curve=pd.DataFrame({"date": [date(2020, 1, 2)], "equity": [900]}),
        fills=[],
        rejected_orders=[],
        expired_orders=[],
    )

    metrics, _, drawdown = calculate_metrics(result)

    assert metrics.max_drawdown_pct == pytest.approx(-10.0)
    assert drawdown["drawdown"].min() == pytest.approx(-0.1)

def test_report_writes_html_and_trades_csv(tmp_path):
    html_path = generate_backtest_report(
        sample_result(),
        strategy_name="sample",
        market="KR",
        symbols=["AAA"],
        reports_root=tmp_path,
    )

    assert html_path.exists()
    assert "data:image/png;base64" in html_path.read_text(encoding="utf-8")
    trades_csv = html_path.parent / "trades.csv"
    assert trades_csv.exists()
    assert len(pd.read_csv(trades_csv)) == 2
