"""E2E 회귀 스모크 테스트.

체크인된 fixture(tests/data/KR, 2종목 × 2년)로 3개 내장 전략을 돌리고
최종 자산이 고정값과 원 단위까지 일치하는지 검증한다. 네트워크 사용 없음.

fixture를 재생성(tests/data/make_fixtures.py)하거나 체결/수수료/리스크 로직을
의도적으로 바꾼 경우에만 고정값을 갱신할 것. 예상 밖의 불일치는 회귀 버그다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine, BacktestResult
from tradingbot.report.report import generate_backtest_report
from tradingbot.risk import RiskManager
from tradingbot.strategies.ma_cross import MovingAverageCrossStrategy
from tradingbot.strategies.rsi_reversion import RsiReversionStrategy
from tradingbot.strategies.vol_breakout import VolatilityBreakoutStrategy

FIXTURE_ROOT = Path(__file__).resolve().parent / "data"
SYMBOLS = ["TESTA", "TESTB"]
INITIAL_CASH = 10_000_000

PINNED = {
    "ma_cross": {"final_equity": 9_335_113.68, "fills": 19},
    "vol_breakout": {"final_equity": 8_779_073.99, "fills": 940},
    "rsi_reversion": {"final_equity": 9_940_520.56, "fills": 18},
}


def make_strategy(name: str):
    if name == "ma_cross":
        return MovingAverageCrossStrategy(fast=20, slow=60, weight=0.2)
    if name == "vol_breakout":
        return VolatilityBreakoutStrategy(k=0.5, weight=0.2, exit="moc")
    if name == "rsi_reversion":
        return RsiReversionStrategy(period=14, buy_below=30, exit_above=55, max_hold_days=10, weight=0.2)
    raise ValueError(name)


def run_backtest(strategy_name: str) -> BacktestResult:
    cache = ParquetCache(FIXTURE_ROOT)
    feed = HistoricalDataFeed(cache, "KR", SYMBOLS, start="2020-01-01", end="2021-12-31")
    broker = BacktestBroker(
        initial_cash=INITIAL_CASH,
        market="KR",
        fee_model=FeeModel("KR", commission_rate=0.00015, sell_tax_rate=0.0015),
        slippage_bps=5.0,
    )
    return BacktestEngine(feed, broker, make_strategy(strategy_name), risk_manager=RiskManager()).run()


@pytest.mark.parametrize("strategy_name", sorted(PINNED))
def test_strategy_final_equity_matches_pinned_value(strategy_name):
    result = run_backtest(strategy_name)
    pinned = PINNED[strategy_name]

    assert result.final_equity == pytest.approx(pinned["final_equity"], abs=0.01)
    assert len(result.fills) == pinned["fills"]
    assert result.rejected_orders == []


def test_smoke_backtest_produces_report_artifacts(tmp_path):
    result = run_backtest("vol_breakout")
    html_path = generate_backtest_report(
        result,
        strategy_name="vol_breakout",
        market="KR",
        symbols=SYMBOLS,
        reports_root=tmp_path,
    )

    assert html_path.exists()
    assert "data:image/png;base64" in html_path.read_text(encoding="utf-8")
    assert (html_path.parent / "trades.csv").exists()
