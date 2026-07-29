# 승격 기준 측정 도구 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지금 잴 수 없는 승격 기준 3개(Walk-forward 승률, 연 회전율, 비용 2배 검정)를 측정 가능하게 만들어, 어떤 전략이든 6개 기준 전부에 대해 합격/불합격 판정을 받을 수 있게 한다.

**Architecture:** 새 계산 로직을 거의 만들지 않는다. 회전율만 지표 모듈에 추가하고, 나머지는 기존 `run_backtest`·`calculate_metrics`·`walk_forward_windows`를 엮는 얇은 평가 층(`research/evaluation.py`)과 CLI 하나를 얹는다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-07-29-promotion-criteria-measurement-design.md`

## Global Constraints

- **신규 의존성 추가 금지.**
- **기존 백테스트·HTML 리포트 결과를 바꾸지 않는다.** `BacktestMetrics`의 필드를 추가/변경하지 않는다 — `tests/test_smoke_backtest.py`가 고정값 회귀 테스트다.
- **미측정과 통과를 구분한다.** 어떤 기준이든 측정에 실패하면 그 항목은 NaN이고 판정은 "합격"이 될 수 없다. 0을 반환해 "거래 없음"으로 위장하거나, 분모에서 조용히 빼서 승률을 부풀리지 않는다.
- **원본 설정 객체를 변경하지 않는다.** 비용 배수는 깊은 복사본에만 적용한다.
- **파라미터 탐색·최적화 금지.** 이 도구는 측정만 한다.
- 테스트에서 네트워크 접근 금지 (합성 가격 fixture만).
- 기존 CLI 명령 동작 변경 금지.
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 접두사: 중간 `EVAL(part):`, 마지막 `EVAL:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `BacktestResult` (`engine/engine.py`, frozen dataclass): `initial_cash: float`, `final_equity: float`, `equity_curve: pd.DataFrame` (컬럼 `date`, `equity`), `fills: list[Fill]`, `rejected_orders`, `expired_orders`; property `return_pct` (백분율), `trade_count`
- `Fill` (`models.py`, frozen): `order_id, symbol, side: OrderSide, qty: int, price: float, fee: float, dt: date`; property `gross_value = qty * price`
- `OrderSide` (`models.py`): `str` Enum, `BUY = "BUY"`, `SELL = "SELL"`
- `run_backtest(config, *, market, symbols, strategy_name, start, end=None, data_root=None) -> BacktestResult` (`services.py`)
- `calculate_metrics(result) -> tuple[BacktestMetrics, list[ClosedTrade], pd.DataFrame]` (`report/metrics.py`); `BacktestMetrics` 필드: `total_return_pct, cagr_pct, max_drawdown_pct, sharpe, win_rate_pct, profit_factor, exposure_pct, closed_trades`
- `walk_forward_windows(start, end, *, train_years, test_years, step_years) -> list[WalkForwardWindow]` (`research/walk_forward.py`); `WalkForwardWindow` frozen dataclass: `train_start, train_end, test_start, test_end` (모두 `date`)
- `load_research_config(path=None) -> dict` (`research/gate.py`)
- `record_experiment(root, *, kind, params, metrics, created_at=None) -> Path` (`research/experiment.py`)
- `resolve_project_path(value) -> Path`, `load_config(path=None) -> dict` (`config.py`)
- `config/research.toml` `[promotion]`: `min_excess_return=0.0`, `min_sharpe=0.5`, `max_mdd=0.25`, `max_annual_turnover=6.0`, `min_walk_forward_win_rate=0.6`, `cost_multiplier_check=2.0`
- `config/research.toml` `[walk_forward]`: `train_years=3`, `test_years=1`, `step_years=1`
- 비용 위치: `config` dict의 `["fees"][MARKET]` (KR: `commission_rate`, `sell_tax_rate` / US: `commission_rate`, `sec_fee_rate`, `finra_taf_per_share`, `finra_taf_cap`), `["execution"]["slippage_bps"]`

---

### Task 1: 회전율 지표

**Files:**
- Modify: `src/tradingbot/report/metrics.py`
- Test: `tests/test_metrics_turnover.py`

**Interfaces:**
- Consumes: `BacktestResult`, `Fill`, `OrderSide` (기존)
- Produces:
  - `metrics.annual_turnover(result: BacktestResult) -> float` — 편도 연 회전율. 측정 불가 시 NaN

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_metrics_turnover.py`:

```python
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide
from tradingbot.report.metrics import annual_turnover


def make_result(fills: list[Fill], equities: list[tuple[str, float]]) -> BacktestResult:
    curve = pd.DataFrame(
        {
            "date": [pd.Timestamp(day) for day, _ in equities],
            "equity": [value for _, value in equities],
        }
    )
    return BacktestResult(
        initial_cash=equities[0][1] if equities else 0.0,
        final_equity=equities[-1][1] if equities else 0.0,
        equity_curve=curve,
        fills=fills,
        rejected_orders=[],
        expired_orders=[],
    )


def buy(qty: int, price: float, day: str = "2024-01-02") -> Fill:
    return Fill(
        order_id="x",
        symbol="AAA",
        side=OrderSide.BUY,
        qty=qty,
        price=price,
        fee=0.0,
        dt=date.fromisoformat(day),
    )


def sell(qty: int, price: float, day: str = "2024-01-02") -> Fill:
    return Fill(
        order_id="x",
        symbol="AAA",
        side=OrderSide.SELL,
        qty=qty,
        price=price,
        fee=0.0,
        dt=date.fromisoformat(day),
    )


# One full year of flat equity at 1000.
YEAR = [("2024-01-01", 1000.0), ("2024-12-31", 1000.0)]


class TestAnnualTurnover:
    def test_buying_the_whole_book_once_a_year_is_one(self):
        result = make_result([buy(10, 100.0)], YEAR)
        assert annual_turnover(result) == pytest.approx(1.0, rel=0.01)

    def test_buying_twice_the_book_is_two(self):
        result = make_result([buy(10, 100.0), buy(10, 100.0)], YEAR)
        assert annual_turnover(result) == pytest.approx(2.0, rel=0.01)

    def test_sells_do_not_count_one_way_turnover(self):
        # One-way turnover counts purchases only; counting both sides would
        # double every round trip.
        with_sells = make_result([buy(10, 100.0), sell(10, 100.0)], YEAR)
        assert annual_turnover(with_sells) == pytest.approx(1.0, rel=0.01)

    def test_annualized_over_a_half_year(self):
        half = [("2024-01-01", 1000.0), ("2024-07-01", 1000.0)]
        result = make_result([buy(10, 100.0)], half)
        # Same trading in half the time is twice the annual rate.
        assert annual_turnover(result) == pytest.approx(2.0, rel=0.02)

    def test_no_trades_is_zero(self):
        assert annual_turnover(make_result([], YEAR)) == pytest.approx(0.0)

    def test_uses_average_equity_not_final(self):
        # Equity doubles mid-run; the denominator must be the average (1500),
        # not the final value (2000), or turnover is understated.
        curve = [("2024-01-01", 1000.0), ("2024-12-31", 2000.0)]
        result = make_result([buy(15, 100.0)], curve)
        assert annual_turnover(result) == pytest.approx(1.0, rel=0.01)

    def test_empty_curve_is_nan_not_zero(self):
        # No equity curve means unmeasurable, not "no trading".
        result = make_result([buy(10, 100.0)], [])
        assert math.isnan(annual_turnover(result))

    def test_zero_average_equity_is_nan_not_infinite(self):
        result = make_result([buy(10, 100.0)], [("2024-01-01", 0.0), ("2024-12-31", 0.0)])
        assert math.isnan(annual_turnover(result))

    def test_single_point_curve_is_nan(self):
        # A zero-length period cannot be annualized.
        result = make_result([buy(10, 100.0)], [("2024-01-01", 1000.0)])
        assert math.isnan(annual_turnover(result))
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_metrics_turnover.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ImportError: cannot import name 'annual_turnover'`

- [ ] **Step 3: 구현**

`src/tradingbot/report/metrics.py` 파일 끝에 추가:

```python
def annual_turnover(result: BacktestResult) -> float:
    """One-way annual turnover: purchases divided by average equity, per year.

    "One-way" counts purchases only — counting sells too would double every
    round trip and make the number incomparable to the promotion limit.

    Returns NaN rather than 0 when the run cannot be measured (no equity
    curve, a zero-length period, or non-positive average equity). Zero would
    read as "this strategy barely trades", which is the opposite of the truth.
    """
    curve = result.equity_curve
    if curve.empty or len(curve) < 2:
        return float("nan")

    average_equity = float(curve["equity"].mean())
    if average_equity <= 0:
        return float("nan")

    start = pd.to_datetime(curve["date"].iloc[0])
    end = pd.to_datetime(curve["date"].iloc[-1])
    years = (end - start).days / 365.25
    if years <= 0:
        return float("nan")

    purchased = sum(
        fill.gross_value for fill in result.fills if fill.side is OrderSide.BUY
    )
    return purchased / average_equity / years
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_metrics_turnover.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (9 tests)

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS — 특히 `tests/test_smoke_backtest.py`의 고정값이 그대로여야 한다 (`BacktestMetrics`를 건드리지 않았으므로)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/report/metrics.py tests/test_metrics_turnover.py
git commit -m "EVAL(part): Add one-way annual turnover metric"
```

---

### Task 2: 비용 배수

**Files:**
- Create: `src/tradingbot/research/evaluation.py`
- Test: `tests/test_evaluation_costs.py`

**Interfaces:**
- Consumes: 없음 (순수 dict 변환)
- Produces:
  - `evaluation.scale_costs(config: dict, market: str, multiplier: float) -> dict` — 비용 항목만 배수 적용한 **새 dict**. 원본 불변

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_evaluation_costs.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.research.evaluation import scale_costs

CONFIG = {
    "fees": {
        "KR": {"commission_rate": 0.00015, "sell_tax_rate": 0.0015},
        "US": {
            "commission_rate": 0.0,
            "sec_fee_rate": 0.0000278,
            "finra_taf_per_share": 0.000166,
            "finra_taf_cap": 8.30,
        },
    },
    "execution": {"slippage_bps": 5},
    "backtest": {"initial_cash_kr": 10000000},
}


class TestScaleCosts:
    def test_doubles_the_markets_fee_rates(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["fees"]["KR"]["commission_rate"] == pytest.approx(0.0003)
        assert scaled["fees"]["KR"]["sell_tax_rate"] == pytest.approx(0.003)

    def test_doubles_slippage(self):
        assert scale_costs(CONFIG, "KR", 2.0)["execution"]["slippage_bps"] == pytest.approx(10)

    def test_doubles_the_taf_cap_too(self):
        # Scaling the per-share rate but not its cap would let the cap absorb
        # the increase and neuter the whole sensitivity test.
        scaled = scale_costs(CONFIG, "US", 2.0)
        assert scaled["fees"]["US"]["finra_taf_per_share"] == pytest.approx(0.000332)
        assert scaled["fees"]["US"]["finra_taf_cap"] == pytest.approx(16.60)

    def test_leaves_other_markets_alone(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["fees"]["US"]["sec_fee_rate"] == pytest.approx(0.0000278)

    def test_leaves_non_cost_settings_alone(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["backtest"]["initial_cash_kr"] == 10000000

    def test_does_not_mutate_the_original(self):
        scale_costs(CONFIG, "KR", 2.0)
        assert CONFIG["fees"]["KR"]["commission_rate"] == pytest.approx(0.00015)
        assert CONFIG["execution"]["slippage_bps"] == 5

    def test_multiplier_of_one_is_a_faithful_copy(self):
        scaled = scale_costs(CONFIG, "KR", 1.0)
        assert scaled["fees"]["KR"] == CONFIG["fees"]["KR"]
        assert scaled is not CONFIG

    def test_market_is_case_insensitive(self):
        assert scale_costs(CONFIG, "kr", 2.0)["fees"]["KR"]["commission_rate"] == pytest.approx(0.0003)

    def test_missing_market_section_is_not_an_error(self):
        # A config without fees for this market still gets slippage scaled.
        bare = {"execution": {"slippage_bps": 5}}
        assert scale_costs(bare, "KR", 2.0)["execution"]["slippage_bps"] == pytest.approx(10)

    def test_negative_multiplier_rejected(self):
        with pytest.raises(ValueError):
            scale_costs(CONFIG, "KR", -1.0)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_costs.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.evaluation'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/evaluation.py` (이 태스크 범위는 `scale_costs`까지 — Walk-forward와 판정은 Task 3·4):

```python
"""Measure a strategy against the promotion criteria.

Three of the six criteria — walk-forward win rate, annual turnover, and
cost-doubling sensitivity — had no harness at all, so no strategy could
earn a pass. This module supplies them by composing the existing backtest
engine rather than adding new simulation logic.

Nothing here searches for better parameters. It measures; a human decides
what to change. A search loop bolted onto a measurement tool is automated
overfitting.
"""

from __future__ import annotations

import copy
from typing import Any


def scale_costs(config: dict[str, Any], market: str, multiplier: float) -> dict[str, Any]:
    """Return a copy of `config` with this market's trading costs multiplied.

    Scales every numeric entry under `[fees.<market>]` plus
    `[execution] slippage_bps`. The FINRA TAF cap scales with its rate on
    purpose: leaving the cap fixed would let it absorb the increase and
    neuter the sensitivity test.

    The input config is never mutated — callers run the same config at 1x
    and 2x and must not have the first run contaminate the second.
    """
    if multiplier < 0:
        raise ValueError("multiplier must be non-negative")

    scaled = copy.deepcopy(config)
    market_fees = scaled.get("fees", {}).get(market.upper())
    if market_fees:
        for name, value in market_fees.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                market_fees[name] = value * multiplier

    execution = scaled.setdefault("execution", {})
    slippage = execution.get("slippage_bps")
    if isinstance(slippage, (int, float)) and not isinstance(slippage, bool):
        execution["slippage_bps"] = slippage * multiplier
    return scaled
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_costs.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/evaluation.py tests/test_evaluation_costs.py
git commit -m "EVAL(part): Add cost scaling for sensitivity checks"
```

---

### Task 3: Walk-forward 구간별 백테스트

**Files:**
- Modify: `src/tradingbot/research/evaluation.py`
- Test: `tests/test_evaluation_walk_forward.py`

**Interfaces:**
- Consumes: `scale_costs` (Task 2), `walk_forward_windows` (기존), `run_backtest` (기존)
- Produces:
  - `evaluation.WindowResult` (frozen dataclass): `test_start: date`, `test_end: date`, `strategy_return_pct: float`, `benchmark_return_pct: float`, `won: bool | None`, `error: str` — 실패 구간은 `won=None`, `error`에 사유
  - `evaluation.run_walk_forward(*, config, benchmark_config, market, symbols, strategy_name, windows, runner=run_backtest) -> list[WindowResult]`
  - `evaluation.win_rate(results: Sequence[WindowResult]) -> float` — 유효 구간만 분모. 유효 0개면 NaN

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_evaluation_walk_forward.py`:

```python
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

CONFIG = {"backtest": {"initial_cash_us": 100000}}
BENCHMARK_CONFIG = {"backtest": {"initial_cash_us": 100000}}


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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_walk_forward.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ImportError: cannot import name 'WindowResult'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/evaluation.py`의 임포트를 다음으로 확장:

```python
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Sequence

from tradingbot.research.walk_forward import WalkForwardWindow
from tradingbot.services import run_backtest
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)
```

`scale_costs` 아래에 추가:

```python
@dataclass(frozen=True)
class WindowResult:
    """One rolling out-of-sample window: how the strategy did against the benchmark.

    `won` is None when the window could not be evaluated; `error` says why.
    A failed window is unmeasured, not a loss.
    """

    test_start: date
    test_end: date
    strategy_return_pct: float
    benchmark_return_pct: float
    won: bool | None
    error: str


def run_walk_forward(
    *,
    config: dict[str, Any],
    benchmark_config: dict[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    windows: Sequence[WalkForwardWindow],
    runner: Callable[..., Any] = run_backtest,
) -> list[WindowResult]:
    """Backtest strategy and benchmark over each window's test segment.

    Only the test segment runs. The train segment is unused because nothing
    in this strategy is fitted to data — what this measures is consistency
    across independent rolling periods, and the report says so plainly.

    A window that fails is recorded with `won=None` rather than dropped, so
    the win rate cannot be quietly inflated by discarding hard periods.
    """
    results: list[WindowResult] = []
    for window in windows:
        start = window.test_start.isoformat()
        end = window.test_end.isoformat()
        try:
            strategy_result = runner(
                config,
                market=market,
                symbols=list(symbols),
                strategy_name=strategy_name,
                start=start,
                end=end,
            )
            benchmark_result = runner(
                benchmark_config,
                market=market,
                symbols=list(symbols),
                strategy_name=strategy_name,
                start=start,
                end=end,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            LOGGER.exception("Walk-forward window %s..%s failed", start, end)
            results.append(
                WindowResult(
                    test_start=window.test_start,
                    test_end=window.test_end,
                    strategy_return_pct=float("nan"),
                    benchmark_return_pct=float("nan"),
                    won=None,
                    error=str(exc),
                )
            )
            continue

        strategy_return = float(strategy_result.return_pct)
        benchmark_return = float(benchmark_result.return_pct)
        results.append(
            WindowResult(
                test_start=window.test_start,
                test_end=window.test_end,
                strategy_return_pct=strategy_return,
                benchmark_return_pct=benchmark_return,
                won=strategy_return > benchmark_return,
                error="",
            )
        )
    return results


def win_rate(results: Sequence[WindowResult]) -> float:
    """Share of evaluated windows the strategy won. NaN when none were evaluated."""
    evaluated = [result for result in results if result.won is not None]
    if not evaluated:
        return float("nan")
    return sum(1 for result in evaluated if result.won) / len(evaluated)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_walk_forward.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/evaluation.py tests/test_evaluation_walk_forward.py
git commit -m "EVAL(part): Run strategy and benchmark over walk-forward windows"
```

---

### Task 4: 승격 판정 조립

**Files:**
- Modify: `src/tradingbot/research/evaluation.py`
- Test: `tests/test_evaluation_verdict.py`

**Interfaces:**
- Consumes: `annual_turnover` (Task 1), `scale_costs` (Task 2), `run_walk_forward`·`win_rate` (Task 3), `calculate_metrics` (기존)
- Produces:
  - `evaluation.CriterionResult` (frozen dataclass): `name: str`, `threshold: str`, `measured: float`, `passed: bool | None` — 미측정은 `passed=None`
  - `evaluation.Verdict` (frozen dataclass): `promoted: bool`, `criteria: list[CriterionResult]`; property `unmeasured: list[str]`
  - `evaluation.judge(*, excess_return, sharpe, mdd, turnover, wf_win_rate, excess_return_2x, promotion: dict) -> Verdict`

**설계 근거:** 판정은 순수 함수다. 백테스트 실행과 분리해야 "미측정이 통과로 둔갑하지 않는다"는 규칙을 값만으로 테스트할 수 있다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_evaluation_verdict.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.research.evaluation import judge

PROMOTION = {
    "min_excess_return": 0.0,
    "min_sharpe": 0.5,
    "max_mdd": 0.25,
    "max_annual_turnover": 6.0,
    "min_walk_forward_win_rate": 0.6,
    "cost_multiplier_check": 2.0,
}

PASSING = {
    "excess_return": 2.0,
    "sharpe": 1.2,
    "mdd": 0.18,
    "turnover": 3.0,
    "wf_win_rate": 0.7,
    "excess_return_2x": 1.0,
}


def verdict(**overrides):
    values = {**PASSING, **overrides}
    return judge(promotion=PROMOTION, **values)


class TestJudge:
    def test_all_criteria_met_is_promoted(self):
        result = verdict()
        assert result.promoted is True
        assert all(criterion.passed for criterion in result.criteria)

    def test_reports_all_six_criteria(self):
        assert len(verdict().criteria) == 6

    def test_negative_excess_return_blocks_promotion(self):
        result = verdict(excess_return=-1.0)
        assert result.promoted is False
        assert any(c.name == "excess_return" and c.passed is False for c in result.criteria)

    def test_low_sharpe_blocks(self):
        assert verdict(sharpe=0.4).promoted is False

    def test_deep_drawdown_blocks(self):
        assert verdict(mdd=0.33).promoted is False

    def test_excessive_turnover_blocks(self):
        assert verdict(turnover=7.0).promoted is False

    def test_low_win_rate_blocks(self):
        assert verdict(wf_win_rate=0.4).promoted is False

    def test_failing_the_doubled_cost_check_blocks(self):
        assert verdict(excess_return_2x=-0.5).promoted is False

    def test_unmeasured_criterion_is_not_a_pass(self):
        # The whole point of this tool: NaN must never read as "passed".
        result = verdict(wf_win_rate=float("nan"))
        criterion = next(c for c in result.criteria if c.name == "walk_forward_win_rate")
        assert criterion.passed is None
        assert result.promoted is False

    def test_unmeasured_criteria_are_listed(self):
        result = verdict(turnover=float("nan"), wf_win_rate=float("nan"))
        assert set(result.unmeasured) == {"annual_turnover", "walk_forward_win_rate"}

    def test_no_unmeasured_when_everything_measured(self):
        assert verdict().unmeasured == []

    def test_boundary_values_pass(self):
        # Thresholds are inclusive: exactly at the limit is acceptable.
        result = verdict(sharpe=0.5, mdd=0.25, turnover=6.0, wf_win_rate=0.6, excess_return=0.0)
        assert result.promoted is True
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_verdict.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ImportError: cannot import name 'judge'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/evaluation.py` 상단 임포트에 `math`를 추가하고, 파일 끝에 다음을 추가:

```python
@dataclass(frozen=True)
class CriterionResult:
    """One promotion criterion. `passed=None` means it could not be measured."""

    name: str
    threshold: str
    measured: float
    passed: bool | None


@dataclass(frozen=True)
class Verdict:
    promoted: bool
    criteria: list[CriterionResult]

    @property
    def unmeasured(self) -> list[str]:
        return [c.name for c in self.criteria if c.passed is None]


def _at_least(measured: float, floor: float) -> bool | None:
    if math.isnan(measured):
        return None
    return measured >= floor


def _at_most(measured: float, ceiling: float) -> bool | None:
    if math.isnan(measured):
        return None
    return measured <= ceiling


def judge(
    *,
    excess_return: float,
    sharpe: float,
    mdd: float,
    turnover: float,
    wf_win_rate: float,
    excess_return_2x: float,
    promotion: dict[str, Any],
) -> Verdict:
    """Compare measurements against the promotion criteria.

    An unmeasured criterion (NaN) is never a pass — it blocks promotion just
    as a failure does. Distinguishing "we checked and it's fine" from "we
    never checked" is the reason this tool exists.
    """
    criteria = [
        CriterionResult(
            "excess_return",
            f">= {promotion['min_excess_return']}",
            excess_return,
            _at_least(excess_return, float(promotion["min_excess_return"])),
        ),
        CriterionResult(
            "sharpe",
            f">= {promotion['min_sharpe']}",
            sharpe,
            _at_least(sharpe, float(promotion["min_sharpe"])),
        ),
        CriterionResult(
            "max_drawdown",
            f"<= {promotion['max_mdd']}",
            mdd,
            _at_most(mdd, float(promotion["max_mdd"])),
        ),
        CriterionResult(
            "annual_turnover",
            f"<= {promotion['max_annual_turnover']}",
            turnover,
            _at_most(turnover, float(promotion["max_annual_turnover"])),
        ),
        CriterionResult(
            "walk_forward_win_rate",
            f">= {promotion['min_walk_forward_win_rate']}",
            wf_win_rate,
            _at_least(wf_win_rate, float(promotion["min_walk_forward_win_rate"])),
        ),
        CriterionResult(
            f"excess_return_at_{promotion['cost_multiplier_check']}x_costs",
            f">= {promotion['min_excess_return']}",
            excess_return_2x,
            _at_least(excess_return_2x, float(promotion["min_excess_return"])),
        ),
    ]
    return Verdict(promoted=all(c.passed for c in criteria), criteria=criteria)
```

주의: `all(c.passed ...)`에서 `None`은 falsy이므로 미측정이 자동으로 승격을 막는다. 이것이 의도된 동작이다.

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_verdict.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (12 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/evaluation.py tests/test_evaluation_verdict.py
git commit -m "EVAL(part): Judge measurements against the promotion criteria"
```

---

### Task 5: 평가 실행·리포트·CLI·설정

**Files:**
- Modify: `src/tradingbot/research/evaluation.py`
- Modify: `src/tradingbot/cli.py`
- Modify: `config/us_etf_rotation.toml`, `config/us_etf_benchmark.toml`
- Modify: `README.md`, `docs/architecture.md`
- Test: `tests/test_evaluation_report.py`

**Interfaces:**
- Consumes: Task 1~4 전부, `load_research_config`·`record_experiment`·`resolve_project_path`·`load_config` (기존)
- Produces:
  - `evaluation.evaluate_strategy(*, config, benchmark_config, research, market, symbols, strategy_name, start, end=None, runner=run_backtest) -> dict` — 측정값·구간결과·판정을 담은 dict
  - `evaluation.render_markdown(report: dict) -> str`
  - CLI: `tradingbot research evaluate --strategy S --market M --symbols ... --start D [--end D] [--benchmark-config P] [--research-config P] [--out P]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_evaluation_report.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_report.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ImportError: cannot import name 'evaluate_strategy'`

- [ ] **Step 3: `evaluate_strategy`와 `render_markdown` 구현**

`src/tradingbot/research/evaluation.py` 상단 임포트에 추가:

```python
from tradingbot.report.metrics import annual_turnover, calculate_metrics
from tradingbot.research.walk_forward import walk_forward_windows
```

파일 끝에 추가:

```python
def _measure(result: Any) -> dict[str, float]:
    metrics, _, _ = calculate_metrics(result)
    return {
        "total_return_pct": float(result.return_pct),
        "cagr_pct": float(metrics.cagr_pct),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "sharpe": float(metrics.sharpe),
        "annual_turnover": float(annual_turnover(result)),
        "trades": int(result.trade_count),
        "rejected_orders": len(result.rejected_orders),
    }


def evaluate_strategy(
    *,
    config: dict[str, Any],
    benchmark_config: dict[str, Any],
    research: dict[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    start: str,
    end: str | None = None,
    runner: Callable[..., Any] = run_backtest,
) -> dict[str, Any]:
    """Measure a strategy against every promotion criterion.

    Runs the full period for strategy and benchmark, repeats it at doubled
    costs, walks rolling out-of-sample windows, and judges the result.
    """
    promotion = research["promotion"]
    multiplier = float(promotion["cost_multiplier_check"])

    def backtest(active_config: dict[str, Any]) -> Any:
        return runner(
            active_config,
            market=market,
            symbols=list(symbols),
            strategy_name=strategy_name,
            start=start,
            end=end,
        )

    strategy_result = backtest(config)
    benchmark_result = backtest(benchmark_config)
    strategy = _measure(strategy_result)
    benchmark = _measure(benchmark_result)

    strategy_2x = _measure(backtest(scale_costs(config, market, multiplier)))
    benchmark_2x = _measure(backtest(scale_costs(benchmark_config, market, multiplier)))

    wf_config = research.get("walk_forward", {})
    windows = walk_forward_windows(
        date.fromisoformat(start),
        date.fromisoformat(end) if end else date.today(),
        train_years=int(wf_config.get("train_years", 3)),
        test_years=int(wf_config.get("test_years", 1)),
        step_years=int(wf_config.get("step_years", 1)),
    )
    window_results = run_walk_forward(
        config=config,
        benchmark_config=benchmark_config,
        market=market,
        symbols=symbols,
        strategy_name=strategy_name,
        windows=windows,
        runner=runner,
    )

    # Both excess figures are annualized (CAGR difference in percentage
    # points). Mixing CAGR here and total return there would make the cost
    # check incomparable to the headline criterion it is supposed to stress.
    excess_return = strategy["cagr_pct"] - benchmark["cagr_pct"]
    excess_return_2x = strategy_2x["cagr_pct"] - benchmark_2x["cagr_pct"]
    verdict = judge(
        excess_return=excess_return,
        sharpe=strategy["sharpe"],
        mdd=abs(strategy["max_drawdown_pct"]) / 100.0,
        turnover=strategy["annual_turnover"],
        wf_win_rate=win_rate(window_results),
        excess_return_2x=excess_return_2x,
        promotion=promotion,
    )

    return {
        "strategy_name": strategy_name,
        "market": market.upper(),
        "symbols": list(symbols),
        "period": {"start": start, "end": end},
        "strategy": strategy,
        "benchmark": benchmark,
        "excess_return_pct": excess_return,
        "cost_2x": {
            "multiplier": multiplier,
            "strategy_total_return_pct": strategy_2x["total_return_pct"],
            "benchmark_total_return_pct": benchmark_2x["total_return_pct"],
            "strategy_cagr_pct": strategy_2x["cagr_pct"],
            "benchmark_cagr_pct": benchmark_2x["cagr_pct"],
            "excess_return_pct": excess_return_2x,
        },
        "walk_forward": {
            "win_rate": win_rate(window_results),
            "train_segments_used": False,
            "windows": [
                {
                    "test_start": result.test_start.isoformat(),
                    "test_end": result.test_end.isoformat(),
                    "strategy_return_pct": result.strategy_return_pct,
                    "benchmark_return_pct": result.benchmark_return_pct,
                    "won": result.won,
                    "error": result.error,
                }
                for result in window_results
            ],
        },
        "verdict": {
            "promoted": verdict.promoted,
            "unmeasured": verdict.unmeasured,
            "criteria": [
                {
                    "name": c.name,
                    "threshold": c.threshold,
                    "measured": c.measured,
                    "passed": c.passed,
                }
                for c in verdict.criteria
            ],
        },
    }


def _verdict_sentence(report: dict[str, Any]) -> str:
    """Plain-language conclusion — no jargon, for the person deciding."""
    verdict = report["verdict"]
    failed = [c["name"] for c in verdict["criteria"] if c["passed"] is False]
    unmeasured = verdict["unmeasured"]

    if verdict["promoted"]:
        return (
            "이 전략은 승격 기준 6개를 모두 충족했습니다. 다음 단계(모의투자)로 "
            "넘길 수 있습니다."
        )
    parts = ["이 전략은 아직 실제 자금을 넣을 단계가 아닙니다."]
    if failed:
        parts.append(f"기준에 미달한 항목: {', '.join(failed)}.")
    if unmeasured:
        parts.append(
            f"측정하지 못한 항목: {', '.join(unmeasured)} — 미달이 아니라 "
            "확인이 안 된 것이며, 확인 전에는 통과로 치지 않습니다."
        )
    return " ".join(parts)


def render_markdown(report: dict[str, Any]) -> str:
    strategy = report["strategy"]
    benchmark = report["benchmark"]
    period = report["period"]
    lines = [
        f"# {report['strategy_name']} 승격 평가 ({report['market']})",
        "",
        "## 결론",
        "",
        _verdict_sentence(report),
        "",
        f"- 기간: {period['start']} ~ {period['end'] or '최신'}",
        f"- 종목: {', '.join(report['symbols'])}",
        "",
        "## 성과",
        "",
        "| 지표 | 전략 | 벤치마크 |",
        "|---|---|---|",
        f"| 총수익률 | {strategy['total_return_pct']:.2f}% | {benchmark['total_return_pct']:.2f}% |",
        f"| CAGR | {strategy['cagr_pct']:.2f}% | {benchmark['cagr_pct']:.2f}% |",
        f"| MDD | {strategy['max_drawdown_pct']:.2f}% | {benchmark['max_drawdown_pct']:.2f}% |",
        f"| Sharpe | {strategy['sharpe']:.2f} | {benchmark['sharpe']:.2f} |",
        f"| 연 회전율 | {strategy['annual_turnover']:.2f} | {benchmark['annual_turnover']:.2f} |",
        f"| 체결수 | {strategy['trades']} | {benchmark['trades']} |",
        f"| 거부 주문 | {strategy['rejected_orders']} | {benchmark['rejected_orders']} |",
        "",
        "## 승격 기준",
        "",
        "| 기준 | 임계값 | 실측 | 판정 |",
        "|---|---|---|---|",
    ]
    for criterion in report["verdict"]["criteria"]:
        if criterion["passed"] is None:
            mark = "**측정 불가**"
        elif criterion["passed"]:
            mark = "통과"
        else:
            mark = "**미달**"
        lines.append(
            f"| {criterion['name']} | {criterion['threshold']} | "
            f"{criterion['measured']:.4f} | {mark} |"
        )

    cost = report["cost_2x"]
    lines += [
        "",
        f"## 비용 {cost['multiplier']:g}배 검정",
        "",
        "전략과 벤치마크 모두 같은 배수의 비용을 뭅니다 — 초과수익은 상대 개념이라",
        "한쪽만 올리면 비교가 성립하지 않습니다.",
        "",
        f"- 전략: 총수익률 {cost['strategy_total_return_pct']:.2f}% / CAGR {cost['strategy_cagr_pct']:.2f}%",
        f"- 벤치마크: 총수익률 {cost['benchmark_total_return_pct']:.2f}% / CAGR {cost['benchmark_cagr_pct']:.2f}%",
        f"- 초과수익(CAGR 기준): {cost['excess_return_pct']:.2f}%p",
        "",
        "## 구간별 결과 (롤링 독립구간)",
        "",
        "학습 구간은 사용하지 않습니다. 이 전략은 파라미터를 데이터로 맞추지 않는",
        "규칙 기반이라 학습할 것이 없고, 따라서 이 표가 재는 것은 '여러 시기에 걸친",
        "일관성'입니다. 나중에 파라미터를 튜닝하기 시작하면 학습 구간이 실제 의미를",
        "갖게 됩니다.",
        "",
        "| 구간 | 전략 | 벤치마크 | 결과 |",
        "|---|---|---|---|",
    ]
    for window in report["walk_forward"]["windows"]:
        if window["won"] is None:
            outcome = f"측정 실패 ({window['error']})"
            numbers = "— | —"
        else:
            outcome = "승" if window["won"] else "패"
            numbers = (
                f"{window['strategy_return_pct']:.2f}% | "
                f"{window['benchmark_return_pct']:.2f}%"
            )
        lines.append(
            f"| {window['test_start']} ~ {window['test_end']} | {numbers} | {outcome} |"
        )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: CLI 추가**

`src/tradingbot/cli.py`의 `build_parser()`에서 `factor_report_parser.set_defaults(handler=cmd_research_report)` 다음에 추가:

```python
    evaluate_parser = research_subparsers.add_parser(
        "evaluate", help="Measure a strategy against the promotion criteria"
    )
    evaluate_parser.add_argument("--strategy", required=True)
    evaluate_parser.add_argument("--market", choices=["KR", "US"], required=True)
    evaluate_parser.add_argument("--symbols", nargs="+", required=True)
    evaluate_parser.add_argument("--start", required=True)
    evaluate_parser.add_argument("--end", default=None)
    evaluate_parser.add_argument(
        "--benchmark-config", default=None, help="Benchmark TOML (default: same as --config)"
    )
    evaluate_parser.add_argument("--research-config", default=None)
    evaluate_parser.add_argument("--data-root", default=None)
    evaluate_parser.add_argument("--out", default="reports/evaluation")
    evaluate_parser.set_defaults(handler=cmd_research_evaluate)
```

파일 끝에 핸들러를 추가:

```python
def cmd_research_evaluate(args) -> int:
    from datetime import datetime as _dt

    from tradingbot.research.evaluation import evaluate_strategy, render_markdown
    from tradingbot.research.experiment import record_experiment
    from tradingbot.research.gate import load_research_config

    config = load_config(args.config)
    benchmark_config = (
        load_config(args.benchmark_config) if args.benchmark_config else config
    )
    research = load_research_config(args.research_config)

    report = evaluate_strategy(
        config=config,
        benchmark_config=benchmark_config,
        research=research,
        market=args.market,
        symbols=args.symbols,
        strategy_name=args.strategy,
        start=args.start,
        end=args.end,
    )
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (
        f"{_dt.now():%Y%m%d_%H%M%S}_{args.strategy}_{args.market.upper()}.md"
    )
    out_path.write_text(markdown, encoding="utf-8")
    print(f"평가 리포트: {out_path}")

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="strategy_evaluation",
        params={
            "strategy": args.strategy,
            "market": args.market,
            "symbols": args.symbols,
            "start": args.start,
            "end": args.end,
            "benchmark_config": args.benchmark_config,
        },
        metrics={
            "promoted": report["verdict"]["promoted"],
            "unmeasured": report["verdict"]["unmeasured"],
            "excess_return_pct": report["excess_return_pct"],
            "walk_forward_win_rate": report["walk_forward"]["win_rate"],
            "annual_turnover": report["strategy"]["annual_turnover"],
        },
    )
    print(f"실험 기록: {experiment_path}")
    return 0 if report["verdict"]["promoted"] else 1
```

`resolve_project_path`와 `load_config`가 `cli.py` 상단에 이미 임포트되어 있는지 확인하고, 없으면 추가한다.

- [ ] **Step 5: 초기자본 수정**

`config/us_etf_rotation.toml`과 `config/us_etf_benchmark.toml`에서:

```toml
initial_cash_us = 100000
```

두 파일의 `[backtest]` 섹션에 다음 주석을 붙인다:

```toml
# 1만 달러에서는 ETF 11종 동일비중이 종목당 900달러라, SPY 한 주(수백 달러)
# 반올림에서 주문의 17~25%가 거부됐다. 그 왜곡은 11종목 벤치마크를 3종목
# 전략보다 훨씬 심하게 때려 비교 자체를 오염시킨다. 목적이 공정한 비교이므로
# 실제 투자금과 같을 필요는 없다.
```

- [ ] **Step 6: 통과 확인 + 전체 회귀**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_evaluation_report.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (9 tests)

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS — `tests/test_smoke_backtest.py`의 고정값이 그대로여야 한다

- [ ] **Step 7: 실데이터 검증 (완료 기준)**

미국 전략을 실제로 평가한다 (네트워크 불필요 — 캐시된 가격만 읽는다):

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml research evaluate --strategy theme_multifactor --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01 --benchmark-config config\us_etf_benchmark.toml
```

Expected: 승격 기준 6개 **전부에 실측값**이 찍힌 표가 출력되고 `reports/evaluation/`에 저장된다. **판정 결과가 무엇이든 그대로 기록한다** — 불합격이면 불합격이다. 종료코드는 승격 시 0, 아니면 1.

한국 전략도 같은 방식으로 평가한다:

```powershell
.\.venv\Scripts\python.exe -m tradingbot research evaluate --strategy theme_multifactor --market KR --symbols 005930 000660 042700 058470 240810 --start 2023-01-01 --benchmark-config config\benchmark_equal.toml
```

Expected: 한국은 기간이 짧아(2023~) Walk-forward 구간이 만들어지지 않을 수 있다. 그러면 승률은 **NaN이고 "측정 불가"로 표시**되어야 하며, 통과로 둔갑하면 안 된다. 실제 출력을 보고에 기록한다.

- [ ] **Step 8: 문서 갱신**

`README.md`의 확장 목록에 추가:

```markdown
- 승격 기준 측정 도구(`research/evaluation.py`, `research evaluate`):
  Walk-forward 승률·연 회전율·비용 2배 검정을 실제로 재서, 전략이 승격
  기준 6개 전부에 대해 합격/불합격 판정을 받을 수 있게 합니다. 측정하지
  못한 항목은 통과가 아니라 "측정 불가"로 남습니다.
```

`README.md`의 백테스트 섹션 뒤에 사용법을 추가:

```markdown
## 전략 승격 평가

백테스트 수치가 좋아 보여도 실제로 돈을 넣어도 되는지는 별개입니다. 이 명령은
`config/research.toml`의 `[promotion]` 기준 6개를 전부 측정해 합격/불합격을
판정하고, 리포트 맨 위에 전문용어 없는 결론을 씁니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml research evaluate --strategy theme_multifactor --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01 --benchmark-config config\us_etf_benchmark.toml
```

결과는 `reports/evaluation/`에 저장됩니다. 승격이면 종료코드 0, 아니면 1입니다.
```

`docs/architecture.md` §7 표에 행 추가:

```markdown
| 승격 기준 측정 / 판정 | `src/tradingbot/research/evaluation.py` |
```

- [ ] **Step 9: 커밋**

```powershell
git add src/tradingbot/research/evaluation.py src/tradingbot/cli.py config/us_etf_rotation.toml config/us_etf_benchmark.toml tests/test_evaluation_report.py README.md docs/architecture.md
git commit -m "EVAL: Add the research evaluate command and promotion report"
```

---

## 완료 기준 (스펙 §2, §7)

- [ ] `research evaluate`가 승격 기준 6개 전부에 실측값을 채운 판정표를 낸다.
- [ ] 측정 실패 항목은 NaN·"측정 불가"로 남고 통과로 처리되지 않으며, 판정도 합격이 될 수 없다.
- [ ] 실패한 Walk-forward 구간이 승률 분모에서 조용히 빠지지 않는다.
- [ ] 리포트 맨 위에 전문용어 없는 결론이 있다.
- [ ] 미국 초기자본이 10만 달러로 올라가 주문 거부 왜곡이 줄어든다.
- [ ] 기존 백테스트·HTML 리포트 결과가 이번 변경으로 달라지지 않는다.
- [ ] 전체 테스트 통과.

## 알려진 한계 (범위 밖)

- **파라미터 탐색은 넣지 않는다.** 측정 도구에 탐색기를 붙이면 과최적화가 자동화된다. 무엇을 바꿀지는 사람이 결정한다.
- 회전율을 HTML 백테스트 리포트에 노출하지 않는다. 필요해지면 별도로 결정한다.
- 벤치마크가 `--benchmark-config` 없이 실행되면 전략 설정과 동일해져 초과수익이 0이 된다. 이는 사용자 실수를 조용히 감추지 않고 표에 0.0으로 드러나는 편이 낫다는 판단이며, 리포트의 종목·기간 표기로 확인 가능하다.
- 한국 전략은 데이터 기간이 짧아(2023~) Walk-forward 구간이 부족할 수 있다. 그 경우 "측정 불가"가 정직한 결과이고, 기간을 늘리려면 2021~2022 패널 데이터 수집이 선행되어야 한다.
