# Phase 4: 테마 멀티팩터 전략 + 리밸런싱 (M11 변형 + M12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 3의 종합점수를 실제 매매로 잇는다 — 상위 N종목 선정 → 비중 산출 → 제약 적용 → 리밸런싱 주문을 기존 백테스트·모의투자 엔진에서 실행하는 `theme_multifactor` 전략.

**Architecture:** 순수 계산 계층(`allocation/`: 선정·비중·제약·주문계획)과 엔진 어댑터(`strategies/theme_multifactor.py`)를 분리한다. 엔진은 종목별 `on_bar`만 제공하므로 어댑터가 "새 날짜의 첫 on_bar"에서 하루 1회 판단하고, 리밸런싱일(캘린더로 판정, 가격 룩어헤드 없음) 종가에 MARKET 주문을 내면 기존 흐름대로 다음 거래일 시가에 체결된다. 중복 주문은 기존 `SignalLedger`로 차단한다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-07-19-kr-theme-multifactor-design.md` §9(전략과 포트폴리오), §10(오류 처리)

## Global Constraints

- **신규 의존성 추가 금지.**
- **스펙 §9.1의 `portfolio/` 패키지명은 `allocation/`으로 변경한다** — 기존 `src/tradingbot/portfolio.py`(계좌 모듈)와 이름이 충돌하기 때문. 승인된 의도적 편차이며 커밋 메시지와 문서에 남긴다.
- **스펙 §9.2의 `generate_targets(date, universe, data_store, portfolio)`에서 `portfolio` 인자는 제외한다** — 현재 보유 상태는 어댑터(`on_bar`)가 ctx에서 읽어 `plan_rebalance`에 넘긴다. 목표 산출을 보유와 독립적인 순수 함수로 유지해 테스트를 단순하게 만들기 위한 의도적 편차.
- **종가 신호 → 다음 거래일 시가 체결**: 전략은 CLOSE 단계에서 MARKET 주문만 낸다(엔진이 다음 SessionOpen에서 체결). 당일 체결을 시도하는 코드를 만들지 않는다.
- **리밸런싱일 판정은 거래소 캘린더로만** 한다 (`get_calendar(market).next_trading_day`). 가격 데이터로 판정하지 않는다 — 캘린더는 정적이라 룩어헤드가 아니다.
- **Point-in-Time**: 팩터 계산은 `ParquetDataStore(price_history/panel/panel_latest)`만 사용. `close_series` 호출 금지.
- **팩터 가중치 계약(Phase 3 이월 해소)**: 전략은 `research.toml [factor_weights]`의 키를 순회하며 `get_factor(name)`을 호출해 점수를 만든다. 오타 팩터명은 `get_factor`가 즉시 ValueError로 거부한다. `transform.combine`에는 **weights 키와 정확히 같은 키의 scores만** 전달해, 조용한 0-가중치 경로를 구조적으로 통과 불가능하게 한다.
- **데이터 신선도 게이트(스펙 §10)**: 종합점수가 전 종목 NaN이면 리밸런싱을 건너뛰고 경고를 남긴다. 오래된/없는 데이터로 주문하지 않는다.
- **중복 주문 차단**: 모든 주문은 기존 `SignalLedger.claim()`을 통과해야 한다. 같은 결정의 재실행은 no-op.
- 백테스트 실행 경로에서 네트워크 접근 금지. 테스트도 네트워크 금지(고정 fixture만).
- 기존 테스트·기존 CLI·기존 전략 3종의 동작 변경 금지. `Strategy` ABC 시그니처 변경 금지.
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 접두사: 중간 `M12(part):`, 마지막 `M12:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `Strategy` (`strategies/base.py`): `name: ClassVar[str]`, `default_params: ClassVar[dict]`, `__init__(**params)`→`self.params`, `on_bar(ctx, bar)` 필수, `snapshot_state()/restore_state(dict)`, `persist_state()`, `self._state_store`
- `StrategyContext`: `history(symbol, n)->DataFrame`, `position(symbol)->Position(qty, avg_price, last_price, market_value)`, `cash()`, `equity()`, `has_open_order(symbol, side)`, `buy(symbol, qty=None, weight=None, ...)`, `sell(symbol, qty, ...)` — `weight`는 `equity()*weight/추정가`로 수량 환산됨
- `Bar`: `symbol, dt(date), open, high, low, close, volume`
- `SignalLedger` (`strategies/signals.py`): `SignalLedger(strategy_name, store)`, `make_signal_id(strategy, date, symbol, side, target_weight)`, `.claim(id)->bool`
- `get_calendar(market)` (`engine/calendar.py`): `.next_trading_day(date)->date`
- `get_theme(key, path=None)`, `members(theme, dt)->list[str]` (`data/universe.py`)
- `get_factor(name)` (`factors/registry.py`), `standardize(series)`, `combine(scores, weights, *, min_factors)` (`factors/transform.py`)
- `market_regime(store, dt, *, series, ma_days)->str`, `equity_exposure(state, *, bull, bear)->float` (`research/regime.py`)
- `load_research_config(path=None)->dict` (`research/gate.py`)
- `ParquetDataStore(cache, market, processed_root=None)` (`data/store.py`)
- `build_strategy(config, name)` (`services.py`): `config["strategies"][name]` dict를 `**params`로 전달

---

### Task 1: 종목 선정과 비중 (`allocation/ranking.py`, `allocation/weights.py`)

**Files:**
- Create: `src/tradingbot/allocation/__init__.py` (빈 파일)
- Create: `src/tradingbot/allocation/ranking.py`
- Create: `src/tradingbot/allocation/weights.py`
- Test: `tests/test_allocation_ranking.py`, `tests/test_allocation_weights.py`

**Interfaces:**
- Consumes: pandas만.
- Produces (이후 태스크가 사용):
  - `ranking.select_top(scores: pd.Series, top_n: int) -> list[str]` — NaN 제외, 점수 내림차순, 동점은 심볼 오름차순(재현성), 유효 종목이 top_n보다 적으면 있는 만큼만
  - `weights.equal_weights(symbols: Sequence[str]) -> dict[str, float]`
  - `weights.realized_volatility(closes: pd.Series, days: int) -> float` — 일간수익률 표준편차, 표본 부족 시 NaN
  - `weights.inverse_volatility_weights(volatilities: dict[str, float]) -> dict[str, float]` — 1/σ 정규화, NaN/0/음수 σ 종목은 제외, 유효 σ가 하나도 없으면 동일비중 폴백
  - `weights.scale_weights(weights: dict[str, float], factor: float) -> dict[str, float]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_allocation_ranking.py`:

```python
from __future__ import annotations

import pandas as pd

from tradingbot.allocation.ranking import select_top


class TestSelectTop:
    def test_takes_highest_scores(self):
        scores = pd.Series({"AAA": 1.0, "BBB": 3.0, "CCC": 2.0})
        assert select_top(scores, 2) == ["BBB", "CCC"]

    def test_nan_scores_are_excluded(self):
        scores = pd.Series({"AAA": 1.0, "BBB": float("nan"), "CCC": 2.0})
        assert select_top(scores, 3) == ["CCC", "AAA"]

    def test_ties_break_by_symbol_for_reproducibility(self):
        scores = pd.Series({"BBB": 1.0, "AAA": 1.0, "CCC": 2.0})
        assert select_top(scores, 2) == ["CCC", "AAA"]

    def test_fewer_valid_than_top_n_returns_what_exists(self):
        scores = pd.Series({"AAA": 1.0, "BBB": float("nan")})
        assert select_top(scores, 5) == ["AAA"]

    def test_all_nan_returns_empty(self):
        scores = pd.Series({"AAA": float("nan")})
        assert select_top(scores, 3) == []

    def test_empty_scores_returns_empty(self):
        assert select_top(pd.Series(dtype=float), 3) == []

    def test_non_positive_top_n_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            select_top(pd.Series({"AAA": 1.0}), 0)
```

`tests/test_allocation_weights.py`:

```python
from __future__ import annotations

import math

import pandas as pd
import pytest

from tradingbot.allocation.weights import (
    equal_weights,
    inverse_volatility_weights,
    realized_volatility,
    scale_weights,
)


class TestEqualWeights:
    def test_splits_evenly(self):
        assert equal_weights(["AAA", "BBB"]) == {"AAA": 0.5, "BBB": 0.5}

    def test_empty_is_empty(self):
        assert equal_weights([]) == {}


class TestRealizedVolatility:
    def test_constant_prices_have_zero_volatility(self):
        closes = pd.Series([100.0] * 30)
        assert realized_volatility(closes, 20) == pytest.approx(0.0)

    def test_wilder_swings_mean_higher_volatility(self):
        calm = pd.Series([100.0 + (i % 2) * 0.1 for i in range(30)])
        wild = pd.Series([100.0 + (i % 2) * 10.0 for i in range(30)])
        assert realized_volatility(wild, 20) > realized_volatility(calm, 20)

    def test_insufficient_history_is_nan(self):
        assert math.isnan(realized_volatility(pd.Series([100.0] * 5), 20))

    def test_invalid_days_rejected(self):
        with pytest.raises(ValueError):
            realized_volatility(pd.Series([100.0] * 30), 0)


class TestInverseVolatilityWeights:
    def test_lower_volatility_gets_more_weight(self):
        result = inverse_volatility_weights({"CALM": 0.01, "WILD": 0.04})
        assert result["CALM"] == pytest.approx(0.8)
        assert result["WILD"] == pytest.approx(0.2)

    def test_weights_sum_to_one(self):
        result = inverse_volatility_weights({"A": 0.02, "B": 0.03, "C": 0.05})
        assert sum(result.values()) == pytest.approx(1.0)

    def test_nan_volatility_symbol_is_excluded(self):
        result = inverse_volatility_weights({"A": 0.02, "B": float("nan")})
        assert set(result) == {"A"}
        assert result["A"] == pytest.approx(1.0)

    def test_zero_volatility_symbol_is_excluded_not_infinite(self):
        result = inverse_volatility_weights({"A": 0.02, "B": 0.0})
        assert set(result) == {"A"}

    def test_no_valid_volatility_falls_back_to_equal(self):
        # A brand-new theme member with short history must not sink the whole
        # rebalance — fall back to equal weight rather than empty.
        result = inverse_volatility_weights({"A": float("nan"), "B": float("nan")})
        assert result == {"A": 0.5, "B": 0.5}

    def test_empty_is_empty(self):
        assert inverse_volatility_weights({}) == {}


class TestScaleWeights:
    def test_scales_every_weight(self):
        assert scale_weights({"A": 0.6, "B": 0.4}, 0.5) == {"A": 0.3, "B": 0.2}

    def test_negative_factor_rejected(self):
        with pytest.raises(ValueError):
            scale_weights({"A": 1.0}, -0.1)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_ranking.py tests\test_allocation_weights.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.allocation'`

- [ ] **Step 3: 구현**

`src/tradingbot/allocation/__init__.py`: 빈 파일.

`src/tradingbot/allocation/ranking.py`:

```python
"""Pick the names a theme portfolio should hold.

Selection is deliberately dumb: the intelligence lives in the combined
factor score. NaN scores are unscoreable names, not zeros — they are
excluded rather than ranked last, and ties break by symbol so the same
inputs always produce the same portfolio.
"""

from __future__ import annotations

import pandas as pd


def select_top(scores: pd.Series, top_n: int) -> list[str]:
    """Symbols of the `top_n` highest scores, deterministic under ties."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    clean = scores.dropna()
    if clean.empty:
        return []
    frame = clean.rename("score").rename_axis("symbol").reset_index()
    frame = frame.sort_values(["score", "symbol"], ascending=[False, True])
    return frame["symbol"].head(top_n).tolist()
```

`src/tradingbot/allocation/weights.py`:

```python
"""Turn a selected list of names into portfolio weights.

Inverse-volatility weighting sizes positions so each contributes similar
risk — a theme's calmest name gets more capital than its wildest. A symbol
whose volatility cannot be measured is excluded rather than guessed; if no
symbol can be measured the whole basket falls back to equal weight, because
an empty rebalance is a worse failure than an unsophisticated one.
"""

from __future__ import annotations

import math
from typing import Sequence

import pandas as pd


def equal_weights(symbols: Sequence[str]) -> dict[str, float]:
    if not symbols:
        return {}
    share = 1.0 / len(symbols)
    return {str(symbol): share for symbol in symbols}


def realized_volatility(closes: pd.Series, days: int) -> float:
    """Standard deviation of daily returns over the trailing `days` returns."""
    if days <= 0:
        raise ValueError("days must be positive")
    returns = closes.dropna().pct_change().dropna().tail(days)
    if len(returns) < days:
        return float("nan")
    return float(returns.std(ddof=0))


def inverse_volatility_weights(volatilities: dict[str, float]) -> dict[str, float]:
    """1/sigma weights, normalized. Unmeasurable symbols are excluded."""
    if not volatilities:
        return {}
    inverses = {
        symbol: 1.0 / vol
        for symbol, vol in volatilities.items()
        if not math.isnan(vol) and vol > 0
    }
    if not inverses:
        return equal_weights(list(volatilities))
    total = sum(inverses.values())
    return {symbol: value / total for symbol, value in inverses.items()}


def scale_weights(weights: dict[str, float], factor: float) -> dict[str, float]:
    """Scale every weight by `factor` (e.g. regime-based exposure)."""
    if factor < 0:
        raise ValueError("factor must be non-negative")
    return {symbol: weight * factor for symbol, weight in weights.items()}
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_ranking.py tests\test_allocation_weights.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (19 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/allocation tests/test_allocation_ranking.py tests/test_allocation_weights.py
git commit -m "M12(part): Add ranking and weighting for theme portfolios"
```

---

### Task 2: 제약과 주문 계획 (`allocation/constraints.py`, `allocation/rebalance.py`)

**Files:**
- Create: `src/tradingbot/allocation/constraints.py`
- Create: `src/tradingbot/allocation/rebalance.py`
- Test: `tests/test_allocation_constraints.py`, `tests/test_allocation_rebalance.py`

**Interfaces:**
- Consumes: `get_calendar(market)` (기존, `.next_trading_day(date)->date`)
- Produces:
  - `constraints.apply_constraints(weights: dict[str, float], *, max_weight: float, cash_buffer: float) -> dict[str, float]` — 종목별 상한 캡(초과분은 현금으로, 재배분하지 않음), 총합이 `1 - cash_buffer` 초과 시 전체 비례 축소
  - `rebalance.TradeIntent` (frozen dataclass: `symbol: str`, `side: str` ("SELL"|"BUY"), `qty: int | None`, `weight: float | None`)
  - `rebalance.plan_rebalance(*, targets: dict[str, float], current_weights: dict[str, float], positions: dict[str, int], band: float = 0.005) -> list[TradeIntent]` — 매도 먼저, 심볼 오름차순, band 이내 차이는 무시
  - `rebalance.is_rebalance_date(dt: date, frequency: str, calendar) -> bool` — "daily"|"weekly"|"monthly", 기간의 마지막 거래일에 True

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_allocation_constraints.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.allocation.constraints import apply_constraints


class TestApplyConstraints:
    def test_caps_a_single_oversized_weight(self):
        result = apply_constraints({"A": 0.6, "B": 0.2}, max_weight=0.4, cash_buffer=0.0)
        assert result["A"] == pytest.approx(0.4)
        assert result["B"] == pytest.approx(0.2)

    def test_capped_excess_goes_to_cash_not_other_symbols(self):
        result = apply_constraints({"A": 0.8, "B": 0.2}, max_weight=0.4, cash_buffer=0.0)
        # B must not absorb A's excess — concentration limits exist to cap
        # risk, and redistribution would just move the concentration.
        assert result["B"] == pytest.approx(0.2)
        assert sum(result.values()) == pytest.approx(0.6)

    def test_total_scaled_down_to_respect_cash_buffer(self):
        result = apply_constraints({"A": 0.5, "B": 0.5}, max_weight=1.0, cash_buffer=0.1)
        assert sum(result.values()) == pytest.approx(0.9)
        assert result["A"] == pytest.approx(0.45)

    def test_within_limits_passes_through(self):
        original = {"A": 0.3, "B": 0.3}
        assert apply_constraints(original, max_weight=0.4, cash_buffer=0.1) == pytest.approx(original)

    def test_empty_weights(self):
        assert apply_constraints({}, max_weight=0.4, cash_buffer=0.1) == {}

    def test_invalid_limits_rejected(self):
        with pytest.raises(ValueError):
            apply_constraints({"A": 0.5}, max_weight=0.0, cash_buffer=0.1)
        with pytest.raises(ValueError):
            apply_constraints({"A": 0.5}, max_weight=0.4, cash_buffer=1.0)
```

`tests/test_allocation_rebalance.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from tradingbot.allocation.rebalance import TradeIntent, is_rebalance_date, plan_rebalance
from tradingbot.engine.calendar import get_calendar


class TestPlanRebalance:
    def test_new_position_is_a_buy_by_weight(self):
        plan = plan_rebalance(targets={"AAA": 0.3}, current_weights={}, positions={})
        assert plan == [TradeIntent(symbol="AAA", side="BUY", qty=None, weight=pytest.approx(0.3))]

    def test_dropped_position_is_a_full_sell(self):
        plan = plan_rebalance(
            targets={}, current_weights={"AAA": 0.3}, positions={"AAA": 10}
        )
        assert plan == [TradeIntent(symbol="AAA", side="SELL", qty=10, weight=None)]

    def test_trimming_sells_a_proportional_quantity(self):
        plan = plan_rebalance(
            targets={"AAA": 0.1}, current_weights={"AAA": 0.3}, positions={"AAA": 30}
        )
        # Shed 2/3 of a 30-share position.
        assert plan == [TradeIntent(symbol="AAA", side="SELL", qty=20, weight=None)]

    def test_topping_up_buys_the_weight_difference(self):
        plan = plan_rebalance(
            targets={"AAA": 0.3}, current_weights={"AAA": 0.1}, positions={"AAA": 10}
        )
        assert plan == [TradeIntent(symbol="AAA", side="BUY", qty=None, weight=pytest.approx(0.2))]

    def test_sells_come_before_buys(self):
        plan = plan_rebalance(
            targets={"BBB": 0.3},
            current_weights={"AAA": 0.3},
            positions={"AAA": 10},
        )
        # Sells free the cash the buys need at the same next-open fill.
        assert [intent.side for intent in plan] == ["SELL", "BUY"]

    def test_within_band_changes_are_ignored(self):
        plan = plan_rebalance(
            targets={"AAA": 0.301}, current_weights={"AAA": 0.300}, positions={"AAA": 10},
            band=0.005,
        )
        assert plan == []

    def test_deterministic_symbol_order(self):
        plan = plan_rebalance(
            targets={"CCC": 0.2, "AAA": 0.2},
            current_weights={},
            positions={},
        )
        assert [intent.symbol for intent in plan] == ["AAA", "CCC"]

    def test_zero_quantity_sell_is_dropped(self):
        # A tiny trim of a tiny position can round to zero shares — emitting
        # a zero-share order would just be rejected downstream.
        plan = plan_rebalance(
            targets={"AAA": 0.29}, current_weights={"AAA": 0.30}, positions={"AAA": 1},
            band=0.001,
        )
        assert plan == []


class TestIsRebalanceDate:
    def test_monthly_true_on_last_trading_day_of_month(self):
        calendar = get_calendar("KR")
        # 2024-01-31 is a Wednesday and the last KR trading day of January.
        assert is_rebalance_date(date(2024, 1, 31), "monthly", calendar) is True

    def test_monthly_false_mid_month(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 15), "monthly", calendar) is False

    def test_monthly_true_at_year_end_boundary(self):
        calendar = get_calendar("KR")
        # KRX closes Dec 31; the last 2024 trading day is Dec 30 (Mon).
        assert is_rebalance_date(date(2024, 12, 30), "monthly", calendar) is True

    def test_weekly_true_on_friday(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 19), "weekly", calendar) is True

    def test_weekly_false_on_tuesday(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 16), "weekly", calendar) is False

    def test_daily_always_true(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 16), "daily", calendar) is True

    def test_unknown_frequency_rejected(self):
        calendar = get_calendar("KR")
        with pytest.raises(ValueError, match="frequency"):
            is_rebalance_date(date(2024, 1, 16), "hourly", calendar)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_constraints.py tests\test_allocation_rebalance.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.allocation.constraints'`

- [ ] **Step 3: 구현**

`src/tradingbot/allocation/constraints.py`:

```python
"""Hard limits between a computed portfolio and the orders it becomes.

Two rules, applied in order:
1. No single name above `max_weight`. Excess goes to cash, never to the
   other names — concentration caps exist to limit risk, and redistributing
   the excess would just relocate the concentration.
2. Total equity exposure stays at or below `1 - cash_buffer`, scaling every
   weight down proportionally when it doesn't.
"""

from __future__ import annotations


def apply_constraints(
    weights: dict[str, float], *, max_weight: float, cash_buffer: float
) -> dict[str, float]:
    if max_weight <= 0:
        raise ValueError("max_weight must be positive")
    if not 0.0 <= cash_buffer < 1.0:
        raise ValueError("cash_buffer must be in [0, 1)")
    if not weights:
        return {}

    capped = {symbol: min(weight, max_weight) for symbol, weight in weights.items()}
    budget = 1.0 - cash_buffer
    total = sum(capped.values())
    if total <= budget:
        return capped
    scale = budget / total
    return {symbol: weight * scale for symbol, weight in capped.items()}
```

`src/tradingbot/allocation/rebalance.py`:

```python
"""Turn target weights into a concrete, ordered list of trades.

Sells are planned before buys because both fill at the same next session
open — the sells free the cash the buys spend. Differences inside `band`
are ignored: rebalancing a 0.1%p drift buys nothing but transaction costs.

Rebalance timing is judged from the exchange calendar alone (is this the
period's last trading day?). The calendar is static data, so this cannot
leak price information from the future.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tradingbot.engine.calendar import ExchangeCalendar

FREQUENCIES = ("daily", "weekly", "monthly")


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    side: str  # "SELL" | "BUY"
    qty: int | None  # sells: concrete share count
    weight: float | None  # buys: equity fraction to add


def plan_rebalance(
    *,
    targets: dict[str, float],
    current_weights: dict[str, float],
    positions: dict[str, int],
    band: float = 0.005,
) -> list[TradeIntent]:
    """Sells first (freeing cash), then buys, both in symbol order."""
    sells: list[TradeIntent] = []
    buys: list[TradeIntent] = []
    symbols = sorted(set(targets) | set(current_weights))

    for symbol in symbols:
        target = float(targets.get(symbol, 0.0))
        current = float(current_weights.get(symbol, 0.0))
        delta = target - current
        if abs(delta) <= band:
            continue
        if delta < 0:
            held = int(positions.get(symbol, 0))
            if held <= 0:
                continue
            if target <= 0:
                qty = held
            else:
                qty = round(held * (-delta) / current) if current > 0 else held
            if qty >= 1:
                sells.append(TradeIntent(symbol=symbol, side="SELL", qty=qty, weight=None))
        else:
            buys.append(TradeIntent(symbol=symbol, side="BUY", qty=None, weight=delta))
    return sells + buys


def is_rebalance_date(dt: date, frequency: str, calendar: ExchangeCalendar) -> bool:
    """True on the last trading day of the period (signal at that close)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency}. Available: {', '.join(FREQUENCIES)}")
    if frequency == "daily":
        return True
    next_day = calendar.next_trading_day(dt)
    if frequency == "monthly":
        return (next_day.year, next_day.month) != (dt.year, dt.month)
    this_week = dt.isocalendar()[:2]
    next_week = next_day.isocalendar()[:2]
    return this_week != next_week
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_constraints.py tests\test_allocation_rebalance.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (21 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/allocation/constraints.py src/tradingbot/allocation/rebalance.py tests/test_allocation_constraints.py tests/test_allocation_rebalance.py
git commit -m "M12(part): Add portfolio constraints and rebalance planning"
```

---

### Task 3: 목표 비중 산출 (`strategies/theme_multifactor.py`의 `generate_targets`)

**Files:**
- Create: `src/tradingbot/strategies/theme_multifactor.py` (이 태스크에서는 `generate_targets`와 헬퍼까지 — `on_bar` 어댑터는 Task 4)
- Test: `tests/test_theme_multifactor_targets.py`

**Interfaces:**
- Consumes: Task 1~2의 allocation 모듈, `get_factor`, `standardize`, `combine`, `market_regime`, `equity_exposure`, `load_research_config`, `get_theme`, `members`
- Produces:
  - `ThemeMultifactorStrategy(Strategy)` — `name = "theme_multifactor"`, default_params:
    ```python
    {
        "theme": "ai_semiconductor",
        "market": "KR",
        "rebalance": "monthly",
        "top_n": 3,
        "weighting": "inverse_volatility",  # 또는 "equal"
        "volatility_days": 60,
        "band": 0.005,
        "min_factors": 1,
        "bear_exposure": 0.5,
        "regime_series": "kospi",
        "regime_ma_days": 200,
        "data_root": "data/cache",
        "processed_root": "data/processed",
        "research_config": None,   # None이면 config/research.toml
        "themes_path": None,       # None이면 config/themes.toml
    }
    ```
  - `.generate_targets(dt: date, universe: Sequence[str], data_store) -> dict[str, float]` — 빈 dict는 "신선한 데이터 없음, 리밸런싱 스킵" 신호
  - `.factor_weights` (property) — research config `[factor_weights]`에서 로드·캐시. **각 키를 `get_factor`로 즉시 검증** (오타 → ValueError)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_theme_multifactor_targets.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

AS_OF = date(2024, 6, 28)
# momentum_3m needs 3*21+1 = 64 closes.
HISTORY_DAYS = 70

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""


@pytest.fixture
def research_config(tmp_path):
    path = tmp_path / "research.toml"
    path.write_text(RESEARCH_TOML, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_prices(store, symbol: str, start_price: float, end_price: float) -> None:
    closes = list(np.linspace(start_price, end_price, HISTORY_DAYS))
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0] * HISTORY_DAYS,
            },
            index=index,
        ),
    )


def write_macro(store, closes: list[float]) -> None:
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=len(closes))
    frame = pd.DataFrame({"date": index, "symbol": "kospi", "close": closes})
    PanelStore(store.processed_root, "macro", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


def make_strategy(research_config, **overrides) -> ThemeMultifactorStrategy:
    params = {"research_config": str(research_config), "top_n": 2, "weighting": "equal"}
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


class TestGenerateTargets:
    def test_picks_the_strongest_momentum_names(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)   # +100%
        write_prices(store, "WIN2", 100.0, 150.0)   # +50%
        write_prices(store, "LOSE", 100.0, 80.0)    # -20%
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2", "LOSE"], store
        )
        assert set(targets) == {"WIN1", "WIN2"}

    def test_equal_weighting_respects_cash_buffer(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # 2 names, equal, capped by max_weight 0.40 then total <= 0.98.
        assert targets["WIN1"] == pytest.approx(0.40)
        assert targets["WIN2"] == pytest.approx(0.40)

    def test_no_data_returns_empty_not_orders(self, store, research_config):
        # The freshness gate: nothing scoreable -> no rebalance at all.
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["GHOST"], store
        )
        assert targets == {}

    def test_bear_regime_halves_exposure(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        # Index well below its 200-day mean -> bear.
        write_macro(store, [100.0] * 200 + [50.0])
        targets = make_strategy(research_config, bear_exposure=0.5).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # equal 0.5 -> exposure x0.5 = 0.25 (the 0.40 cap then has nothing to cut).
        assert targets["WIN1"] == pytest.approx(0.25)

    def test_missing_macro_keeps_full_exposure(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # UNKNOWN regime must not silently de-risk.
        assert targets["WIN1"] == pytest.approx(0.40)

    def test_inverse_volatility_prefers_the_calm_name(self, store, research_config):
        write_prices(store, "CALM", 100.0, 140.0)
        # Same total return, wilder path: alternate +/- swings around the trend.
        closes = list(np.linspace(100.0, 140.0, HISTORY_DAYS))
        wild = [c * (1.05 if i % 2 else 0.95) for i, c in enumerate(closes)]
        index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
        store.cache.write(
            "KR",
            "WILD",
            pd.DataFrame(
                {"open": wild, "high": [c * 1.01 for c in wild],
                 "low": [c * 0.99 for c in wild], "close": wild,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )
        targets = make_strategy(
            research_config, weighting="inverse_volatility"
        ).generate_targets(AS_OF, ["CALM", "WILD"], store)
        assert targets["CALM"] > targets["WILD"]

    def test_typo_factor_name_raises_immediately(self, store, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text(
            "[factor_weights]\nmomentum_3m_typo = 1.0\n"
            "[risk_limits]\nmax_position_weight = 0.4\nmin_cash_weight = 0.02\n",
            encoding="utf-8",
        )
        strategy = ThemeMultifactorStrategy(research_config=str(bad))
        # A typo'd factor silently zero-weighted was the Phase 3 review's
        # deferred trap; here it must fail loudly instead.
        with pytest.raises(ValueError, match="momentum_3m_typo"):
            strategy.factor_weights

    def test_empty_universe_returns_empty(self, store, research_config):
        assert make_strategy(research_config).generate_targets(AS_OF, [], store) == {}
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.strategies.theme_multifactor'`

- [ ] **Step 3: 구현**

`src/tradingbot/strategies/theme_multifactor.py` (Task 4에서 `on_bar` 어댑터가 이 파일에 추가된다):

```python
"""Theme multifactor strategy: combined factor score -> target weights -> orders.

Decision flow (spec §9):
    theme members at dt -> factor scores (weights config drives WHICH factors)
    -> standardize -> combine -> top N -> equal or inverse-vol weights
    -> regime exposure scaling -> concentration/cash constraints -> targets

The factor-weights config is the single source of truth for which factors
run: every key is resolved through the registry up front, so a typo'd name
raises immediately instead of being silently zero-weighted.

An empty targets dict is a deliberate signal: nothing was scoreable at dt
(stale or missing data), and the caller must skip rebalancing rather than
trade on nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from tradingbot.allocation.constraints import apply_constraints
from tradingbot.allocation.ranking import select_top
from tradingbot.allocation.weights import (
    equal_weights,
    inverse_volatility_weights,
    realized_volatility,
    scale_weights,
)
from tradingbot.config import resolve_project_path
from tradingbot.factors.registry import get_factor
from tradingbot.factors.transform import combine, standardize
from tradingbot.research.gate import load_research_config
from tradingbot.research.regime import equity_exposure, market_regime
from tradingbot.strategies.base import Strategy
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

WEIGHTINGS = ("equal", "inverse_volatility")


class ThemeMultifactorStrategy(Strategy):
    name = "theme_multifactor"
    default_params = {
        "theme": "ai_semiconductor",
        "market": "KR",
        "rebalance": "monthly",
        "top_n": 3,
        "weighting": "inverse_volatility",
        "volatility_days": 60,
        "band": 0.005,
        "min_factors": 1,
        "bear_exposure": 0.5,
        "regime_series": "kospi",
        "regime_ma_days": 200,
        "data_root": "data/cache",
        "processed_root": "data/processed",
        "research_config": None,
        "themes_path": None,
    }

    def __init__(self, **params) -> None:
        super().__init__(**params)
        if self.params["weighting"] not in WEIGHTINGS:
            raise ValueError(
                f"Unknown weighting: {self.params['weighting']}. "
                f"Available: {', '.join(WEIGHTINGS)}"
            )
        self._research: dict | None = None
        self._factor_weights: dict[str, float] | None = None
        self._data_store = None

    @property
    def research(self) -> dict:
        if self._research is None:
            self._research = load_research_config(self.params["research_config"])
        return self._research

    @property
    def factor_weights(self) -> dict[str, float]:
        """[factor_weights] keys drive which factors run; typos fail loudly."""
        if self._factor_weights is None:
            raw = self.research.get("factor_weights", {})
            if not raw:
                raise ValueError("research config has no [factor_weights] section")
            for factor_name in raw:
                get_factor(factor_name)  # raises ValueError on unknown names
            self._factor_weights = {name: float(value) for name, value in raw.items()}
        return self._factor_weights

    def generate_targets(
        self, dt: date, universe: Sequence[str], data_store
    ) -> dict[str, float]:
        """Target equity weights as of dt's close. Empty dict = skip rebalance."""
        if not universe:
            return {}

        scores = {
            name: standardize(get_factor(name).compute(dt, universe, data_store))
            for name in self.factor_weights
        }
        combined = combine(
            scores, self.factor_weights, min_factors=int(self.params["min_factors"])
        )
        if combined.dropna().empty:
            LOGGER.warning(
                "theme_multifactor: no scoreable symbol at %s (stale or missing data); "
                "skipping rebalance",
                dt,
            )
            return {}

        selected = select_top(combined, int(self.params["top_n"]))
        if not selected:
            return {}

        if self.params["weighting"] == "equal":
            base = equal_weights(selected)
        else:
            vol_days = int(self.params["volatility_days"])
            volatilities = {}
            for symbol in selected:
                try:
                    history = data_store.price_history(symbol, dt, vol_days + 1)
                except (FileNotFoundError, KeyError):
                    volatilities[symbol] = float("nan")
                    continue
                volatilities[symbol] = realized_volatility(history["close"], vol_days)
            base = inverse_volatility_weights(volatilities)

        regime_state = market_regime(
            data_store,
            dt,
            series=str(self.params["regime_series"]),
            ma_days=int(self.params["regime_ma_days"]),
        )
        exposure = equity_exposure(regime_state, bear=float(self.params["bear_exposure"]))
        scaled = scale_weights(base, exposure)

        limits = self.research.get("risk_limits", {})
        return apply_constraints(
            scaled,
            max_weight=float(limits.get("max_position_weight", 0.40)),
            cash_buffer=float(limits.get("min_cash_weight", 0.02)),
        )

    def _store(self):
        """Lazily built local-only data store (prices + PIT panels)."""
        if self._data_store is None:
            from tradingbot.data.cache import ParquetCache
            from tradingbot.data.store import ParquetDataStore

            self._data_store = ParquetDataStore(
                ParquetCache(resolve_project_path(self.params["data_root"])),
                str(self.params["market"]),
                processed_root=resolve_project_path(self.params["processed_root"]),
            )
        return self._data_store

    def on_bar(self, ctx, bar) -> None:  # pragma: no cover - completed in Task 4
        raise NotImplementedError("on_bar adapter lands in the next task")
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (8 tests)

- [ ] **Step 5: 전체 회귀 + 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"` → 전체 PASS

```powershell
git add src/tradingbot/strategies/theme_multifactor.py tests/test_theme_multifactor_targets.py
git commit -m "M12(part): Add theme multifactor target generation"
```

---

### Task 4: 엔진 어댑터 — `on_bar`, 멱등성, 상태, 레지스트리, 설정

**Files:**
- Modify: `src/tradingbot/strategies/theme_multifactor.py` (`on_bar` 완성 + 상태)
- Modify: `src/tradingbot/strategies/registry.py` (등록)
- Modify: `config/default.toml` (`[strategies.theme_multifactor]`)
- Test: `tests/test_theme_multifactor_adapter.py`

**Interfaces:**
- Consumes: Task 3의 `generate_targets`, `SignalLedger`/`make_signal_id` (기존), `is_rebalance_date`·`plan_rebalance` (Task 2), `get_theme`/`members` (기존), `get_calendar` (기존)
- Produces:
  - `ThemeMultifactorStrategy.on_bar(ctx, bar)` — 새 날짜의 첫 호출에서만 동작; 리밸런싱일이 아니면 no-op; 주문 전 `SignalLedger.claim` 필수
  - `snapshot_state()/restore_state()` — `last_seen_date`, `last_rebalance_date`, `last_targets` 영속화
  - 레지스트리에 `theme_multifactor` 등록; `config/default.toml`에 기본 파라미터

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_theme_multifactor_adapter.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.models import Bar, Position
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

# 2024-06-28 is the last KR trading day of June (Friday) -> monthly rebalance day.
REBALANCE_DAY = date(2024, 6, 28)
MID_MONTH_DAY = date(2024, 6, 14)
HISTORY_DAYS = 70

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

THEMES_TOML = """
[themes.test_theme]
name = "테스트"
market = "KR"
members = [
    { symbol = "WIN1", from = "2023-01-01" },
    { symbol = "WIN2", from = "2023-01-01" },
    { symbol = "LOSE", from = "2023-01-01" },
]
"""


class FakeContext:
    """Records orders; enough surface for the adapter, no engine needed."""

    def __init__(self, equity: float = 1_000_000.0):
        self._equity = equity
        self.positions: dict[str, Position] = {}
        self.orders: list[tuple] = []

    def history(self, symbol, n):
        raise AssertionError("adapter must use its own data store, not ctx.history")

    def position(self, symbol):
        return self.positions.get(symbol, Position(symbol=symbol))

    def cash(self):
        return self._equity

    def equity(self):
        return self._equity

    def has_open_order(self, symbol, side=None):
        return False

    def buy(self, symbol, qty=None, weight=None, **kwargs):
        self.orders.append(("BUY", symbol, qty, weight))

    def sell(self, symbol, qty, **kwargs):
        self.orders.append(("SELL", symbol, qty, None))


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")
    themes = tmp_path / "themes.toml"
    themes.write_text(THEMES_TOML, encoding="utf-8")

    cache = ParquetCache(tmp_path / "cache")
    for symbol, end_price in [("WIN1", 200.0), ("WIN2", 150.0), ("LOSE", 80.0)]:
        closes = list(np.linspace(100.0, end_price, HISTORY_DAYS))
        index = pd.bdate_range(end=pd.Timestamp(REBALANCE_DAY), periods=HISTORY_DAYS)
        cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )
    return {"research": research, "themes": themes, "root": tmp_path}


def make_strategy(env, **overrides) -> ThemeMultifactorStrategy:
    params = {
        "theme": "test_theme",
        "research_config": str(env["research"]),
        "themes_path": str(env["themes"]),
        "data_root": str(env["root"] / "cache"),
        "processed_root": str(env["root"] / "processed"),
        "top_n": 2,
        "weighting": "equal",
    }
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


def bar(symbol: str, dt: date) -> Bar:
    return Bar(symbol=symbol, dt=dt, open=100.0, high=101.0, low=99.0, close=100.0)


class TestOnBarAdapter:
    def test_rebalance_day_places_buy_orders(self, env):
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        sides_symbols = {(side, symbol) for side, symbol, *_ in ctx.orders}
        assert ("BUY", "WIN1") in sides_symbols
        assert ("BUY", "WIN2") in sides_symbols
        assert all(symbol != "LOSE" for _, symbol, *_ in ctx.orders)

    def test_runs_once_per_day_despite_per_symbol_calls(self, env):
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        first_count = len(ctx.orders)
        strategy.on_bar(ctx, bar("WIN2", REBALANCE_DAY))
        strategy.on_bar(ctx, bar("LOSE", REBALANCE_DAY))
        assert len(ctx.orders) == first_count

    def test_mid_month_day_is_a_no_op(self, env):
        ctx = FakeContext()
        make_strategy(env).on_bar(ctx, bar("WIN1", MID_MONTH_DAY))
        assert ctx.orders == []

    def test_rerun_of_the_same_decision_is_idempotent(self, env):
        # A paper-trading restart replays the same day; the ledger must
        # swallow the duplicate orders even when the day gate reopens.
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        first = list(ctx.orders)

        strategy._last_seen_date = None  # force the day gate open again
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ctx.orders == first  # ledger rejected every duplicate claim

    def test_trims_and_exits_existing_positions(self, env):
        ctx = FakeContext()
        # Holding LOSE (not selected) -> full exit sell before buys.
        ctx.positions["LOSE"] = Position(symbol="LOSE", qty=50, avg_price=100.0, last_price=100.0)
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ("SELL", "LOSE", 50, None) in ctx.orders
        sell_index = ctx.orders.index(("SELL", "LOSE", 50, None))
        buy_indices = [i for i, order in enumerate(ctx.orders) if order[0] == "BUY"]
        assert all(sell_index < i for i in buy_indices)

    def test_no_scoreable_data_skips_without_orders(self, env, tmp_path):
        ctx = FakeContext()
        strategy = make_strategy(env, data_root=str(tmp_path / "empty_cache"))
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ctx.orders == []

    def test_state_round_trip(self, env):
        strategy = make_strategy(env)
        ctx = FakeContext()
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        state = strategy.snapshot_state()
        assert state["last_rebalance_date"] == REBALANCE_DAY.isoformat()

        restored = make_strategy(env)
        restored.restore_state(state)
        assert restored._last_rebalance_date == REBALANCE_DAY
        assert restored._last_targets


class TestRegistration:
    def test_registered_in_strategy_registry(self):
        from tradingbot.strategies.registry import get_strategy, list_strategies

        assert "theme_multifactor" in list_strategies()
        assert get_strategy("theme_multifactor") is ThemeMultifactorStrategy

    def test_default_config_section_exists(self):
        from tradingbot.config import load_config

        config = load_config()
        assert "theme_multifactor" in config.get("strategies", {})
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_adapter.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `NotImplementedError` (on_bar) 및 registry 미등록

- [ ] **Step 3: 구현 — `on_bar` 어댑터**

`src/tradingbot/strategies/theme_multifactor.py`에서 임포트에 추가:

```python
from tradingbot.allocation.rebalance import is_rebalance_date, plan_rebalance
from tradingbot.data.universe import get_theme, members as theme_members
from tradingbot.engine.calendar import get_calendar
from tradingbot.strategies.signals import SignalLedger, make_signal_id
```

`__init__` 끝에 추가:

```python
        self._last_seen_date: date | None = None
        self._last_rebalance_date: date | None = None
        self._last_targets: dict[str, float] = {}
        self._ledger: SignalLedger | None = None
```

`on_bar`의 `raise NotImplementedError`를 다음으로 교체하고, 상태 메서드를 추가:

```python
    def on_bar(self, ctx, bar) -> None:
        """Once-per-day driver: the engine calls this per symbol, so the
        first call of a new date does the day's work and the rest no-op.

        Orders are plain MARKET at the CLOSE phase — the engine fills them
        at the next session open, which is the established no-lookahead flow.
        """
        dt = bar.dt
        if dt == self._last_seen_date:
            return
        self._last_seen_date = dt

        calendar = get_calendar(str(self.params["market"]))
        if not is_rebalance_date(dt, str(self.params["rebalance"]), calendar):
            return

        theme = get_theme(str(self.params["theme"]), self.params["themes_path"])
        universe = theme_members(theme, dt)
        targets = self.generate_targets(dt, universe, self._store())
        if not targets:
            self.persist_state()
            return

        equity = ctx.equity()
        candidates = sorted(set(targets) | set(self._last_targets))
        current_weights: dict[str, float] = {}
        positions: dict[str, int] = {}
        for symbol in candidates:
            position = ctx.position(symbol)
            positions[symbol] = position.qty
            current_weights[symbol] = (
                position.market_value / equity if equity > 0 and position.qty > 0 else 0.0
            )

        plan = plan_rebalance(
            targets=targets,
            current_weights=current_weights,
            positions=positions,
            band=float(self.params["band"]),
        )
        ledger = self._signal_ledger()
        for intent in plan:
            target_weight = targets.get(intent.symbol, 0.0)
            signal_id = make_signal_id(
                self.name, dt, intent.symbol, intent.side, target_weight
            )
            if not ledger.claim(signal_id):
                continue
            if intent.side == "SELL":
                ctx.sell(intent.symbol, qty=intent.qty)
            else:
                ctx.buy(intent.symbol, weight=intent.weight)

        self._last_rebalance_date = dt
        self._last_targets = dict(targets)
        self.persist_state()

    def _signal_ledger(self) -> SignalLedger:
        if self._ledger is None:
            self._ledger = SignalLedger(self.name, self._state_store)
        return self._ledger

    def snapshot_state(self) -> dict:
        return {
            "last_seen_date": self._last_seen_date.isoformat() if self._last_seen_date else None,
            "last_rebalance_date": (
                self._last_rebalance_date.isoformat() if self._last_rebalance_date else None
            ),
            "last_targets": dict(self._last_targets),
        }

    def restore_state(self, state: dict) -> None:
        seen = state.get("last_seen_date")
        rebalanced = state.get("last_rebalance_date")
        self._last_seen_date = date.fromisoformat(seen) if seen else None
        self._last_rebalance_date = date.fromisoformat(rebalanced) if rebalanced else None
        self._last_targets = {
            str(symbol): float(weight)
            for symbol, weight in (state.get("last_targets") or {}).items()
        }
```

- [ ] **Step 4: 레지스트리와 설정**

`src/tradingbot/strategies/registry.py` — 임포트 블록에 추가하고 dict에 등록:

```python
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy
```

```python
_STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageCrossStrategy.name: MovingAverageCrossStrategy,
    RsiReversionStrategy.name: RsiReversionStrategy,
    VolatilityBreakoutStrategy.name: VolatilityBreakoutStrategy,
    ThemeMultifactorStrategy.name: ThemeMultifactorStrategy,
}
```

`config/default.toml`의 `[strategies.rsi_reversion]` 섹션 뒤에 추가:

```toml
[strategies.theme_multifactor]
theme = "ai_semiconductor"
market = "KR"
rebalance = "monthly"
top_n = 3
weighting = "inverse_volatility"
volatility_days = 60
bear_exposure = 0.5
```

- [ ] **Step 5: 통과 확인 + 전체 회귀**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_adapter.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (9 tests)

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS (기존 전략·CLI 테스트 포함)

- [ ] **Step 6: 커밋**

```powershell
git add src/tradingbot/strategies/theme_multifactor.py src/tradingbot/strategies/registry.py config/default.toml tests/test_theme_multifactor_adapter.py
git commit -m "M12(part): Wire theme multifactor strategy into the engine"
```

---

### Task 5: 통합 검증 — E2E 백테스트, 데이터 복구, 실데이터 실행, 문서

**Files:**
- Test: `tests/test_theme_multifactor_backtest.py` (E2E fixture 백테스트)
- Modify: `README.md`, `docs/architecture.md`

**Interfaces:**
- Consumes: 전부 (Task 1~4 + 기존 `services.run_backtest`)

- [ ] **Step 1: E2E 백테스트 테스트 작성**

`tests/test_theme_multifactor_backtest.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.services import run_backtest

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.60
min_cash_weight = 0.02
"""

THEMES_TOML = """
[themes.e2e]
name = "E2E"
market = "KR"
members = [
    { symbol = "WIN1", from = "2023-01-01" },
    { symbol = "LOSE", from = "2023-01-01" },
]
"""

# Prices start 2023-12-01 so momentum_3m (64 rows) is fully warmed up by the
# first monthly signal (2024-03-29 close); the backtest itself starts in March.
DATA_START = date(2023, 12, 1)
START = date(2024, 3, 4)
DAYS = 150  # through May: warmup + one full monthly rebalance cycle


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")
    themes = tmp_path / "themes.toml"
    themes.write_text(THEMES_TOML, encoding="utf-8")

    cache_root = tmp_path / "cache"
    cache = ParquetCache(cache_root)
    index = pd.bdate_range(start=pd.Timestamp(DATA_START), periods=DAYS)
    for symbol, end_price in [("WIN1", 200.0), ("LOSE", 80.0)]:
        closes = list(np.linspace(100.0, end_price, DAYS))
        cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [10000.0] * DAYS},
                index=index,
            ),
        )

    config = {
        "backtest": {"initial_cash_kr": 10_000_000},
        "data": {"cache_dir": str(cache_root)},
        "fees": {"KR": {"commission_rate": 0.00015}},
        "strategies": {
            "theme_multifactor": {
                "theme": "e2e",
                "research_config": str(research),
                "themes_path": str(themes),
                "data_root": str(cache_root),
                "processed_root": str(tmp_path / "processed"),
                "top_n": 1,
                "weighting": "equal",
            }
        },
    }
    return config


class TestEndToEndBacktest:
    def test_backtest_runs_and_buys_the_winner(self, env):
        result = run_backtest(
            env,
            market="KR",
            symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor",
            start=START.isoformat(),
            end=None,
        )
        symbols_bought = {fill.symbol for fill in result.fills if fill.side.value == "BUY"}
        assert symbols_bought == {"WIN1"}
        assert result.final_equity > 0

    def test_close_signal_fills_at_next_session_open(self, env):
        result = run_backtest(
            env,
            market="KR",
            symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor",
            start=START.isoformat(),
            end=None,
        )
        first_fill = min(result.fills, key=lambda fill: fill.dt)
        # The first monthly signal fires at March's last trading day close
        # (2024-03-29); the fill must land on the NEXT trading day.
        assert first_fill.dt == date(2024, 4, 1)

    def test_deterministic_across_runs(self, env):
        first = run_backtest(
            env, market="KR", symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor", start=START.isoformat(), end=None,
        )
        second = run_backtest(
            env, market="KR", symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor", start=START.isoformat(), end=None,
        )
        assert first.final_equity == second.final_equity
        assert len(first.fills) == len(second.fills)
```

주의: `run_backtest`의 실제 시그니처·`BacktestResult` 필드(`fills`, `final_equity`)를 `src/tradingbot/services.py`·`engine/engine.py`에서 확인하고, 다르면 **테스트를 실제 API에 맞춰 조정**한다 (구현을 테스트에 맞추지 말 것). `fill.side.value` 접근도 `OrderSide` enum 정의에 맞춘다.

- [ ] **Step 2: 실패 확인 → 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_backtest.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: 처음엔 fixture/필드명 조정이 필요할 수 있다. 조정 후 PASS (3 tests). **여기서 실패가 나면 그것은 어댑터의 실제 버그일 가능성이 높다 — 테스트를 약화시키지 말고 원인을 고친다.**

- [ ] **Step 3: 손상된 가격 캐시 복구 (실데이터)**

Phase 3에서 발견된 기존 데이터 손상(005930 6행, 035420 33행 ohlc_logic FAIL)을 재수집으로 복구한다:

```powershell
Remove-Item data\cache\KR\005930.parquet -ErrorAction SilentlyContinue
Remove-Item data\cache\KR\035420.parquet -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 035420 --start 2015-01-01
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR --symbols 005930 035420
```

Expected: `prices` 소스에 더 이상 `quality=fail: ... ohlc_logic`이 나오지 않는다. 여전히 나오면 **원천 데이터 자체의 문제**이므로 몇 행이 어느 날짜인지 보고에 기록하고 진행한다 (수정하려고 데이터를 손으로 편집하지 않는다).

- [ ] **Step 4: 실데이터 백테스트 (완료 기준 확인)**

테마 종목 시세를 준비하고 실제 백테스트를 실행한다 (네트워크는 `data update`만 사용):

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 000660 042700 058470 240810 --start 2022-01-01
.\.venv\Scripts\python.exe -m tradingbot backtest --strategy theme_multifactor --market KR --symbols 005930 000660 042700 058470 240810 --start 2023-01-01
```

Expected: 백테스트가 완주하고 HTML 리포트가 생성된다. 월별 리밸런싱 체결이 trades.csv에 나타난다. **수익률 값 자체는 검증하지 않는다** — 데이터가 말하는 대로 기록한다.

**보조 벤치마크 비교(스펙 §8)**: 같은 명령을 `--strategy` 파라미터만 바꿔 실행할 수는 없으므로, config 오버라이드로 동일비중 근사 벤치마크를 돌린다: `config/default.toml`을 복사한 임시 TOML에서 `[strategies.theme_multifactor]`를 `top_n = 5, weighting = "equal", bear_exposure = 1.0`으로 바꾸고 `--config`로 실행. 두 결과의 최종 자산·MDD를 보고에 나란히 기록한다.

- [ ] **Step 5: 문서 갱신**

`README.md` 확장 목록에 추가:

```markdown
- 테마 멀티팩터 전략(`strategies/theme_multifactor.py`, `allocation/`):
  종합점수 상위 N종목 선정 → 동일/변동성 역가중 → 국면 노출 조절 →
  제약 적용 → 월간/주간 리밸런싱. 종가 신호는 다음 거래일 시가에 체결되고,
  `signal_id` 원장이 재실행 중복 주문을 차단 — M11/M12
```

`README.md` 백테스트 섹션에 실행 예시 한 줄 추가:

```markdown
테마 전략 백테스트는 테마 종목을 `--symbols`로 함께 넘깁니다 (팩터 유니버스는
`config/themes.toml`이, 체결 대상은 `--symbols`가 결정합니다):

```powershell
.\.venv\Scripts\python.exe -m tradingbot backtest --strategy theme_multifactor --market KR --symbols 005930 000660 042700 058470 240810 --start 2023-01-01
```
```

`docs/architecture.md` §7 표에 행 추가:

```markdown
| 선정·비중·제약·리밸런싱 계획 | `src/tradingbot/allocation/` |
| 테마 멀티팩터 전략 | `src/tradingbot/strategies/theme_multifactor.py` |
```

- [ ] **Step 6: 전체 회귀 + 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"` → 전체 PASS

```powershell
git add tests/test_theme_multifactor_backtest.py README.md docs/architecture.md
git commit -m "M12: Add end-to-end theme strategy backtest and docs"
```

---

## 완료 기준 (스펙 §3 Phase 4)

- [ ] `backtest --strategy theme_multifactor`가 실데이터로 완주하고, 월별 리밸런싱 체결이 리포트에 나타난다.
- [ ] 종가 신호가 다음 거래일 시가에 체결된다 (E2E 테스트로 고정).
- [ ] 같은 결정의 재실행이 중복 주문을 내지 않는다 (ledger 테스트로 고정).
- [ ] 데이터가 전혀 없으면 주문 없이 경고와 함께 스킵한다.
- [ ] 오타 팩터명은 조용히 0-가중되지 않고 즉시 오류가 난다 (Phase 3 이월 해소).
- [ ] 전체 테스트 통과, 기존 회귀 없음.

## 알려진 한계 (의도된 범위 제외)

- **모의투자 장기 운영 검증(M15)과 KIS 실전 연동(M17~M18)은 이 계획 밖이다.** 전략이 백테스트 승격 기준을 통과한 뒤의 다음 단계다.
- 회전율 제한·거래대금 대비 주문 한도(M12 제약 목록의 일부)는 1차에서 제외 — 월간 리밸런싱 + band로 회전율이 구조적으로 낮다. 필요해지면 `constraints.py`에 추가한다.
- 벤치마크 비교는 리포트 자동화가 아니라 수동 비교 실행(Task 5 Step 4)이다. 자동 벤치마크 리포트는 연구 프레임워크 확장으로 미룬다.
