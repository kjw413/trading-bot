# Phase 1: 연구·검증 프레임워크 (M10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 팩터가 실제로 유효한지 숫자로 검증하는 프레임워크(forward return 라벨, Spearman IC, 분위수 분석, Walk-forward, 실험 기록, 팩터 채택 게이트)를 만들고, 기존 모멘텀 팩터에 대해 `tradingbot research report` CLI로 리포트를 생성한다.

**Architecture:** `src/tradingbot/research/` 신규 패키지에 순수 계산 모듈(labels, ic, quantiles, walk_forward)을 쌓고, 그 위에 gate(채택 기준)와 report(조립)를 올린다. 데이터 접근은 기존 `ParquetDataStore`에 라벨 전용 `close_series` 메서드를 추가해 해결한다 — 팩터는 계속 Point-in-Time `price_history`만 사용한다.

**Tech Stack:** Python 3.13, pandas, pyarrow(기존 Parquet 캐시), pytest. 신규 외부 의존성 없음.

**스펙:** `docs/superpowers/specs/2026-07-19-kr-theme-multifactor-design.md` §5 (Phase 1)

## Global Constraints

- 신규 의존성 추가 금지. Spearman 상관은 scipy가 아니라 `pandas.Series.corr(method="spearman")`을 사용한다.
- 팩터 계산은 `PriceDataStore.price_history`(as-of cutoff 강제)만 사용한다. `close_series`(전체 이력, 미래 포함)는 **라벨 계산 전용**이며 팩터 코드에서 호출 금지.
- research 코드 경로에서 네트워크 접근 금지 — 로컬 Parquet 캐시만 읽는다.
- 리포트 기본 평가 구간은 `in_sample_start`~`validation_end` (`config/research.toml [periods]`). Out-of-sample(2022-01-01~)은 기본값에서 제외 — 파라미터 조정에 사용 금지.
- 기존 테스트 삭제·수정 금지 (`tests/test_factors.py`의 자체 `write_prices` 헬퍼도 그대로 둔다).
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 메시지 접두사: 중간 작업 `M10(part):`, 마지막 작업 `M10:`.
- 테스트 실행 명령 (PowerShell, 저장소 루트에서): `.\.venv\Scripts\python.exe -m pytest <경로> -v`

---

### Task 1: 라벨 데이터 접근 + forward return 라벨 + 평가일 그리드

**Files:**
- Modify: `src/tradingbot/data/store.py` (ResearchDataStore 프로토콜 + ParquetDataStore.close_series)
- Create: `src/tradingbot/research/__init__.py` (빈 파일)
- Create: `src/tradingbot/research/labels.py`
- Create: `src/tradingbot/research/dates.py`
- Create: `tests/conftest.py` (공용 fixture — 기존 테스트에 영향 없음, 신규 research 테스트만 사용)
- Test: `tests/test_research_labels.py`, `tests/test_research_dates.py`

**Interfaces:**
- Consumes: `ParquetCache.read(market, symbol) -> pd.DataFrame` (기존), `get_calendar(market).trading_days(start, end) -> list[date]` (기존 `engine/calendar.py`)
- Produces (이후 태스크가 사용):
  - `ResearchDataStore` 프로토콜: `price_history(symbol, end, lookback)` + `close_series(symbol) -> pd.Series`
  - `labels.forward_return(closes: pd.Series, dt: date, horizon_days: int) -> float`
  - `labels.forward_returns(store, universe, dt, horizon_days) -> pd.Series` (인덱스=대문자 심볼, name=`fwd_{h}d`)
  - `labels.excess_forward_returns(store, universe, dt, horizon_days, benchmark: str) -> pd.Series`
  - `dates.month_end_trading_days(market: str, start: date, end: date) -> list[date]`
  - pytest fixture: `us_store` (임시 US ParquetDataStore), `write_prices` (합성 시세 기록 함수)

- [ ] **Step 1: 공용 conftest 작성**

`tests/conftest.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore


@pytest.fixture
def us_store(tmp_path):
    return ParquetDataStore(ParquetCache(tmp_path), "US")


@pytest.fixture
def write_prices():
    """Write a synthetic business-day OHLCV series to a cache."""

    def _write(
        cache: ParquetCache,
        market: str,
        symbol: str,
        closes: list[float],
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> None:
        if (start is None) == (end is None):
            raise ValueError("pass exactly one of start/end")
        if start is not None:
            index = pd.bdate_range(start=pd.Timestamp(start), periods=len(closes))
        else:
            index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
        frame = pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0] * len(closes),
            },
            index=index,
        )
        cache.write(market, symbol, frame)

    return _write
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_research_labels.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.research.labels import excess_forward_returns, forward_return, forward_returns

INDEX = pd.bdate_range(start="2020-01-01", periods=30)
CLOSES = pd.Series([float(100 + i) for i in range(30)], index=INDEX)


class TestForwardReturn:
    def test_five_day_horizon(self):
        dt = INDEX[5].date()  # close 105; 5 rows later -> 110
        assert forward_return(CLOSES, dt, 5) == pytest.approx(110.0 / 105.0 - 1.0)

    def test_base_is_last_close_at_or_before_dt(self):
        saturday = date(2020, 1, 11)  # last close = Fri 2020-01-10 (107); +5 rows -> 112
        assert forward_return(CLOSES, saturday, 5) == pytest.approx(112.0 / 107.0 - 1.0)

    def test_runs_off_series_end_is_nan(self):
        assert np.isnan(forward_return(CLOSES, INDEX[-3].date(), 5))

    def test_before_series_start_is_nan(self):
        assert np.isnan(forward_return(CLOSES, date(2019, 12, 31), 5))

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError):
            forward_return(CLOSES, INDEX[0].date(), 0)


class TestForwardReturns:
    def test_missing_symbol_is_nan_not_error(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [float(100 + i) for i in range(30)], start=date(2020, 1, 1))
        result = forward_returns(us_store, ["aaa", "MISSING"], INDEX[5].date(), 5)
        assert result.name == "fwd_5d"
        assert result.loc["AAA"] == pytest.approx(110.0 / 105.0 - 1.0)
        assert np.isnan(result.loc["MISSING"])

    def test_close_series_returns_full_history(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [100.0, 101.0, 102.0], start=date(2020, 1, 1))
        assert list(us_store.close_series("AAA")) == [100.0, 101.0, 102.0]


class TestExcessForwardReturns:
    def test_subtracts_benchmark(self, us_store, write_prices):
        write_prices(us_store.cache, "US", "AAA", [float(100 + i) for i in range(30)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BENCH", [100.0] * 30, start=date(2020, 1, 1))
        result = excess_forward_returns(us_store, ["AAA"], INDEX[5].date(), 5, benchmark="BENCH")
        assert result.name == "excess_5d"
        assert result.loc["AAA"] == pytest.approx(110.0 / 105.0 - 1.0)

    def test_missing_benchmark_raises(self, us_store):
        with pytest.raises((FileNotFoundError, KeyError)):
            excess_forward_returns(us_store, ["AAA"], INDEX[5].date(), 5, benchmark="NOPE")
```

`tests/test_research_dates.py`:

```python
from __future__ import annotations

from datetime import date

from tradingbot.research.dates import month_end_trading_days


def test_month_end_trading_days_us_q1_2020():
    days = month_end_trading_days("US", date(2020, 1, 1), date(2020, 3, 31))
    assert days == [date(2020, 1, 31), date(2020, 2, 28), date(2020, 3, 31)]


def test_range_end_mid_month_uses_last_available_day():
    days = month_end_trading_days("US", date(2020, 1, 1), date(2020, 2, 14))
    assert days == [date(2020, 1, 31), date(2020, 2, 14)]


def test_empty_range():
    assert month_end_trading_days("US", date(2020, 3, 31), date(2020, 1, 1)) == []
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_labels.py tests\test_research_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research'`

- [ ] **Step 4: 구현**

`src/tradingbot/data/store.py`에 추가 (기존 코드 유지, `ParquetDataStore` 클래스에 메서드 추가 + 파일 끝에 프로토콜 추가):

```python
class ResearchDataStore(PriceDataStore, Protocol):
    """PriceDataStore + full-history close access for research labels.

    close_series intentionally sees past any as-of date — labels are
    evaluation targets, never factor inputs. Factor code must keep using
    price_history, which enforces the point-in-time cutoff.
    """

    def close_series(self, symbol: str) -> pd.Series:
        ...
```

`ParquetDataStore`에 메서드 추가:

```python
    def close_series(self, symbol: str) -> pd.Series:
        """Full close history for research labels (look-ahead by design)."""
        return self.cache.read(self.market, symbol)["close"].dropna()
```

`src/tradingbot/research/__init__.py`: 빈 파일 생성.

`src/tradingbot/research/labels.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore


def forward_return(closes: pd.Series, dt: date, horizon_days: int) -> float:
    """Return from the last close at/before `dt` to the close `horizon_days`
    trading rows later. NaN when the base or target close is unavailable."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    clean = closes.dropna()
    if clean.empty:
        return float("nan")
    base_count = int((clean.index <= pd.Timestamp(dt)).sum())
    if base_count == 0:
        return float("nan")
    base_idx = base_count - 1
    target_idx = base_idx + horizon_days
    if target_idx >= len(clean):
        return float("nan")
    base = float(clean.iloc[base_idx])
    if base <= 0:
        return float("nan")
    return float(clean.iloc[target_idx]) / base - 1.0


def forward_returns(
    store: ResearchDataStore, universe: Sequence[str], dt: date, horizon_days: int
) -> pd.Series:
    """Forward returns indexed by upper-cased symbol; missing symbols get NaN."""
    values = pd.Series(
        [float("nan")] * len(universe),
        index=[symbol.upper() for symbol in universe],
        name=f"fwd_{horizon_days}d",
        dtype=float,
    )
    for symbol in values.index:
        try:
            closes = store.close_series(symbol)
        except (FileNotFoundError, KeyError):
            continue
        values.loc[symbol] = forward_return(closes, dt, horizon_days)
    return values


def excess_forward_returns(
    store: ResearchDataStore,
    universe: Sequence[str],
    dt: date,
    horizon_days: int,
    benchmark: str,
) -> pd.Series:
    """Forward returns minus the benchmark's forward return.

    A missing benchmark series is a configuration error and raises."""
    benchmark_return = forward_return(store.close_series(benchmark), dt, horizon_days)
    values = forward_returns(store, universe, dt, horizon_days) - benchmark_return
    values.name = f"excess_{horizon_days}d"
    return values
```

`src/tradingbot/research/dates.py`:

```python
from __future__ import annotations

from datetime import date

from tradingbot.engine.calendar import get_calendar


def month_end_trading_days(market: str, start: date, end: date) -> list[date]:
    """Last trading day of each month between start and end (inclusive)."""
    last_by_month: dict[tuple[int, int], date] = {}
    for day in get_calendar(market).trading_days(start, end):
        last_by_month[(day.year, day.month)] = day
    return sorted(last_by_month.values())
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_labels.py tests\test_research_dates.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: 전체 회귀 확인 후 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q` → 전체 PASS 확인.

```powershell
git add src/tradingbot/data/store.py src/tradingbot/research tests/conftest.py tests/test_research_labels.py tests/test_research_dates.py
git commit -m "M10(part): Add forward-return labels and research date grid"
```

---

### Task 2: Spearman IC 분석

**Files:**
- Create: `src/tradingbot/research/ic.py`
- Test: `tests/test_research_ic.py`

**Interfaces:**
- Consumes: `labels.forward_returns` (Task 1), `Factor.compute(dt, universe, data_store) -> pd.Series` (기존 `factors/base.py`), `ResearchDataStore` (Task 1)
- Produces:
  - `ic.spearman_ic(factor_values: pd.Series, forward: pd.Series) -> float`
  - `ic.ic_series(factor: Factor, store, universe, dates: Sequence[date], horizon_days: int) -> pd.Series` (인덱스=Timestamp)
  - `ic.ICSummary` (frozen dataclass: `mean, std, ir, positive_share: float; n_periods: int`)
  - `ic.summarize_ic(ics: pd.Series) -> ICSummary`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_ic.py`:

```python
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.factors.base import Factor
from tradingbot.research.ic import ic_series, spearman_ic, summarize_ic


class FixedFactor(Factor):
    """Deterministic factor for tests: fixed score per symbol."""

    def __init__(self, scores: dict[str, float], name: str = "fixed") -> None:
        self.scores = scores
        self.name = name

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol in values.index:
            if symbol in self.scores:
                values.loc[symbol] = self.scores[symbol]
        return values


class TestSpearmanIC:
    def test_perfect_positive(self):
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert spearman_ic(scores, forward) == pytest.approx(1.0)

    def test_perfect_negative(self):
        scores = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert spearman_ic(scores, forward) == pytest.approx(-1.0)

    def test_nan_pairs_dropped(self):
        scores = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": float("nan")})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04})
        assert spearman_ic(scores, forward) == pytest.approx(1.0)

    def test_fewer_than_three_pairs_is_nan(self):
        scores = pd.Series({"A": 1.0, "B": 2.0})
        forward = pd.Series({"A": 0.01, "B": 0.02})
        assert math.isnan(spearman_ic(scores, forward))

    def test_constant_scores_is_nan(self):
        scores = pd.Series({"A": 1.0, "B": 1.0, "C": 1.0})
        forward = pd.Series({"A": 0.01, "B": 0.02, "C": 0.03})
        assert math.isnan(spearman_ic(scores, forward))


class TestICSeries:
    def test_series_and_summary(self, us_store, write_prices):
        n = 60
        # AAA rises fastest, BBB medium, CCC flat -> IC = 1.0 on every date
        write_prices(us_store.cache, "US", "AAA", [100.0 + 2 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BBB", [100.0 + 1 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "CCC", [100.0] * n, start=date(2020, 1, 1))
        factor = FixedFactor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})

        ics = ic_series(factor, us_store, ["AAA", "BBB", "CCC"], [date(2020, 1, 31), date(2020, 2, 28)], 5)
        assert len(ics) == 2
        assert ics.iloc[0] == pytest.approx(1.0)
        assert ics.iloc[1] == pytest.approx(1.0)

        summary = summarize_ic(ics)
        assert summary.mean == pytest.approx(1.0)
        assert summary.n_periods == 2
        assert summary.positive_share == pytest.approx(1.0)
        assert math.isnan(summary.ir)  # std of constant series is 0 -> IR undefined

    def test_summary_of_empty_series(self):
        summary = summarize_ic(pd.Series(dtype=float))
        assert summary.n_periods == 0
        assert math.isnan(summary.mean)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_ic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.ic'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/ic.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.labels import forward_returns


def spearman_ic(factor_values: pd.Series, forward: pd.Series) -> float:
    """Spearman rank correlation between factor scores and forward returns.

    NaN pairs are dropped; fewer than 3 remaining pairs or a constant column
    yields NaN (correlation undefined — not zero)."""
    frame = pd.concat([factor_values, forward], axis=1, join="inner").dropna()
    if len(frame) < 3:
        return float("nan")
    scores, returns = frame.iloc[:, 0], frame.iloc[:, 1]
    if scores.nunique() < 2 or returns.nunique() < 2:
        return float("nan")
    return float(scores.corr(returns, method="spearman"))


def ic_series(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    horizon_days: int,
) -> pd.Series:
    """Per-date cross-sectional IC, indexed by date."""
    values = {
        pd.Timestamp(dt): spearman_ic(
            factor.compute(dt, universe, store),
            forward_returns(store, universe, dt, horizon_days),
        )
        for dt in dates
    }
    return pd.Series(values, name=f"ic_{factor.name}_{horizon_days}d", dtype=float)


@dataclass(frozen=True)
class ICSummary:
    mean: float
    std: float
    ir: float
    positive_share: float
    n_periods: int


def summarize_ic(ics: pd.Series) -> ICSummary:
    clean = ics.dropna()
    n = len(clean)
    if n == 0:
        nan = float("nan")
        return ICSummary(nan, nan, nan, nan, 0)
    mean = float(clean.mean())
    std = float(clean.std(ddof=1)) if n > 1 else float("nan")
    ir = mean / std if std and std > 0 else float("nan")
    return ICSummary(mean, std, ir, float((clean > 0).mean()), n)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_ic.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/ic.py tests/test_research_ic.py
git commit -m "M10(part): Add Spearman IC analysis"
```

---

### Task 3: 분위수 분석 + 팩터 회전율

**Files:**
- Create: `src/tradingbot/research/quantiles.py`
- Test: `tests/test_research_quantiles.py`

**Interfaces:**
- Consumes: `labels.forward_returns` (Task 1), `Factor` (기존), `ResearchDataStore` (Task 1)
- Produces:
  - `quantiles.quantile_assignments(factor_values: pd.Series, n_quantiles: int) -> pd.Series` (1=최저점수 .. n=최고점수, NaN 제외, 유효 수 부족 시 빈 Series)
  - `quantiles.quantile_returns(factor, store, universe, dates, horizon_days, n_quantiles=5) -> pd.DataFrame` (컬럼 `q1..qN`, `spread`)
  - `quantiles.monotonicity(quantile_means: Sequence[float]) -> float`
  - `quantiles.top_quantile_turnover(factor, store, universe, dates, n_quantiles=5) -> pd.Series`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_quantiles.py`:

```python
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.factors.base import Factor
from tradingbot.research.quantiles import (
    monotonicity,
    quantile_assignments,
    quantile_returns,
    top_quantile_turnover,
)


class FixedFactor(Factor):
    """Deterministic factor for tests: fixed score per symbol."""

    def __init__(self, scores: dict[str, float], name: str = "fixed") -> None:
        self.scores = scores
        self.name = name

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol in values.index:
            if symbol in self.scores:
                values.loc[symbol] = self.scores[symbol]
        return values


class ScheduledFactor(Factor):
    """Different fixed scores per date, for turnover tests."""

    name = "scheduled"

    def __init__(self, by_date: dict[date, dict[str, float]]) -> None:
        self.by_date = by_date

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol, score in self.by_date.get(dt, {}).items():
            values.loc[symbol.upper()] = score
        return values


class TestQuantileAssignments:
    def test_two_buckets(self):
        scores = pd.Series({"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0})
        q = quantile_assignments(scores, 2)
        assert q.loc["A"] == 2 and q.loc["B"] == 2
        assert q.loc["C"] == 1 and q.loc["D"] == 1

    def test_nan_excluded_and_too_few_yields_empty(self):
        scores = pd.Series({"A": 1.0, "B": float("nan")})
        assert quantile_assignments(scores, 2).empty

    def test_invalid_n_quantiles_raises(self):
        with pytest.raises(ValueError):
            quantile_assignments(pd.Series({"A": 1.0}), 1)


class TestQuantileReturns:
    def test_spread(self, us_store, write_prices):
        # WIN jumps to 110 after row 20, LOSE stays flat at 100.
        write_prices(us_store.cache, "US", "WIN", [100.0] * 20 + [110.0] * 20, start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "LOSE", [100.0] * 40, start=date(2020, 1, 1))
        factor = FixedFactor({"WIN": 2.0, "LOSE": 1.0})
        dt = pd.bdate_range(start="2020-01-01", periods=40)[19].date()

        frame = quantile_returns(factor, us_store, ["WIN", "LOSE"], [dt], horizon_days=5, n_quantiles=2)
        row = frame.loc[pd.Timestamp(dt)]
        assert row["q2"] == pytest.approx(0.10)
        assert row["q1"] == pytest.approx(0.0)
        assert row["spread"] == pytest.approx(0.10)

    def test_dates_without_enough_scores_are_skipped(self, us_store):
        factor = FixedFactor({})  # nothing scored
        frame = quantile_returns(factor, us_store, ["AAA", "BBB"], [date(2020, 1, 31)], 5, n_quantiles=2)
        assert frame.empty


class TestMonotonicity:
    def test_values(self):
        assert monotonicity([0.0, 0.01, 0.02]) == pytest.approx(1.0)
        assert monotonicity([0.02, 0.01, 0.0]) == pytest.approx(0.0)
        assert monotonicity([0.0, 0.01, 0.005]) == pytest.approx(0.5)
        assert math.isnan(monotonicity([]))


class TestTopQuantileTurnover:
    def test_full_swap_is_one(self, us_store):
        d1, d2 = date(2020, 1, 15), date(2020, 2, 14)
        factor = ScheduledFactor(
            {
                d1: {"AAA": 4.0, "BBB": 3.0, "CCC": 2.0, "DDD": 1.0},
                d2: {"CCC": 4.0, "DDD": 3.0, "AAA": 2.0, "BBB": 1.0},
            }
        )
        universe = ["AAA", "BBB", "CCC", "DDD"]
        turnover = top_quantile_turnover(factor, us_store, universe, [d1, d2], n_quantiles=2)
        assert list(turnover) == [1.0]

    def test_no_change_is_zero(self, us_store):
        scores = {"AAA": 4.0, "BBB": 3.0, "CCC": 2.0, "DDD": 1.0}
        d1, d2 = date(2020, 1, 15), date(2020, 2, 14)
        factor = ScheduledFactor({d1: scores, d2: scores})
        turnover = top_quantile_turnover(factor, us_store, list(scores), [d1, d2], n_quantiles=2)
        assert list(turnover) == [0.0]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_quantiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.quantiles'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/quantiles.py`:

```python
from __future__ import annotations

import math
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.labels import forward_returns


def quantile_assignments(factor_values: pd.Series, n_quantiles: int) -> pd.Series:
    """Assign 1 (lowest score) .. n_quantiles (highest). NaN scores excluded.

    Fewer valid scores than quantiles yields an empty assignment."""
    if n_quantiles < 2:
        raise ValueError("n_quantiles must be at least 2")
    clean = factor_values.dropna()
    if len(clean) < n_quantiles:
        return pd.Series(dtype=int)
    ranks = clean.rank(method="first")
    return pd.qcut(ranks, n_quantiles, labels=False).astype(int) + 1


def quantile_returns(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    horizon_days: int,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Equal-weight mean forward return per quantile, per date.

    Columns: q1..qN and 'spread' (top minus bottom). Dates without enough
    scored symbols are skipped."""
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for dt in dates:
        scores = factor.compute(dt, universe, store)
        assignments = quantile_assignments(scores, n_quantiles)
        if assignments.empty:
            continue
        forwards = forward_returns(store, list(assignments.index), dt, horizon_days)
        row: dict[str, float] = {}
        for quantile in range(1, n_quantiles + 1):
            members = assignments.index[assignments == quantile]
            row[f"q{quantile}"] = float(forwards.loc[members].mean())
        row["spread"] = row[f"q{n_quantiles}"] - row["q1"]
        rows[pd.Timestamp(dt)] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def monotonicity(quantile_means: Sequence[float]) -> float:
    """Share of adjacent quantile pairs whose mean return strictly increases."""
    pairs = [
        (low, high)
        for low, high in zip(quantile_means, quantile_means[1:])
        if not (math.isnan(low) or math.isnan(high))
    ]
    if not pairs:
        return float("nan")
    return sum(1 for low, high in pairs if high > low) / len(pairs)


def top_quantile_turnover(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    n_quantiles: int = 5,
) -> pd.Series:
    """1 - overlap of the top-quantile set with the previous date's set."""
    previous: set[str] | None = None
    values: dict[pd.Timestamp, float] = {}
    for dt in dates:
        assignments = quantile_assignments(factor.compute(dt, universe, store), n_quantiles)
        if assignments.empty:
            continue
        top = set(assignments.index[assignments == n_quantiles])
        if previous:
            values[pd.Timestamp(dt)] = 1.0 - len(top & previous) / len(previous)
        previous = top
    return pd.Series(values, name=f"turnover_{factor.name}", dtype=float)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_quantiles.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/quantiles.py tests/test_research_quantiles.py
git commit -m "M10(part): Add quantile-return analysis and factor turnover"
```

---

### Task 4: Walk-forward 검증

**Files:**
- Create: `src/tradingbot/research/walk_forward.py`
- Test: `tests/test_research_walk_forward.py`

**Interfaces:**
- Consumes: `ic.ic_series`, `ic.summarize_ic` (Task 2), `dates.month_end_trading_days` (Task 1)
- Produces:
  - `walk_forward.WalkForwardWindow` (frozen dataclass: `train_start, train_end, test_start, test_end: date`)
  - `walk_forward.walk_forward_windows(start, end, *, train_years, test_years, step_years) -> list[WalkForwardWindow]`
  - `walk_forward.walk_forward_ic(factor, store, universe, *, market, horizon_days, windows) -> pd.DataFrame` (컬럼: train_start, train_end, test_start, test_end, test_ic_mean, test_ic_ir, n_periods)
  - `walk_forward.window_win_rate(results: pd.DataFrame) -> float`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_walk_forward.py`:

```python
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.factors.base import Factor
from tradingbot.research.walk_forward import (
    WalkForwardWindow,
    walk_forward_ic,
    walk_forward_windows,
    window_win_rate,
)


class FixedFactor(Factor):
    """Deterministic factor for tests: fixed score per symbol."""

    def __init__(self, scores: dict[str, float], name: str = "fixed") -> None:
        self.scores = scores
        self.name = name

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol in values.index:
            if symbol in self.scores:
                values.loc[symbol] = self.scores[symbol]
        return values


class TestWalkForwardWindows:
    def test_three_windows_2010_to_2015(self):
        windows = walk_forward_windows(
            date(2010, 1, 1), date(2015, 12, 31), train_years=3, test_years=1, step_years=1
        )
        assert len(windows) == 3
        first = windows[0]
        assert first.train_start == date(2010, 1, 1)
        assert first.train_end == date(2012, 12, 31)
        assert first.test_start == date(2013, 1, 1)
        assert first.test_end == date(2013, 12, 31)
        assert windows[-1].test_end == date(2015, 12, 31)

    def test_last_window_test_end_capped_at_end(self):
        windows = walk_forward_windows(
            date(2010, 1, 1), date(2013, 6, 30), train_years=3, test_years=1, step_years=1
        )
        assert len(windows) == 1
        assert windows[0].test_end == date(2013, 6, 30)

    def test_invalid_years_raise(self):
        with pytest.raises(ValueError):
            walk_forward_windows(date(2010, 1, 1), date(2015, 1, 1), train_years=0, test_years=1, step_years=1)


class TestWalkForwardIC:
    def test_single_window_perfect_ic(self, us_store, write_prices):
        n = 300
        write_prices(us_store.cache, "US", "AAA", [100.0 + 2 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "BBB", [100.0 + 1 * i for i in range(n)], start=date(2020, 1, 1))
        write_prices(us_store.cache, "US", "CCC", [100.0] * n, start=date(2020, 1, 1))
        windows = [
            WalkForwardWindow(date(2019, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 6, 30))
        ]
        factor = FixedFactor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})

        results = walk_forward_ic(
            factor, us_store, ["AAA", "BBB", "CCC"], market="US", horizon_days=5, windows=windows
        )
        assert len(results) == 1
        assert results.loc[0, "test_ic_mean"] == pytest.approx(1.0)
        assert results.loc[0, "n_periods"] == 6  # Jan..Jun month-ends
        assert window_win_rate(results) == pytest.approx(1.0)

    def test_win_rate_of_empty_results_is_nan(self):
        assert math.isnan(window_win_rate(pd.DataFrame()))
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_walk_forward.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.walk_forward'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/walk_forward.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.dates import month_end_trading_days
from tradingbot.research.ic import ic_series, summarize_ic


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def _add_years(day: date, years: int) -> date:
    return date(day.year + years, day.month, day.day)


def walk_forward_windows(
    start: date, end: date, *, train_years: int, test_years: int, step_years: int
) -> list[WalkForwardWindow]:
    """Rolling windows: train `train_years`, test `test_years`, advance by
    `step_years`. The last window's test end is capped at `end`; a window
    whose test period would start after `end` is dropped."""
    if min(train_years, test_years, step_years) <= 0:
        raise ValueError("train_years, test_years, and step_years must be positive")
    if start.month == 2 and start.day == 29:
        raise ValueError("start must not be Feb 29")
    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        train_end = _add_years(train_start, train_years) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = _add_years(test_start, test_years) - timedelta(days=1)
        if test_start > end:
            break
        windows.append(WalkForwardWindow(train_start, train_end, test_start, min(test_end, end)))
        train_start = _add_years(train_start, step_years)
    return windows


def walk_forward_ic(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    *,
    market: str,
    horizon_days: int,
    windows: Sequence[WalkForwardWindow],
) -> pd.DataFrame:
    """Test-segment IC summary per window (month-end evaluation dates)."""
    rows = []
    for window in windows:
        dates = month_end_trading_days(market, window.test_start, window.test_end)
        summary = summarize_ic(ic_series(factor, store, universe, dates, horizon_days))
        rows.append(
            {
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "test_ic_mean": summary.mean,
                "test_ic_ir": summary.ir,
                "n_periods": summary.n_periods,
            }
        )
    return pd.DataFrame(rows)


def window_win_rate(results: pd.DataFrame) -> float:
    """Share of windows with positive test IC mean; NaN when empty."""
    if results.empty:
        return float("nan")
    return float((results["test_ic_mean"] > 0).mean())
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_walk_forward.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/walk_forward.py tests/test_research_walk_forward.py
git commit -m "M10(part): Add walk-forward IC validation"
```

---

### Task 5: 실험 기록

**Files:**
- Create: `src/tradingbot/research/experiment.py`
- Test: `tests/test_research_experiment.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만)
- Produces:
  - `experiment.current_git_commit(cwd: Path | None = None) -> str` ("unknown" fallback)
  - `experiment.record_experiment(root: Path, *, kind: str, params: dict, metrics: dict, created_at: datetime | None = None) -> Path`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_experiment.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

from tradingbot.research.experiment import current_git_commit, record_experiment


def test_record_experiment_writes_json(tmp_path):
    path = record_experiment(
        tmp_path / "experiments", kind="factor_report", params={"market": "US"}, metrics={"ic": 0.03}
    )
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["kind"] == "factor_report"
    assert record["params"] == {"market": "US"}
    assert record["metrics"] == {"ic": 0.03}
    assert record["experiment_id"] == path.stem
    assert record["git_commit"]  # hash or "unknown", never empty


def test_record_experiment_ids_are_unique_even_at_same_timestamp(tmp_path):
    root = tmp_path / "experiments"
    created = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    first = record_experiment(root, kind="x", params={}, metrics={}, created_at=created)
    second = record_experiment(root, kind="x", params={}, metrics={}, created_at=created)
    assert first != second


def test_current_git_commit_outside_repo_is_unknown(tmp_path):
    assert current_git_commit(cwd=tmp_path) == "unknown"
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_experiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.experiment'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/experiment.py`:

```python
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def current_git_commit(cwd: Path | None = None) -> str:
    """Current HEAD hash, or 'unknown' outside a repo / without git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def record_experiment(
    root: Path,
    *,
    kind: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    created_at: datetime | None = None,
) -> Path:
    """Write one experiment record as JSON under `root`; returns the path."""
    root.mkdir(parents=True, exist_ok=True)
    created = created_at or datetime.now(timezone.utc)
    experiment_id = f"{created:%Y%m%dT%H%M%S}_{kind}_{uuid4().hex[:8]}"
    record = {
        "experiment_id": experiment_id,
        "kind": kind,
        "git_commit": current_git_commit(),
        "created_at": created.isoformat(),
        "params": params,
        "metrics": metrics,
    }
    path = root / f"{experiment_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_experiment.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/experiment.py tests/test_research_experiment.py
git commit -m "M10(part): Add experiment recording"
```

---

### Task 6: 팩터 채택 게이트 (설정 + 평가)

**Files:**
- Modify: `config/research.toml` (`[factor_gate]` 섹션 추가)
- Create: `src/tradingbot/research/gate.py`
- Test: `tests/test_research_gate.py`

**Interfaces:**
- Consumes: `ic.ICSummary` (Task 2), `tradingbot.config.PROJECT_ROOT` (기존)
- Produces:
  - `gate.load_research_config(path: str | Path | None = None) -> dict` (기본: `config/research.toml`)
  - `gate.GateThresholds` (frozen dataclass: `horizon_days, n_quantiles: int; min_ic_mean, min_ic_ir, min_monotonicity: float`)
  - `gate.load_gate_thresholds(research_config: dict) -> GateThresholds`
  - `gate.GateResult` (frozen dataclass: `passed: bool; reasons: list[str]`)
  - `gate.evaluate_gate(ic: ICSummary, monotonicity: float, thresholds: GateThresholds) -> GateResult`

- [ ] **Step 1: config/research.toml에 섹션 추가**

`config/research.toml` 파일 끝에 추가:

```toml
[factor_gate]
# 팩터 채택 게이트 — In-sample 구간에서만 조정한다 (docs/quant_research_spec.md 5장).
# 팩터 점수는 "높을수록 좋음"으로 방향 통일된 상태를 전제로 signed IC를 사용한다.
horizon_days = 20        # IC/분위수 평가용 forward return 기간 (거래일)
n_quantiles = 5
min_ic_mean = 0.01
min_ic_ir = 0.30
min_monotonicity = 0.60  # 인접 분위 쌍 중 단조 증가 비율
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_research_gate.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.research.gate import (
    GateThresholds,
    evaluate_gate,
    load_gate_thresholds,
    load_research_config,
)
from tradingbot.research.ic import ICSummary

THRESHOLDS = GateThresholds(
    horizon_days=20, n_quantiles=5, min_ic_mean=0.01, min_ic_ir=0.30, min_monotonicity=0.60
)


def make_summary(mean: float, ir: float) -> ICSummary:
    return ICSummary(mean=mean, std=0.05, ir=ir, positive_share=0.6, n_periods=24)


def test_gate_passes_when_all_thresholds_met():
    result = evaluate_gate(make_summary(0.02, 0.40), 0.75, THRESHOLDS)
    assert result.passed
    assert result.reasons == []


def test_gate_fails_low_ir_with_reason():
    result = evaluate_gate(make_summary(0.02, 0.10), 0.75, THRESHOLDS)
    assert not result.passed
    assert any("ic_ir" in reason for reason in result.reasons)


def test_gate_nan_metrics_fail_all_checks():
    nan = float("nan")
    result = evaluate_gate(ICSummary(nan, nan, nan, nan, 0), nan, THRESHOLDS)
    assert not result.passed
    assert len(result.reasons) == 3


def test_load_gate_thresholds_from_repo_config():
    thresholds = load_gate_thresholds(load_research_config())
    assert thresholds.n_quantiles >= 2
    assert 0 < thresholds.min_monotonicity <= 1


def test_load_research_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_research_config(tmp_path / "nope.toml")
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.gate'`

- [ ] **Step 4: 구현**

`src/tradingbot/research/gate.py`:

```python
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradingbot.config import PROJECT_ROOT
from tradingbot.research.ic import ICSummary

RESEARCH_CONFIG_PATH = PROJECT_ROOT / "config" / "research.toml"


def load_research_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else RESEARCH_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Research config not found: {config_path}")
    with config_path.open("rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class GateThresholds:
    horizon_days: int
    n_quantiles: int
    min_ic_mean: float
    min_ic_ir: float
    min_monotonicity: float


def load_gate_thresholds(research_config: dict[str, Any]) -> GateThresholds:
    section = research_config["factor_gate"]
    return GateThresholds(
        horizon_days=int(section["horizon_days"]),
        n_quantiles=int(section["n_quantiles"]),
        min_ic_mean=float(section["min_ic_mean"]),
        min_ic_ir=float(section["min_ic_ir"]),
        min_monotonicity=float(section["min_monotonicity"]),
    )


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]


def evaluate_gate(ic: ICSummary, monotonicity: float, thresholds: GateThresholds) -> GateResult:
    """Check a factor against the acceptance gate.

    NaN metrics fail their check (comparison with NaN is False), so factors
    with insufficient data are rejected loudly rather than passed silently."""
    reasons: list[str] = []
    if not ic.mean >= thresholds.min_ic_mean:
        reasons.append(f"ic_mean {ic.mean:.4f} < {thresholds.min_ic_mean}")
    if not ic.ir >= thresholds.min_ic_ir:
        reasons.append(f"ic_ir {ic.ir:.4f} < {thresholds.min_ic_ir}")
    if not monotonicity >= thresholds.min_monotonicity:
        reasons.append(f"monotonicity {monotonicity:.4f} < {thresholds.min_monotonicity}")
    return GateResult(passed=not reasons, reasons=reasons)
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_gate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: 커밋**

```powershell
git add config/research.toml src/tradingbot/research/gate.py tests/test_research_gate.py
git commit -m "M10(part): Add factor acceptance gate config and evaluation"
```

---

### Task 7: 팩터 리포트 조립 + `research report` CLI + 문서

**Files:**
- Create: `src/tradingbot/research/report.py`
- Modify: `src/tradingbot/cli.py` (research 서브커맨드 + 핸들러)
- Modify: `README.md` (확장 목록에 M10 한 줄 추가)
- Modify: `docs/architecture.md` (§7 주요 코드 위치 표에 research/ 행 추가)
- Test: `tests/test_research_report.py`

**Interfaces:**
- Consumes: Task 1~6의 모든 Produces + `factors.get_factor/list_factors` (기존 registry), `config.resolve_project_path` (기존)
- Produces:
  - `report.build_factor_report(*, store, market, universe, factors, dates, windows, thresholds) -> dict`
    (구조: `{"market", "universe", "n_dates", "horizon_days", "n_quantiles", "factors": {name: {"ic": {...}, "quantile_means": [...], "spread_mean", "monotonicity", "turnover_mean", "walk_forward": {"windows": [...], "win_rate"}, "gate": {"passed", "reasons"}}}}`)
  - `report.render_markdown(report: dict) -> str`
  - CLI: `tradingbot research report [--research-config P] [--factors N ...] [--start D] [--end D] [--data-root P] [--out P]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_report.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from tradingbot.cli import build_parser, cmd_research_report
from tradingbot.factors.base import Factor
from tradingbot.research.dates import month_end_trading_days
from tradingbot.research.gate import GateThresholds
from tradingbot.research.report import build_factor_report, render_markdown
from tradingbot.research.walk_forward import WalkForwardWindow


class FixedFactor(Factor):
    """Deterministic factor for tests: fixed score per symbol."""

    def __init__(self, scores: dict[str, float], name: str = "fixed") -> None:
        self.scores = scores
        self.name = name

    def compute(self, dt, universe, data_store):
        values = self._empty(universe)
        for symbol in values.index:
            if symbol in self.scores:
                values.loc[symbol] = self.scores[symbol]
        return values


@pytest.fixture
def report(us_store, write_prices):
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
        factors=[FixedFactor({"AAA": 3.0, "BBB": 2.0, "CCC": 1.0})],
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
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.report'`

- [ ] **Step 3: report.py 구현**

`src/tradingbot/research/report.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.gate import GateThresholds, evaluate_gate
from tradingbot.research.ic import ic_series, summarize_ic
from tradingbot.research.quantiles import monotonicity, quantile_returns, top_quantile_turnover
from tradingbot.research.walk_forward import WalkForwardWindow, walk_forward_ic, window_win_rate


def build_factor_report(
    *,
    store: ResearchDataStore,
    market: str,
    universe: Sequence[str],
    factors: Sequence[Factor],
    dates: Sequence[date],
    windows: Sequence[WalkForwardWindow],
    thresholds: GateThresholds,
) -> dict[str, Any]:
    """IC / quantile / walk-forward / gate summary for each factor."""
    report: dict[str, Any] = {
        "market": market,
        "universe": list(universe),
        "n_dates": len(dates),
        "horizon_days": thresholds.horizon_days,
        "n_quantiles": thresholds.n_quantiles,
        "factors": {},
    }
    for factor in factors:
        ic_summary = summarize_ic(
            ic_series(factor, store, universe, dates, thresholds.horizon_days)
        )
        quantile_frame = quantile_returns(
            factor, store, universe, dates, thresholds.horizon_days, thresholds.n_quantiles
        )
        if quantile_frame.empty:
            quantile_means: list[float] = []
            spread_mean = float("nan")
        else:
            quantile_means = [
                float(quantile_frame[f"q{q}"].mean())
                for q in range(1, thresholds.n_quantiles + 1)
            ]
            spread_mean = float(quantile_frame["spread"].mean())
        mono = monotonicity(quantile_means)
        turnover = top_quantile_turnover(factor, store, universe, dates, thresholds.n_quantiles)
        wf = walk_forward_ic(
            factor,
            store,
            universe,
            market=market,
            horizon_days=thresholds.horizon_days,
            windows=windows,
        )
        gate = evaluate_gate(ic_summary, mono, thresholds)
        report["factors"][factor.name] = {
            "ic": {
                "mean": ic_summary.mean,
                "std": ic_summary.std,
                "ir": ic_summary.ir,
                "positive_share": ic_summary.positive_share,
                "n_periods": ic_summary.n_periods,
            },
            "quantile_means": quantile_means,
            "spread_mean": spread_mean,
            "monotonicity": mono,
            "turnover_mean": float(turnover.mean()) if len(turnover) else float("nan"),
            "walk_forward": {
                "windows": wf.to_dict(orient="records"),
                "win_rate": window_win_rate(wf),
            },
            "gate": {"passed": gate.passed, "reasons": gate.reasons},
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Factor Research Report",
        "",
        f"- Market: {report['market']}",
        f"- Universe: {', '.join(report['universe'])}",
        f"- Evaluation dates: {report['n_dates']} (month-end)",
        f"- Horizon: {report['horizon_days']} trading days, quantiles: {report['n_quantiles']}",
        "",
        "| factor | IC mean | IC IR | IC>0 | mono | spread | turnover | WF win | gate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, data in report["factors"].items():
        ic = data["ic"]
        gate = "PASS" if data["gate"]["passed"] else "FAIL: " + "; ".join(data["gate"]["reasons"])
        lines.append(
            f"| {name} | {ic['mean']:.4f} | {ic['ir']:.2f} | {ic['positive_share']:.0%} "
            f"| {data['monotonicity']:.2f} | {data['spread_mean']:.4f} "
            f"| {data['turnover_mean']:.2f} | {data['walk_forward']['win_rate']:.0%} | {gate} |"
        )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: CLI 구현**

`src/tradingbot/cli.py` 수정. 파일 상단 import에 추가:

```python
from datetime import date as _date
from datetime import datetime as _datetime
```

`build_parser()`의 `gui_parser` 블록 앞에 추가:

```python
    research_parser = subparsers.add_parser("research", help="Factor research commands")
    research_subparsers = research_parser.add_subparsers(dest="research_command")
    factor_report_parser = research_subparsers.add_parser(
        "report", help="IC / quantile / walk-forward factor report"
    )
    factor_report_parser.add_argument("--research-config", default=None, help="research.toml path")
    factor_report_parser.add_argument(
        "--factors", nargs="+", default=None, help="Factor names (default: all registered)"
    )
    factor_report_parser.add_argument("--start", default=None, help="Evaluation start (default: in_sample_start)")
    factor_report_parser.add_argument("--end", default=None, help="Evaluation end (default: validation_end)")
    factor_report_parser.add_argument("--data-root", default=None)
    factor_report_parser.add_argument("--out", default="reports/research")
    factor_report_parser.set_defaults(handler=cmd_research_report)
```

파일 끝에 핸들러 추가:

```python
def cmd_research_report(args) -> int:
    from tradingbot.data.cache import ParquetCache
    from tradingbot.data.store import ParquetDataStore
    from tradingbot.factors import get_factor, list_factors
    from tradingbot.research.dates import month_end_trading_days
    from tradingbot.research.experiment import record_experiment
    from tradingbot.research.gate import load_gate_thresholds, load_research_config
    from tradingbot.research.report import build_factor_report, render_markdown
    from tradingbot.research.walk_forward import walk_forward_windows

    research = load_research_config(args.research_config)
    market = research["universe"]["market"]
    universe = research["universe"]["symbols"]
    thresholds = load_gate_thresholds(research)
    periods = research["periods"]
    start = _date.fromisoformat(args.start or periods["in_sample_start"])
    end = _date.fromisoformat(args.end or periods["validation_end"])

    store = ParquetDataStore(ParquetCache(resolve_project_path(args.data_root or "data/cache")), market)
    factor_names = args.factors or list_factors()
    factors = [get_factor(name) for name in factor_names]
    dates = month_end_trading_days(market, start, end)
    wf_config = research["walk_forward"]
    windows = walk_forward_windows(
        start,
        end,
        train_years=int(wf_config["train_years"]),
        test_years=int(wf_config["test_years"]),
        step_years=int(wf_config["step_years"]),
    )

    report = build_factor_report(
        store=store,
        market=market,
        universe=universe,
        factors=factors,
        dates=dates,
        windows=windows,
        thresholds=thresholds,
    )
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_factor_report.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"리포트 저장: {out_path}")

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="factor_report",
        params={
            "market": market,
            "universe": universe,
            "factors": factor_names,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizon_days": thresholds.horizon_days,
            "n_quantiles": thresholds.n_quantiles,
        },
        metrics={
            name: data["ic"] | {"gate_passed": data["gate"]["passed"]}
            for name, data in report["factors"].items()
        },
    )
    print(f"실험 기록: {experiment_path}")
    return 0
```

`get_factor`/`list_factors`는 `tradingbot.factors` 패키지에서 이미 export되어 있다 (`tests/test_factors.py`가 `from tradingbot.factors import get_factor, list_factors` 형태로 사용 중). 위 핸들러의 임포트를 그대로 쓰면 된다.

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_report.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: 전체 테스트 회귀 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 전체 PASS (기존 테스트 포함, 실패 0)

- [ ] **Step 7: 실데이터 검증 (완료 기준 확인)**

연구 유니버스 ETF 시세를 받고 리포트를 실행한다 (네트워크 필요, 리포트 자체는 캐시만 사용):

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2008-01-01
.\.venv\Scripts\python.exe -m tradingbot research report
```

Expected: 콘솔에 `momentum_3m`, `momentum_6m`, `momentum_12m`, `momentum_12m_ex1m` 4행짜리 마크다운 표가 출력되고 (IC 값 자체는 데이터에 따라 다름 — 값 검증은 하지 않는다), `reports/research/*_factor_report.md`와 `data/experiments/*_factor_report_*.json` 파일이 생성된다. 게이트 PASS/FAIL 결과가 각 팩터에 표시된다.

- [ ] **Step 8: 문서 갱신**

`README.md`의 "현재까지 반영된 확장" 목록에 추가:

```markdown
- 연구·검증 프레임워크(`research/`): forward return 라벨, Spearman IC,
  분위수 분석, Walk-forward, 실험 기록, 팩터 채택 게이트와
  `research report` CLI — M10
```

`docs/architecture.md` §7 "주요 코드 위치" 표에 행 추가:

```markdown
| 연구·검증 (IC/분위수/Walk-forward) | `src/tradingbot/research/` |
```

- [ ] **Step 9: 커밋**

```powershell
git add src/tradingbot/research/report.py src/tradingbot/cli.py tests/test_research_report.py README.md docs/architecture.md
git commit -m "M10: Add research report CLI with IC, quantile, and walk-forward analysis"
```

---

## 완료 기준 (스펙 §3 Phase 1)

- [ ] `tradingbot research report`가 기존 모멘텀 팩터 4종에 대해 IC / 분위수 / Walk-forward / 게이트 결과를 담은 리포트를 생성한다.
- [ ] 실험 기록이 `data/experiments/`에 git commit 해시와 함께 남는다.
- [ ] 전체 테스트가 통과하고 기존 회귀 테스트가 깨지지 않는다.
