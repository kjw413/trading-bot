# 미국 시장 ETF 로테이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이미 `market` 파라미터를 받고 있는 시스템이 실제로 미국 시장에서 동작하게 만들고, 11개 ETF 로테이션 전략을 2007년부터의 실데이터로 검증한다.

**Architecture:** 새 추상화를 만들지 않는다. 거시 시리즈를 시장별 매핑으로 나누고, 파이프라인이 수집기별 적용 시장을 확인해 해당 없으면 `skipped`로 보고하며, 전략에 팩터 목록과 절대 모멘텀 필터 두 파라미터를 추가한다. 나머지는 설정 파일이 담당한다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-07-26-us-market-etf-rotation-design.md`

## Global Constraints

- **신규 의존성 추가 금지.**
- **한국 동작을 바꾸지 않는다.** `abs_momentum_ma_days` 기본값 0(비활성), `factors` 기본값 `None`(전체 사용). 기존 한국 백테스트 결과가 이번 변경으로 달라지면 안 된다 — Task 5에서 수치로 확인한다.
- **시장 부적용은 실패가 아니라 `skipped`**: 파이프라인 종료코드 0을 유지하고, 이유가 콘솔과 실행 로그 JSON에 남는다.
- **Point-in-Time 준수**: 팩터·필터는 `price_history`(as-of cutoff)와 `panel(as_of=...)`만 사용한다. `close_series` 호출 금지.
- **점수 없는 종목은 NaN** — 조용히 드롭하지 않는다. 절대 모멘텀 필터도 제외를 NaN으로 표현한다.
- **죽은 티커를 기본값으로 넣지 않는다.** 미국 거시 심볼은 Task 1에서 실제로 호출해 확인하고, 응답이 없으면 대안을 찾거나 그 시리즈를 뺀다.
- 테스트에서 네트워크 접근 금지 (실데이터 단계는 Task 5에서만).
- 기존 CLI 명령 동작 변경 금지. `Strategy` ABC 시그니처 변경 금지.
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 접두사: 중간 `US(part):`, 마지막 `US:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `PanelStore(root, dataset, market)` — `.market`은 대문자 시장 코드, `.append(frame)->int`, `.read(*, as_of=None, start=None, end=None, symbols=None)`, `.last_date(symbol=None)`
- `update_macro(store, *, series=None, start=None, end=None, fetcher=fetch_macro_series) -> int` (`data/macro.py`)
- `fetch_macro_series(series, start, end=None) -> pd.DataFrame` — `MACRO_SERIES[series]`로 심볼을 찾아 FinanceDataReader 호출; 컬럼 `date`, `symbol`, `close`
- `run_pipeline(config, *, market, symbols=None, processed_root=None, log_root=None, collectors=None) -> PipelineResult` (`data/pipeline.py`)
- `SourceResult(name, status, rows, message)` — 상태 상수 `STATUS_OK="ok"`, `STATUS_FAILED="failed"`, `STATUS_SKIPPED="skipped"`
- `_default_collectors(processed_root, symbols, market, fundamental_years, cache_root) -> dict[str, Callable[..., int]]` — 키 순서 `prices, macro, flows, valuation, fundamentals`
- `ThemeMultifactorStrategy` (`strategies/theme_multifactor.py`) — `default_params` dict, `factor_weights` property, `generate_targets(dt, universe, data_store) -> dict[str, float]`, `_is_price_data_stale(dt, universe, data_store) -> bool`
- `get_factor(name)` (`factors/registry.py`) — 미등록 이름에 ValueError
- `select_top(scores, top_n) -> list[str]` (`allocation/ranking.py`) — NaN 제외
- `data_store.price_history(symbol, end, lookback) -> pd.DataFrame` — 뒤에서 `lookback`행

---

### Task 1: 시장별 거시 시리즈

**Files:**
- Modify: `src/tradingbot/data/macro.py`
- Test: `tests/test_data_macro.py`

**Interfaces:**
- Consumes: `PanelStore.market` (기존)
- Produces:
  - `macro.MACRO_SERIES_BY_MARKET: dict[str, dict[str, str]]` — 시장 → {시리즈명: FDR 심볼}
  - `macro.MACRO_SERIES: dict[str, str]` — 전 시장 심볼의 합집합 (기존 이름 유지, `fetch_macro_series`의 조회 대상)
  - `update_macro`가 `series=None`일 때 `store.market`에 해당하는 시리즈만 수집

- [ ] **Step 1: 미국 거시 심볼을 실제로 확인**

죽은 티커를 기본값에 넣지 않기 위해 후보를 직접 호출한다. 스크래치패드에 스크립트를 쓰고 실행한다:

```python
import FinanceDataReader as fdr
import datetime as dt

candidates = {
    "sp500": ["US500", "^GSPC", "S&P500"],
    "nasdaq": ["IXIC", "^IXIC", "NASDAQ"],
    "us_treasury_10y": ["US10YT=X", "^TNX"],
    "vix": ["VIX"],
}
for name, symbols in candidates.items():
    for symbol in symbols:
        try:
            frame = fdr.DataReader(symbol, dt.date(2024, 1, 1))
            print(f"{name:18} {symbol:12} rows={len(frame)} cols={list(frame.columns)[:3]}")
        except Exception as exc:
            print(f"{name:18} {symbol:12} ERROR {type(exc).__name__}: {str(exc)[:80]}")
```

각 시리즈에서 **행이 실제로 돌아오는 첫 심볼을 채택**한다. 네 시리즈 모두 실패하면 그 시리즈를 빼고, 무엇을 왜 뺐는지 보고에 적는다. 아래 구현 코드의 심볼은 이 결과로 교체한다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data_macro.py`의 `TestMacroSeries` 클래스에 추가:

```python
    def test_series_are_scoped_by_market(self):
        from tradingbot.data.macro import MACRO_SERIES_BY_MARKET

        assert "kospi" in MACRO_SERIES_BY_MARKET["KR"]
        assert "sp500" in MACRO_SERIES_BY_MARKET["US"]
        # A US panel must never be filled with Korean indices.
        assert "kospi" not in MACRO_SERIES_BY_MARKET["US"]
        assert "sp500" not in MACRO_SERIES_BY_MARKET["KR"]

    def test_flat_lookup_covers_every_market(self):
        from tradingbot.data.macro import MACRO_SERIES, MACRO_SERIES_BY_MARKET

        for series in MACRO_SERIES_BY_MARKET.values():
            for name, symbol in series.items():
                assert MACRO_SERIES[name] == symbol
```

같은 파일 끝에 새 클래스를 추가:

```python
class TestMarketScopedCollection:
    def test_us_store_collects_only_us_series(self, tmp_path):
        from tradingbot.data.macro import MACRO_SERIES_BY_MARKET

        store = PanelStore(tmp_path, "macro", "US")
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        collected = {symbol.upper() for symbol in store.read()["symbol"]}
        assert collected == {name.upper() for name in MACRO_SERIES_BY_MARKET["US"]}

    def test_kr_store_collects_only_kr_series(self, store):
        from tradingbot.data.macro import MACRO_SERIES_BY_MARKET

        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        collected = {symbol.upper() for symbol in store.read()["symbol"]}
        assert collected == {name.upper() for name in MACRO_SERIES_BY_MARKET["KR"]}

    def test_unknown_market_fails_loudly(self, tmp_path):
        store = PanelStore(tmp_path, "macro", "JP")
        # Silently collecting nothing would look like a quiet day forever.
        with pytest.raises(ValueError, match="JP"):
            update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)

    def test_explicit_series_still_honored(self, tmp_path):
        store = PanelStore(tmp_path, "macro", "US")
        update_macro(store, series=["vix"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert set(store.read()["symbol"]) == {"VIX"}
```

기존 테스트 `test_defaults_to_all_registered_series`는 **의도적으로 바뀐 동작**과 충돌한다 (전 시리즈 → 시장별 시리즈). 다음으로 교체한다:

```python
    def test_defaults_to_the_markets_own_series(self, store):
        from tradingbot.data.macro import MACRO_SERIES_BY_MARKET

        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(set(store.read()["symbol"])) == len(MACRO_SERIES_BY_MARKET["KR"])
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_macro.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ImportError: cannot import name 'MACRO_SERIES_BY_MARKET'`

- [ ] **Step 4: 구현**

`src/tradingbot/data/macro.py`의 `MACRO_SERIES` 정의를 다음으로 교체한다. **심볼은 Step 1의 실측 결과로 바꿔 넣는다** — 아래는 후보 기본값이다:

```python
# 시장 -> {시리즈명: FinanceDataReader 심볼}. 국면 필터와 리스크 맥락에 쓰이며,
# 종목 팩터가 아니다. 패널 저장 경로가 시장별(macro/{MARKET}/)이므로 한 시장의
# 패널에 다른 시장 지수가 섞이지 않아야 한다.
#
# kr_treasury_3y (KR3YT=RR)는 제거됨: Yahoo 백엔드 티커가 404를 내고 대체
# 3년물 지표를 FinanceDataReader에서 찾지 못했다.
MACRO_SERIES_BY_MARKET: dict[str, dict[str, str]] = {
    "KR": {
        "kospi": "KS11",
        "kosdaq": "KQ11",
        "usdkrw": "USD/KRW",
        "vix": "VIX",
    },
    "US": {
        "sp500": "US500",
        "nasdaq": "IXIC",
        "us_treasury_10y": "US10YT=X",
        "vix": "VIX",
    },
}

# 전 시장 심볼의 합집합. fetch_macro_series가 이름으로 심볼을 찾을 때 쓴다.
MACRO_SERIES: dict[str, str] = {
    name: symbol
    for series in MACRO_SERIES_BY_MARKET.values()
    for name, symbol in series.items()
}
```

`update_macro`의 시리즈 결정 부분을 교체한다. 현재:

```python
    names = list(series) if series is not None else list(MACRO_SERIES)
```

를 다음으로:

```python
    if series is not None:
        names = list(series)
    else:
        try:
            names = list(MACRO_SERIES_BY_MARKET[store.market])
        except KeyError as exc:
            available = ", ".join(sorted(MACRO_SERIES_BY_MARKET))
            raise ValueError(
                f"No macro series defined for market {store.market}. Available: {available}"
            ) from exc
```

`update_macro`의 docstring도 갱신한다:

```python
    """Incrementally collect macro series into the panel store.

    Without an explicit `series`, collects the ones defined for the store's
    own market — a US panel must not be filled with Korean indices.
    """
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_macro.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```powershell
git add src/tradingbot/data/macro.py tests/test_data_macro.py
git commit -m "US(part): Scope macro series to their own market"
```

---

### Task 2: 수집기 시장 가드

**Files:**
- Modify: `src/tradingbot/data/pipeline.py`
- Test: `tests/test_data_pipeline.py`

**Interfaces:**
- Consumes: `SourceResult`, `STATUS_SKIPPED` (기존)
- Produces:
  - `pipeline.COLLECTOR_MARKETS: dict[str, tuple[str, ...]]` — 수집기명 → 적용 시장
  - `run_pipeline`이 부적용 수집기를 호출하지 않고 `skipped`로 기록

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_pipeline.py` 끝에 추가:

```python
class TestMarketGuards:
    def test_kr_only_sources_are_skipped_on_us(self, config):
        called: list[str] = []

        def recording(name):
            def collect(**kwargs):
                called.append(name)
                return 1

            return collect

        result = run_pipeline(
            config,
            market="US",
            symbols=["SPY"],
            collectors={
                "prices": recording("prices"),
                "flows": recording("flows"),
                "valuation": recording("valuation"),
                "fundamentals": recording("fundamentals"),
            },
        )
        by_name = {r.name: r for r in result.results}
        assert by_name["prices"].status == "ok"
        for kr_only in ["flows", "valuation", "fundamentals"]:
            assert by_name[kr_only].status == "skipped"
        # The guard must prevent the call, not just relabel the result —
        # otherwise US tickers still get sent to KRX APIs.
        assert called == ["prices"]

    def test_skipping_is_not_a_failure(self, config):
        result = run_pipeline(
            config,
            market="US",
            symbols=["SPY"],
            collectors={"flows": ok_collector("flows")},
        )
        assert result.ok

    def test_skip_reason_names_the_market(self, config):
        result = run_pipeline(
            config, market="US", symbols=["SPY"], collectors={"flows": ok_collector("flows")}
        )
        message = result.results[0].message
        assert "KR" in message and "US" in message

    def test_skip_reaches_the_run_log(self, config, tmp_path):
        run_pipeline(
            config, market="US", symbols=["SPY"], collectors={"flows": ok_collector("flows")}
        )
        log = json.loads(next((tmp_path / "log").glob("*.json")).read_text(encoding="utf-8"))
        assert log["results"][0]["status"] == "skipped"

    def test_kr_run_is_unaffected(self, config):
        result = run_pipeline(
            config, market="KR", symbols=["005930"], collectors={"flows": ok_collector("flows", 7)}
        )
        assert result.results[0].status == "ok"
        assert result.results[0].rows == 7

    def test_unknown_collector_name_is_not_guarded(self, config):
        # Injected test collectors and any future source default to running.
        result = run_pipeline(
            config, market="US", symbols=["SPY"], collectors={"custom": ok_collector("custom")}
        )
        assert result.results[0].status == "ok"
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_pipeline.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — KR 전용 수집기가 US에서도 `ok`로 실행됨

- [ ] **Step 3: 구현**

`src/tradingbot/data/pipeline.py`의 상태 상수 아래에 추가:

```python
# 수집기명 -> 적용 가능한 시장. 여기 없는 이름(주입된 테스트 수집기, 향후 신규
# 소스)은 가드 없이 실행된다.
#
# flows/valuation은 pykrx(KRX 회원 데이터), fundamentals는 DART로 모두 한국
# 전용이다. 가드가 없으면 --market US 실행이 미국 티커를 KRX API에 보내고 그
# 결과가 data/processed/{dataset}/US/에 기록된다.
COLLECTOR_MARKETS: dict[str, tuple[str, ...]] = {
    "prices": ("KR", "US"),
    "macro": ("KR", "US"),
    "flows": ("KR",),
    "valuation": ("KR",),
    "fundamentals": ("KR",),
}
```

`run_pipeline`의 수집 루프 시작부에 가드를 넣는다. 현재:

```python
    for name, collector in active.items():
        try:
            rows = with_retry(
```

를 다음으로:

```python
    for name, collector in active.items():
        supported = COLLECTOR_MARKETS.get(name)
        if supported is not None and market.upper() not in supported:
            reason = (
                f"{name} is {'/'.join(supported)}-only and does not apply to {market.upper()}"
            )
            results.append(SourceResult(name, STATUS_SKIPPED, 0, reason))
            LOGGER.info("Pipeline source %s skipped: %s", name, reason)
            continue
        try:
            rows = with_retry(
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_pipeline.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/data/pipeline.py tests/test_data_pipeline.py
git commit -m "US(part): Guard Korea-only collectors from non-KR markets"
```

---

### Task 3: 전략 팩터 목록 명시

**Files:**
- Modify: `src/tradingbot/strategies/theme_multifactor.py`
- Test: `tests/test_theme_multifactor_targets.py`

**Interfaces:**
- Consumes: `get_factor` (기존)
- Produces:
  - `ThemeMultifactorStrategy.default_params["factors"] = None`
  - `factor_weights` property가 `factors` 목록으로 제한되고, 가중치 없는 이름에 ValueError

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_theme_multifactor_targets.py` 끝에 추가:

```python
class TestExplicitFactorSelection:
    MULTI_TOML = """
[factor_weights]
momentum_3m = 0.5
momentum_6m = 0.5

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

    @pytest.fixture
    def multi_config(self, tmp_path):
        path = tmp_path / "multi.toml"
        path.write_text(self.MULTI_TOML, encoding="utf-8")
        return path

    def test_default_uses_every_weighted_factor(self, multi_config):
        strategy = ThemeMultifactorStrategy(research_config=str(multi_config))
        assert set(strategy.factor_weights) == {"momentum_3m", "momentum_6m"}

    def test_explicit_list_restricts_the_set(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_3m"]
        )
        # A US run declares momentum-only instead of silently degrading when
        # the flow and value panels come back all-NaN.
        assert set(strategy.factor_weights) == {"momentum_3m"}

    def test_weights_still_come_from_the_config(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_6m"]
        )
        assert strategy.factor_weights["momentum_6m"] == pytest.approx(0.5)

    def test_factor_without_a_weight_raises(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_12m"]
        )
        # momentum_12m is a real registered factor but carries no weight here;
        # running it at an implicit zero would be the silent trap Phase 3 closed.
        with pytest.raises(ValueError, match="momentum_12m"):
            strategy.factor_weights

    def test_unregistered_factor_name_still_raises(self, tmp_path):
        path = tmp_path / "typo.toml"
        path.write_text(
            "[factor_weights]\nmomentum_3m_typo = 1.0\n"
            "[risk_limits]\nmax_position_weight = 0.4\nmin_cash_weight = 0.02\n",
            encoding="utf-8",
        )
        strategy = ThemeMultifactorStrategy(
            research_config=str(path), factors=["momentum_3m_typo"]
        )
        with pytest.raises(ValueError, match="momentum_3m_typo"):
            strategy.factor_weights

    def test_unused_config_weights_are_not_an_error(self, multi_config):
        # One config file must be able to carry both markets' weights.
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_3m"]
        )
        assert "momentum_6m" not in strategy.factor_weights

    def test_selected_factors_drive_the_targets(self, store, multi_config):
        write_prices(store, "WIN", 100.0, 200.0)
        write_prices(store, "LOSE", 100.0, 80.0)
        targets = ThemeMultifactorStrategy(
            research_config=str(multi_config),
            factors=["momentum_3m"],
            top_n=1,
            weighting="equal",
        ).generate_targets(AS_OF, ["WIN", "LOSE"], store)
        assert set(targets) == {"WIN"}
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `factors`를 넘겨도 무시되어 두 팩터가 모두 사용됨

- [ ] **Step 3: 구현**

`src/tradingbot/strategies/theme_multifactor.py`의 `default_params`에 추가 (`"min_factors": 1,` 다음 줄):

```python
        # 사용할 팩터를 명시적으로 제한한다. None이면 [factor_weights]의 모든
        # 키를 쓴다(현행). 미국처럼 수급·가치 패널이 없는 시장은 여기에
        # 모멘텀만 적어, 조용히 퇴화하는 대신 무엇을 돌리는지 선언한다.
        "factors": None,
```

`factor_weights` property를 다음으로 교체:

```python
    @property
    def factor_weights(self) -> dict[str, float]:
        """[factor_weights] keys drive which factors run; typos fail loudly.

        `factors` narrows that set without moving the weights: one config file
        can carry both markets' weights while each market declares the subset
        it actually has data for.
        """
        if self._factor_weights is None:
            raw = self.research.get("factor_weights", {})
            if not raw:
                raise ValueError("research config has no [factor_weights] section")
            selected = self.params.get("factors")
            if selected:
                missing = [name for name in selected if name not in raw]
                if missing:
                    available = ", ".join(sorted(raw))
                    raise ValueError(
                        f"factors {missing} have no weight in [factor_weights]. "
                        f"Available: {available}"
                    )
                raw = {name: raw[name] for name in selected}
            for factor_name in raw:
                get_factor(factor_name)  # raises ValueError on unknown names
            self._factor_weights = {name: float(value) for name, value in raw.items()}
        return self._factor_weights
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/strategies/theme_multifactor.py tests/test_theme_multifactor_targets.py
git commit -m "US(part): Let a strategy declare which factors it runs"
```

---

### Task 4: 절대 모멘텀 필터

**Files:**
- Modify: `src/tradingbot/strategies/theme_multifactor.py`
- Test: `tests/test_theme_multifactor_targets.py`

**Interfaces:**
- Consumes: `data_store.price_history` (기존), `select_top` (기존)
- Produces:
  - `ThemeMultifactorStrategy.default_params["abs_momentum_ma_days"] = 0`
  - `ThemeMultifactorStrategy._apply_absolute_momentum(dt, scores, data_store) -> pd.Series`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_theme_multifactor_targets.py` 끝에 추가:

```python
class TestAbsoluteMomentumFilter:
    def write_falling_then_flat(self, store, symbol: str) -> None:
        """A name well below its own moving average at AS_OF."""
        closes = [200.0] * (HISTORY_DAYS - 10) + [100.0] * 10
        index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
        store.cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )

    def test_disabled_by_default(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["RISER", "FALLER"], store
        )
        # Default 0 must preserve the Korean strategy's recorded behavior.
        assert set(targets) == {"RISER", "FALLER"}

    def test_excludes_a_name_below_its_moving_average(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60
        ).generate_targets(AS_OF, ["RISER", "FALLER"], store)
        # Relative momentum alone would still buy the least-bad asset.
        assert set(targets) == {"RISER"}

    def test_all_names_filtered_skips_the_rebalance(self, store, research_config):
        self.write_falling_then_flat(store, "FALLER1")
        self.write_falling_then_flat(store, "FALLER2")
        assert (
            make_strategy(research_config, abs_momentum_ma_days=60).generate_targets(
                AS_OF, ["FALLER1", "FALLER2"], store
            )
            == {}
        )

    def test_short_history_is_excluded_not_admitted(self, store, research_config):
        # Long enough to be judged by a 200-day filter window.
        long_closes = list(np.linspace(100.0, 200.0, 250))
        long_index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=250)
        store.cache.write(
            "KR",
            "RISER",
            pd.DataFrame(
                {"open": long_closes, "high": long_closes, "low": long_closes,
                 "close": long_closes, "volume": [1000.0] * 250},
                index=long_index,
            ),
        )
        # Scoreable by momentum_3m (needs 64 closes) and the STRONGER mover,
        # so without the filter it would certainly be picked — but it has too
        # little history to judge, and admitting it would let a new listing
        # bypass the filter entirely.
        short_closes = list(np.linspace(100.0, 300.0, 70))
        short_index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=70)
        store.cache.write(
            "KR",
            "NEW",
            pd.DataFrame(
                {"open": short_closes, "high": short_closes, "low": short_closes,
                 "close": short_closes, "volume": [1000.0] * 70},
                index=short_index,
            ),
        )
        targets = make_strategy(
            research_config, abs_momentum_ma_days=200, top_n=2
        ).generate_targets(AS_OF, ["RISER", "NEW"], store)
        assert set(targets) == {"RISER"}

    def test_fewer_survivors_than_top_n_holds_what_remains(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60, top_n=2
        ).generate_targets(AS_OF, ["RISER", "FALLER"], store)
        assert set(targets) == {"RISER"}

    def test_unscoreable_names_stay_excluded(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        # GHOST has no data at all, so the factor already scored it NaN; the
        # filter must not resurrect it. (The filter's own missing-data branch
        # is unreachable in practice — a symbol with a score necessarily has
        # price history — so it stays as defensive code, matching the
        # convention in factors/momentum.py.)
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60, top_n=2
        ).generate_targets(AS_OF, ["RISER", "GHOST"], store)
        assert set(targets) == {"RISER"}
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `abs_momentum_ma_days`가 무시되어 하락 자산도 선정됨

- [ ] **Step 3: 구현**

`default_params`에 추가 (`"factors": None,` 다음 줄):

```python
        # 종가가 자기 N일 이동평균 아래인 자산을 선정에서 제외한다. 0이면 비활성.
        # 상대 모멘텀만으로는 전 자산 하락장에서 "가장 덜 나쁜 것"을 강제로 사게
        # 된다 — 채권·원자재가 섞인 ETF 유니버스에서 특히 위험하다.
        "abs_momentum_ma_days": 0,
```

`generate_targets`에서 `select_top` 호출 직전에 필터를 끼운다. 현재:

```python
        selected = select_top(combined, int(self.params["top_n"]))
```

를 다음으로:

```python
        combined = self._apply_absolute_momentum(dt, combined, data_store)
        selected = select_top(combined, int(self.params["top_n"]))
```

`_is_price_data_stale` 메서드 바로 앞에 새 메서드를 추가:

```python
    def _apply_absolute_momentum(self, dt: date, scores: pd.Series, data_store) -> pd.Series:
        """NaN out names trading below their own moving average.

        Relative momentum ranks names against each other, so in a broad
        selloff it still buys the least-bad asset. This is the per-asset
        floor: a name has to be in its own uptrend to be eligible at all.

        Exclusion is expressed as NaN so `select_top` drops it the same way
        it drops an unscoreable name. A name with less history than the
        window is excluded too — admitting it would let a new listing bypass
        the filter entirely.
        """
        ma_days = int(self.params["abs_momentum_ma_days"])
        if ma_days <= 0:
            return scores

        filtered = scores.copy()
        for symbol in scores.index:
            if pd.isna(scores.loc[symbol]):
                continue
            try:
                history = data_store.price_history(symbol, dt, ma_days)
            except (FileNotFoundError, KeyError):
                filtered.loc[symbol] = float("nan")
                continue
            closes = history["close"].dropna()
            if len(closes) < ma_days or float(closes.iloc[-1]) <= float(closes.mean()):
                filtered.loc[symbol] = float("nan")
        return filtered
```

`pd`가 이미 import되어 있는지 확인하고, 없으면 파일 상단에 `import pandas as pd`를 추가한다.

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_targets.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/strategies/theme_multifactor.py tests/test_theme_multifactor_targets.py
git commit -m "US(part): Add an off-by-default absolute momentum filter"
```

---

### Task 5: 미국 설정·실데이터 검증·문서

**Files:**
- Modify: `config/themes.toml`
- Create: `config/us_etf_rotation.toml`
- Create: `config/us_etf_benchmark.toml`
- Create: `docs/us_etf_rotation_review.md`
- Modify: `README.md`, `docs/architecture.md`
- Test: `tests/test_data_universe.py`

**Interfaces:**
- Consumes: Task 1~4 전부

- [ ] **Step 1: 미국 ETF 가격 수집**

11개 ETF를 2007년부터 받는다 (네트워크 사용):

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01
```

각 ETF의 **실제 최초 날짜**를 확인한다 (테마 편입일에 쓴다):

```powershell
.\.venv\Scripts\python.exe -c @'
import pandas as pd
from pathlib import Path
for path in sorted(Path("data/cache/US").glob("*.parquet")):
    frame = pd.read_parquet(path)
    print(f"{path.stem:6} {frame.index.min().date()} .. {frame.index.max().date()}  rows={len(frame)}")
'@
```

- [ ] **Step 2: 테마 정의 추가**

`config/themes.toml` 끝에 추가한다. **`from` 값은 Step 1에서 확인한 각 ETF의 최초 거래일로 교체한다** — 상장 전 날짜에 그 ETF가 유니버스에 있으면 생존자 편향이 생긴다. 아래는 알려진 상장 시기를 반영한 초기값이며, 실측이 더 늦으면 실측을 쓴다:

```toml
[themes.us_asset_rotation]
name = "미국 자산배분 ETF 로테이션"
market = "US"
members = [
    { symbol = "SPY", from = "2007-01-01" },  # 미국 대형주
    { symbol = "QQQ", from = "2007-01-01" },  # 미국 기술주
    { symbol = "IWM", from = "2007-01-01" },  # 미국 소형주
    { symbol = "EFA", from = "2007-01-01" },  # 선진국 주식
    { symbol = "EEM", from = "2007-01-01" },  # 신흥국 주식
    { symbol = "TLT", from = "2007-01-01" },  # 미국 장기국채
    { symbol = "IEF", from = "2007-01-01" },  # 미국 중기국채
    { symbol = "LQD", from = "2007-01-01" },  # 투자등급 회사채
    { symbol = "GLD", from = "2007-01-01" },  # 금
    { symbol = "DBC", from = "2007-01-01" },  # 원자재
    { symbol = "VNQ", from = "2007-01-01" },  # 미국 리츠
]
```

`tests/test_data_universe.py`의 `TestLoadThemes`에 추가:

```python
    def test_us_theme_is_defined(self):
        themes = load_themes()
        us_themes = [theme for theme in themes.values() if theme.market == "US"]
        assert us_themes, "no US theme defined"
        for theme in us_themes:
            assert theme.members
```

- [ ] **Step 3: 미국 전략 설정 작성**

`config/us_etf_rotation.toml`을 만든다. `config/default.toml`을 복사한 뒤 아래 두 섹션을 교체한다.

`[pipeline]`의 `symbols`를 미국 ETF로:

```toml
symbols = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "GLD", "DBC", "VNQ"]
```

`[strategies.theme_multifactor]` 섹션 전체를 교체:

```toml
[strategies.theme_multifactor]
theme = "us_asset_rotation"
market = "US"
rebalance = "monthly"
top_n = 3
weighting = "inverse_volatility"
volatility_days = 60
bear_exposure = 0.5
# 미국에는 수급·가치 패널이 없다(수급은 대응 데이터 자체가 없고, ETF에는
# PER/PBR이 없다). 조용히 퇴화하는 대신 사용할 팩터를 명시한다.
factors = ["momentum_6m", "momentum_12m_ex1m"]
# 채권·금·원자재가 섞인 유니버스에서는 자산별 하락 추세 배제가 필수다.
abs_momentum_ma_days = 200
regime_series = "sp500"
```

파일 맨 위에 목적을 적는다:

```toml
# 미국 자산배분 ETF 로테이션 설정.
# 설계: docs/superpowers/specs/2026-07-26-us-market-etf-rotation-design.md
#
# 사용법 (한 줄):
#   python -m tradingbot --config config/us_etf_rotation.toml backtest
#   --strategy theme_multifactor --market US
#   --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01
```

- [ ] **Step 4: 벤치마크 설정 작성**

`config/us_etf_benchmark.toml`은 `us_etf_rotation.toml`의 복사본에서 `[strategies.theme_multifactor]`만 바꾼다 — 유니버스 전체를 동일비중으로 들고, 선별·국면 조절·절대 모멘텀을 모두 끈다:

```toml
[strategies.theme_multifactor]
theme = "us_asset_rotation"
market = "US"
rebalance = "monthly"
top_n = 11
weighting = "equal"
volatility_days = 60
bear_exposure = 1.0
factors = ["momentum_6m", "momentum_12m_ex1m"]
abs_momentum_ma_days = 0
regime_series = "sp500"
```

맨 위 주석:

```toml
# 미국 ETF 동일비중 벤치마크 (설계 §9의 2차 벤치마크).
# 같은 엔진·같은 비용으로 돌리되 선별·국면 조절·절대 모멘텀만 끈다.
# 전략이 이걸 못 이기면 유니버스를 그냥 다 사는 것보다 나을 게 없다.
```

- [ ] **Step 5: 전체 테스트**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 6: 파이프라인 실데이터 검증**

미국 거시를 수집하고 시장 가드가 동작하는지 확인한다 (KRX/DART 자격증명은 User 스코프에 설정되어 있으나 이 실행엔 불필요하다):

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml data pipeline --market US
```

Expected: `prices`와 `macro`는 `성공`, `flows`·`valuation`·`fundamentals`는 **`생략`이고 사유에 "KR-only"가 표시**되며 종료코드 0. macro 수집 행이 0보다 크다.

한국 파이프라인이 그대로 도는지도 확인한다 (자격증명 로드 후):

```powershell
$env:KRX_ID = [System.Environment]::GetEnvironmentVariable("KRX_ID", "User")
$env:KRX_PW = [System.Environment]::GetEnvironmentVariable("KRX_PW", "User")
$env:DART_API_KEY = [System.Environment]::GetEnvironmentVariable("DART_API_KEY", "User")
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR
```

Expected: 5개 소스 모두 `성공` (증분이라 행 수는 0일 수 있다).

- [ ] **Step 7: 팩터 검증 — 전략보다 먼저**

미국 유니버스에서 모멘텀 팩터가 게이트를 통과하는지 본다:

```powershell
.\.venv\Scripts\python.exe -m tradingbot research report --factors momentum_6m momentum_12m_ex1m --start 2010-01-01 --end 2021-12-31
```

Expected: 두 팩터의 IC 평균·IR·분위수 단조성·Walk-forward 승률과 게이트 PASS/FAIL이 표로 출력된다. **IC 값 자체는 검증 대상이 아니다** — 나온 대로 보고에 기록한다. FAIL이 나오면 그것도 결과다.

- [ ] **Step 8: 백테스트와 벤치마크 비교**

```powershell
# 전략
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml backtest --strategy theme_multifactor --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01

# 2차 벤치마크 (유니버스 동일비중)
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_benchmark.toml backtest --strategy theme_multifactor --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01
```

1차 벤치마크(SPY 매수 후 보유)는 전략 실행이 필요 없다. 캐시에서 직접 계산한다:

```powershell
.\.venv\Scripts\python.exe -c @'
import pandas as pd
frame = pd.read_parquet("data/cache/US/SPY.parquet")
window = frame.loc["2007-01-01":]
first, last = window["close"].iloc[0], window["close"].iloc[-1]
peak = window["close"].cummax()
mdd = float(((window["close"] - peak) / peak).min())
print(f"SPY buy-and-hold  {window.index.min().date()}..{window.index.max().date()}")
print(f"  total return {last / first - 1:.2%}   MDD {mdd:.2%}")
'@
```

세 결과의 총수익률·CAGR·MDD·Sharpe를 리포트 HTML에서 뽑아 나란히 기록한다. 지표는 리포트의 표에서 읽는다:

```powershell
.\.venv\Scripts\python.exe -c @'
import re, sys
from pathlib import Path
for report in sorted(Path("reports").glob("*theme_multifactor_US/report.html"))[-2:]:
    text = report.read_text(encoding="utf-8")
    cells = [re.sub(r"<[^>]*>", "", c) for c in re.findall(r"<t[hd][^>]*>[^<]{1,40}</t[hd]>", text)[:16]]
    print(report.parent.name)
    print("  " + "  ".join(f"{cells[i]}={cells[i+1]}" for i in range(0, 16, 2)))
'@
```

- [ ] **Step 9: 판정 문서 작성**

`docs/us_etf_rotation_review.md`를 만든다. 한국 판정문(`docs/theme_multifactor_promotion_review.md`)과 같은 구조로:

- 실행 조건 (기간, 유니버스, 팩터, 절대 모멘텀·국면 필터 설정)
- 결과 표: 전략 / SPY 매수보유 / ETF 동일비중의 총수익률·CAGR·MDD·Sharpe·거래수
- `config/research.toml`의 `[promotion]` 기준 대조표와 통과/미달 판정
- 재현 명령
- 해석: **하락장(2008·2020·2022)에서 절대 모멘텀과 국면 필터가 실제로 낙폭을 줄였는지**가 핵심 관전 포인트다. 한국 표본이 답하지 못했던 질문이다.
- 다음 할 일

**수익률이 좋든 나쁘든 나온 대로 적는다.** 기준 미달이면 승격하지 않는다고 명시한다.

- [ ] **Step 10: 문서 갱신**

`README.md`의 확장 목록에 추가:

```markdown
- 미국 시장 지원(`config/us_etf_rotation.toml`): 수집기 시장 가드,
  시장별 거시 시리즈, 전략의 명시적 팩터 목록과 절대 모멘텀 필터.
  11개 ETF 자산배분 로테이션을 2007년부터 백테스트할 수 있습니다 —
  판정은 [docs/us_etf_rotation_review.md](docs/us_etf_rotation_review.md).
```

`README.md`의 「데이터 소스별 필요 자격증명」 표 아래에 한 줄:

```markdown
미국 시장은 가격·거시만 수집합니다. 수급·밸류에이션·재무는 한국 전용이라
`--market US` 실행에서 **생략**으로 보고되며, 이는 실패가 아닙니다.
```

`docs/architecture.md` §7 표에 행 추가:

```markdown
| 시장별 거시 시리즈 / 수집기 시장 가드 | `src/tradingbot/data/macro.py`, `data/pipeline.py` |
```

- [ ] **Step 11: 커밋**

```powershell
git add config/themes.toml config/us_etf_rotation.toml config/us_etf_benchmark.toml docs/us_etf_rotation_review.md tests/test_data_universe.py README.md docs/architecture.md
git commit -m "US: Add ETF rotation configs and record the US backtest verdict"
```

---

## 완료 기준 (스펙 §9)

- [ ] `data pipeline --market US`가 가격·거시를 수집하고 KR 전용 수집기 3종을 사유와 함께 `생략`으로 보고하며 종료코드 0.
- [ ] 미국 패널에 KOSPI가 섞이지 않는다.
- [ ] `research report`로 미국 모멘텀 팩터의 IC·분위수·Walk-forward 결과가 나온다.
- [ ] 2007년부터의 백테스트가 완주하고, 두 벤치마크와 비교한 판정이 문서로 남는다.
- [ ] 한국 전략 백테스트 결과가 이번 변경으로 바뀌지 않는다 (Task 5 Step 6에서 KR 파이프라인 정상 동작 확인, 전체 테스트 통과).
- [ ] 전체 테스트 통과, 기존 회귀 없음.

## 알려진 한계 (범위 밖)

- **SEC EDGAR 가치 팩터**: 다음 사이클. ETF에는 PER/PBR이 없어 이번 대상에는 불필요하고, 미국 개별 종목으로 확장할 때 필요해진다.
- **미국 수급 팩터**: 대응 데이터가 없다(공매도 잔고 격주, 13F 분기+45일 지연). 대체 지표 설계는 별도 작업.
- **미국 모의투자·실전**: M15 이후. 이번 작업은 백테스트 검증까지다.
- **거래비용 가정**: 미국 수수료 모델은 기존 `broker/fees.py`를 그대로 쓴다. ETF 로테이션의 실제 스프레드·시장충격은 M13(체결 모델 현실화) 대상이다.
