# Phase 2: 자동 데이터 수집·전처리 파이프라인 (M7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수급·밸류에이션·재무·거시 데이터를 Point-in-Time 스키마로 자동 수집·검증·저장하고, Windows 작업 스케줄러가 하루 1회 실행하는 `tradingbot data pipeline` 배치를 만든다.

**Architecture:** 모든 신규 데이터는 `PanelStore`(연도 분할 Parquet 패널)에 `available_at` 메타컬럼과 함께 저장되고, 읽을 때 `available_at <= as_of` 필터가 강제되어 미래 데이터 누수가 구조적으로 차단된다. 수집기 4종(macro/flows+valuation/fundamentals)은 각각 독립 모듈이며 `pipeline.py`가 재시도·실패 격리와 함께 순차 실행한다.

**Tech Stack:** Python 3.13, pandas, pyarrow, pykrx(신규), requests(기존, DART용), FinanceDataReader(기존, 거시용), pytest.

**스펙:** `docs/superpowers/specs/2026-07-19-kr-theme-multifactor-design.md` §4 (Phase 2)

## Global Constraints

- **신규 의존성은 `pykrx`만** 추가한다 (`pyproject.toml` + `uv.lock`). DART는 기존 `requests`, 거시는 기존 `FinanceDataReader`를 쓴다.
- **모든 processed 레코드는 PIT 메타컬럼 필수**: `source`, `available_at`, `ingested_at`, `data_version`. 키 컬럼은 `date`, `symbol`.
- **`available_at` 이후에만 사용 가능**: `PanelStore.read(as_of=...)`가 `available_at <= as_of` 필터를 강제한다. 이 필터를 우회하는 조회 경로를 만들지 않는다.
- **DART API 키는 환경변수 `DART_API_KEY`**로만 읽는다. 키·계좌정보를 저장소에 커밋하지 않는다. 키가 없으면 재무 수집만 `skipped`로 건너뛰고 파이프라인 전체는 계속한다.
- **수집 실패를 조용히 무시하지 않는다**: 실패는 로그와 결과 JSON에 남기고, 하나라도 실패하면 CLI는 종료코드 1을 반환한다 (나머지 소스는 계속 수집).
- **테스트에서 네트워크 접근 금지**: 모든 수집기 테스트는 mock 또는 고정 fixture를 사용한다. 실제 네트워크 호출은 각 태스크의 "실데이터 스모크" 단계에서 수동으로만 실행한다.
- **백테스트/전략 루프 안에서 네트워크 호출 금지** — 수집은 배치 경로에서만 일어난다.
- 파일 쓰기는 `encoding="utf-8"` 명시. bat 파일은 `REM` 뒤에 한글 금지, 한글 `echo`는 `chcp 65001` 이후에만.
- 기존 테스트·기존 CLI 명령 동작을 바꾸지 않는다.
- 커밋 메시지 접두사: 중간 태스크 `M7(part):`, 마지막 태스크 `M7:`.
- **테스트 실행 명령** (PowerShell, 저장소 루트): 이 PC는 pytest 기본 임시 디렉터리 생성이 실패하므로 반드시 `--basetemp`를 저장소 밖으로 지정한다.
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

---

### Task 1: PIT 패널 저장소 (PanelStore)

**Files:**
- Create: `src/tradingbot/data/panel.py`
- Test: `tests/test_data_panel.py`

**Interfaces:**
- Consumes: `get_calendar(market)` (기존 `engine/calendar.py`)
- Produces (이후 모든 태스크가 사용):
  - `panel.PANEL_KEY_COLUMNS = ["date", "symbol"]`, `panel.PANEL_META_COLUMNS = ["source", "available_at", "ingested_at", "data_version"]`
  - `panel.next_trading_day_availability(dates: pd.Series, market: str) -> pd.Series` — 각 날짜의 **다음 거래일**(그 데이터를 쓸 수 있게 되는 첫 날)
  - `panel.attach_metadata(frame, *, source, available_at, data_version, ingested_at=None) -> pd.DataFrame`
  - `panel.PanelStore(root: Path, dataset: str, market: str)` with:
    - `.path(year) -> Path` (`root/dataset/MARKET/{year}.parquet`)
    - `.years() -> list[int]`
    - `.append(frame) -> int` (연도별 분할 저장, `(date, symbol)` 중복은 최신 유지)
    - `.read(*, as_of=None, start=None, end=None, symbols=None) -> pd.DataFrame`
    - `.last_date(symbol=None) -> date | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_panel.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from tradingbot.data.panel import (
    PANEL_META_COLUMNS,
    PanelStore,
    attach_metadata,
    next_trading_day_availability,
)


def make_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), "symbol": s, "value": v} for d, s, v in rows]
    )


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "flows", "KR")


def tagged(frame: pd.DataFrame, available_at: pd.Series | str) -> pd.DataFrame:
    return attach_metadata(frame, source="test", available_at=available_at, data_version="1")


class TestAttachMetadata:
    def test_adds_all_meta_columns(self):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        for column in PANEL_META_COLUMNS:
            assert column in frame.columns
        assert frame.loc[0, "source"] == "test"
        assert frame.loc[0, "available_at"] == pd.Timestamp("2024-01-03")
        assert frame.loc[0, "data_version"] == "1"

    def test_ingested_at_is_timezone_aware_utc(self):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        ingested = frame.loc[0, "ingested_at"]
        assert ingested.tzinfo is not None

    def test_explicit_ingested_at_is_preserved(self):
        moment = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
        frame = attach_metadata(
            make_frame([("2024-01-02", "005930", 1.0)]),
            source="test",
            available_at="2024-01-03",
            data_version="1",
            ingested_at=moment,
        )
        assert frame.loc[0, "ingested_at"] == pd.Timestamp(moment)

    def test_missing_key_column_raises(self):
        with pytest.raises(ValueError, match="date"):
            attach_metadata(
                pd.DataFrame({"symbol": ["005930"], "value": [1.0]}),
                source="test",
                available_at="2024-01-03",
                data_version="1",
            )


class TestNextTradingDayAvailability:
    def test_weekday_maps_to_next_weekday(self):
        dates = pd.Series([pd.Timestamp("2024-01-02")])  # Tue
        assert next_trading_day_availability(dates, "KR").iloc[0] == pd.Timestamp("2024-01-03")

    def test_friday_maps_past_the_weekend(self):
        dates = pd.Series([pd.Timestamp("2024-01-05")])  # Fri
        assert next_trading_day_availability(dates, "KR").iloc[0] == pd.Timestamp("2024-01-08")

    def test_empty_series_returns_empty(self):
        result = next_trading_day_availability(pd.Series([], dtype="datetime64[ns]"), "KR")
        assert result.empty


class TestPanelStoreRoundTrip:
    def test_append_then_read(self, store):
        store.append(tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03"))
        result = store.read()
        assert len(result) == 1
        assert result.loc[0, "symbol"] == "005930"
        assert result.loc[0, "value"] == 1.0

    def test_partitions_by_year(self, store, tmp_path):
        store.append(
            tagged(
                make_frame([("2023-12-28", "005930", 1.0), ("2024-01-02", "005930", 2.0)]),
                pd.Series([pd.Timestamp("2023-12-29"), pd.Timestamp("2024-01-03")]),
            )
        )
        assert (tmp_path / "flows" / "KR" / "2023.parquet").exists()
        assert (tmp_path / "flows" / "KR" / "2024.parquet").exists()
        assert store.years() == [2023, 2024]

    def test_append_replaces_same_key(self, store):
        store.append(tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03"))
        store.append(tagged(make_frame([("2024-01-02", "005930", 9.0)]), "2024-01-03"))
        result = store.read()
        assert len(result) == 1
        assert result.loc[0, "value"] == 9.0

    def test_read_missing_dataset_is_empty_not_error(self, tmp_path):
        empty_store = PanelStore(tmp_path, "nothing", "KR")
        assert empty_store.read().empty
        assert empty_store.years() == []
        assert empty_store.last_date() is None


class TestPanelStorePointInTime:
    @pytest.fixture
    def filled(self, store):
        store.append(
            tagged(
                make_frame([("2024-01-02", "005930", 1.0), ("2024-01-03", "005930", 2.0)]),
                pd.Series([pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")]),
            )
        )
        return store

    def test_as_of_hides_not_yet_available_rows(self, filled):
        result = filled.read(as_of=date(2024, 1, 3))
        assert len(result) == 1
        assert result.loc[0, "value"] == 1.0

    def test_as_of_on_availability_date_includes_row(self, filled):
        assert len(filled.read(as_of=date(2024, 1, 4))) == 2

    def test_as_of_before_everything_is_empty(self, filled):
        assert filled.read(as_of=date(2024, 1, 1)).empty

    def test_read_without_as_of_returns_everything(self, filled):
        assert len(filled.read()) == 2


class TestPanelStoreFilters:
    @pytest.fixture
    def filled(self, store):
        store.append(
            tagged(
                make_frame(
                    [
                        ("2024-01-02", "005930", 1.0),
                        ("2024-01-02", "000660", 2.0),
                        ("2024-02-01", "005930", 3.0),
                    ]
                ),
                "2024-03-01",
            )
        )
        return store

    def test_symbol_filter_is_case_insensitive(self, filled):
        assert len(filled.read(symbols=["005930"])) == 2

    def test_date_range_filter(self, filled):
        result = filled.read(start=date(2024, 1, 1), end=date(2024, 1, 31))
        assert len(result) == 2

    def test_last_date_overall_and_per_symbol(self, filled):
        assert filled.last_date() == date(2024, 2, 1)
        assert filled.last_date("000660") == date(2024, 1, 2)
        assert filled.last_date("999999") is None

    def test_read_is_sorted_by_date_then_symbol(self, filled):
        result = filled.read()
        assert list(result["date"]) == sorted(result["date"])
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_panel.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.panel'`

- [ ] **Step 3: 구현**

`src/tradingbot/data/panel.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

from tradingbot.engine.calendar import get_calendar

PANEL_KEY_COLUMNS = ["date", "symbol"]
PANEL_META_COLUMNS = ["source", "available_at", "ingested_at", "data_version"]


def next_trading_day_availability(dates: pd.Series, market: str) -> pd.Series:
    """First date on which data observed on `dates` may be used.

    Daily data for trading day T is only known after T's close, so the
    earliest a backtest may act on it is the next trading day."""
    if dates.empty:
        return pd.Series([], dtype="datetime64[ns]")
    calendar = get_calendar(market)
    unique = pd.to_datetime(dates).dt.normalize().drop_duplicates()
    mapping = {value: pd.Timestamp(calendar.next_trading_day(value.date())) for value in unique}
    return pd.to_datetime(dates).dt.normalize().map(mapping)


def attach_metadata(
    frame: pd.DataFrame,
    *,
    source: str,
    available_at: pd.Series | str | date,
    data_version: str,
    ingested_at: datetime | None = None,
) -> pd.DataFrame:
    """Add the point-in-time metadata columns every panel record must carry."""
    missing = [column for column in PANEL_KEY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Panel frame is missing key columns: {missing}")

    tagged = frame.copy()
    tagged["date"] = pd.to_datetime(tagged["date"]).dt.normalize()
    tagged["symbol"] = tagged["symbol"].astype(str).str.upper()
    tagged["source"] = source
    if isinstance(available_at, pd.Series):
        tagged["available_at"] = pd.to_datetime(available_at.to_numpy()).normalize()
    else:
        tagged["available_at"] = pd.Timestamp(available_at).normalize()
    tagged["ingested_at"] = pd.Timestamp(ingested_at or datetime.now(timezone.utc))
    tagged["data_version"] = str(data_version)
    return tagged


class PanelStore:
    """Year-partitioned Parquet panel with a point-in-time read barrier.

    Layout: `root/dataset/MARKET/{year}.parquet`, one row per (date, symbol).
    Year partitioning keeps cross-sectional reads to one file per year, which
    is how the research layer consumes these datasets."""

    def __init__(self, root: str | Path, dataset: str, market: str) -> None:
        self.root = Path(root)
        self.dataset = dataset
        self.market = market.upper()

    @property
    def directory(self) -> Path:
        return self.root / self.dataset / self.market

    def path(self, year: int) -> Path:
        return self.directory / f"{year}.parquet"

    def years(self) -> list[int]:
        if not self.directory.exists():
            return []
        return sorted(int(p.stem) for p in self.directory.glob("*.parquet") if p.stem.isdigit())

    def append(self, frame: pd.DataFrame) -> int:
        """Merge rows into their year partitions; (date, symbol) keeps the newest."""
        if frame.empty:
            return 0
        missing = [c for c in PANEL_KEY_COLUMNS + PANEL_META_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"Panel frame is missing required columns: {missing}")

        incoming = frame.copy()
        incoming["date"] = pd.to_datetime(incoming["date"]).dt.normalize()
        incoming["symbol"] = incoming["symbol"].astype(str).str.upper()

        written = 0
        for year, chunk in incoming.groupby(incoming["date"].dt.year):
            path = self.path(int(year))
            if path.exists():
                combined = pd.concat([pd.read_parquet(path), chunk], ignore_index=True)
            else:
                combined = chunk
            combined = combined.drop_duplicates(subset=PANEL_KEY_COLUMNS, keep="last")
            combined = combined.sort_values(PANEL_KEY_COLUMNS).reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(path)
            written += len(chunk)
        return written

    def read(
        self,
        *,
        as_of: date | None = None,
        start: date | None = None,
        end: date | None = None,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Rows visible as of `as_of` — the guard against look-ahead bias.

        Without `as_of` the full panel is returned; callers on the research or
        strategy path must always pass it."""
        years = self.years()
        if not years:
            return pd.DataFrame()
        if start is not None:
            years = [y for y in years if y >= start.year]
        if end is not None:
            years = [y for y in years if y <= end.year]
        frames = [pd.read_parquet(self.path(year)) for year in years]
        if not frames:
            return pd.DataFrame()

        panel = pd.concat(frames, ignore_index=True)
        if as_of is not None:
            panel = panel[panel["available_at"] <= pd.Timestamp(as_of)]
        if start is not None:
            panel = panel[panel["date"] >= pd.Timestamp(start)]
        if end is not None:
            panel = panel[panel["date"] <= pd.Timestamp(end)]
        if symbols is not None:
            wanted = {str(symbol).upper() for symbol in symbols}
            panel = panel[panel["symbol"].isin(wanted)]
        return panel.sort_values(PANEL_KEY_COLUMNS).reset_index(drop=True)

    def last_date(self, symbol: str | None = None) -> date | None:
        """Newest observation date, for incremental collection."""
        panel = self.read(symbols=[symbol] if symbol else None)
        if panel.empty:
            return None
        return panel["date"].max().date()
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_panel.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (20 tests)

- [ ] **Step 5: 전체 회귀 + 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"` → 전체 PASS

```powershell
git add src/tradingbot/data/panel.py tests/test_data_panel.py
git commit -m "M7(part): Add point-in-time panel store"
```

---

### Task 2: 거시 데이터 수집 (macro)

**Files:**
- Create: `src/tradingbot/data/macro.py`
- Modify: `config/research.toml` (`[macro]` 섹션 추가)
- Test: `tests/test_data_macro.py`

**Interfaces:**
- Consumes: `PanelStore`, `attach_metadata`, `next_trading_day_availability` (Task 1); `FinanceDataReader` (기존 의존성)
- Produces:
  - `macro.MACRO_SERIES: dict[str, str]` — 시리즈 이름 → FinanceDataReader 심볼
  - `macro.MACRO_DATA_VERSION = "1"`
  - `macro.fetch_macro_series(series: str, start: date, end: date | None = None) -> pd.DataFrame` (컬럼: `date`, `symbol`, `close`)
  - `macro.update_macro(store: PanelStore, *, series: Sequence[str] | None = None, start: date | None = None, end: date | None = None, fetcher=fetch_macro_series) -> int` (기록된 행 수)

- [ ] **Step 1: config에 섹션 추가**

`config/research.toml` 파일 끝에 추가:

```toml
[macro]
# 국면 판단용 시장·거시 시리즈 (FinanceDataReader 심볼).
# 종목 팩터가 아니라 상승장/하락장 필터와 리스크 관리에 사용한다.
kospi = "KS11"
kosdaq = "KQ11"
usdkrw = "USD/KRW"
kr_treasury_3y = "KR3YT=RR"
vix = "VIX"
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data_macro.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.macro import MACRO_SERIES, fetch_macro_series, update_macro
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "macro", "KR")


def fake_fetcher(series: str, start: date, end: date | None = None) -> pd.DataFrame:
    """Two business days of synthetic data, independent of the network."""
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame({"date": index, "symbol": series, "close": [100.0, 101.0]})


class TestMacroSeries:
    def test_core_series_are_registered(self):
        for expected in ["kospi", "kosdaq", "usdkrw", "vix"]:
            assert expected in MACRO_SERIES


class TestUpdateMacro:
    def test_writes_rows_with_availability_shifted_forward(self, store):
        written = update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert written == 2

        panel = store.read()
        assert set(panel["symbol"]) == {"KOSPI"}
        first = panel.iloc[0]
        assert first["date"] == pd.Timestamp("2024-01-02")
        # Data for Jan 2 is only usable from Jan 3 — no same-day look-ahead.
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["source"] == "financedatareader"

    def test_as_of_read_hides_future_rows(self, store):
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read(as_of=date(2024, 1, 3))) == 1

    def test_rerun_is_idempotent(self, store):
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_defaults_to_all_registered_series(self, store):
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(set(store.read()["symbol"])) == len(MACRO_SERIES)

    def test_unknown_series_raises_with_available_names(self, store):
        with pytest.raises(ValueError, match="Available:"):
            update_macro(store, series=["nope"], start=date(2024, 1, 1), fetcher=fake_fetcher)

    def test_empty_response_writes_nothing(self, store):
        def empty_fetcher(series, start, end=None):
            return pd.DataFrame(columns=["date", "symbol", "close"])

        assert update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=empty_fetcher) == 0
        assert store.read().empty

    def test_incremental_resumes_after_last_stored_date(self, store):
        captured: list[date] = []

        def recording_fetcher(series, start, end=None):
            captured.append(start)
            return fake_fetcher(series, start, end)

        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        # Second run resumes from the day after the last stored observation.
        assert captured[1] == date(2024, 1, 4)


class TestFetchMacroSeries:
    def test_normalizes_fdr_frame(self, monkeypatch):
        raw = pd.DataFrame(
            {"Close": [10.0, 11.0]},
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="Date"),
        )
        monkeypatch.setattr("FinanceDataReader.DataReader", lambda *a, **k: raw)
        result = fetch_macro_series("kospi", date(2024, 1, 1))
        assert list(result.columns) == ["date", "symbol", "close"]
        assert result.loc[0, "symbol"] == "kospi"
        assert result.loc[0, "close"] == 10.0

    def test_missing_close_column_raises(self, monkeypatch):
        raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"]))
        monkeypatch.setattr("FinanceDataReader.DataReader", lambda *a, **k: raw)
        with pytest.raises(ValueError, match="close"):
            fetch_macro_series("kospi", date(2024, 1, 1))
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_macro.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.macro'`

- [ ] **Step 4: 구현**

`src/tradingbot/data/macro.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

MACRO_DATA_VERSION = "1"
MACRO_SOURCE = "financedatareader"
MACRO_DEFAULT_START = date(2010, 1, 1)

# Series name -> FinanceDataReader symbol. Used as regime filters and risk
# context, not as per-stock factors.
MACRO_SERIES: dict[str, str] = {
    "kospi": "KS11",
    "kosdaq": "KQ11",
    "usdkrw": "USD/KRW",
    "kr_treasury_3y": "KR3YT=RR",
    "vix": "VIX",
}


def fetch_macro_series(series: str, start: date, end: date | None = None) -> pd.DataFrame:
    """Daily close for one macro series, normalized to the panel shape."""
    try:
        symbol = MACRO_SERIES[series]
    except KeyError as exc:
        available = ", ".join(sorted(MACRO_SERIES))
        raise ValueError(f"Unknown macro series: {series}. Available: {available}") from exc

    import FinanceDataReader as fdr

    raw = fdr.DataReader(symbol, start, end)
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol", "close"])

    columns = {str(column).lower(): column for column in raw.columns}
    if "close" not in columns:
        raise ValueError(f"Macro series {series} response has no close column: {list(raw.columns)}")

    return pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": series,
            "close": raw[columns["close"]].astype(float).to_numpy(),
        }
    )


def update_macro(
    store: PanelStore,
    *,
    series: Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_macro_series,
) -> int:
    """Incrementally collect macro series into the panel store."""
    names = list(series) if series is not None else list(MACRO_SERIES)
    unknown = [name for name in names if name not in MACRO_SERIES]
    if unknown:
        available = ", ".join(sorted(MACRO_SERIES))
        raise ValueError(f"Unknown macro series: {', '.join(unknown)}. Available: {available}")

    written = 0
    for name in names:
        last = store.last_date(name)
        fetch_start = last + timedelta(days=1) if last else (start or MACRO_DEFAULT_START)
        frame = fetcher(name, fetch_start, end)
        if frame.empty:
            LOGGER.info("Macro series %s returned no new rows from %s", name, fetch_start)
            continue
        tagged = attach_metadata(
            frame,
            source=MACRO_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=MACRO_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_macro.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (11 tests)

- [ ] **Step 6: 실데이터 스모크 (네트워크)**

```powershell
.\.venv\Scripts\python.exe -c @'
from datetime import date
from pathlib import Path
from tradingbot.data.macro import update_macro
from tradingbot.data.panel import PanelStore
store = PanelStore(Path("data/processed"), "macro", "KR")
print("rows:", update_macro(store, series=["kospi"], start=date(2024, 1, 1)))
print(store.read().tail(3))
'@
```

Expected: 수백 행이 기록되고 최근 KOSPI 종가 3행이 출력된다. 값 자체는 검증하지 않는다 (시장 데이터). 실패하면 어떤 시리즈가 왜 실패했는지 보고에 남긴다.

- [ ] **Step 7: 커밋**

```powershell
git add src/tradingbot/data/macro.py config/research.toml tests/test_data_macro.py
git commit -m "M7(part): Add macro series collection"
```

---

### Task 3: 수급·밸류에이션 수집 (pykrx)

**Files:**
- Modify: `pyproject.toml` (`pykrx` 의존성 추가)
- Create: `src/tradingbot/data/flows.py`
- Create: `src/tradingbot/data/valuation.py`
- Test: `tests/test_data_flows.py`, `tests/test_data_valuation.py`

**Interfaces:**
- Consumes: `PanelStore`, `attach_metadata`, `next_trading_day_availability` (Task 1)
- Produces:
  - `flows.FLOW_COLUMNS = ["foreign_net", "institution_net", "individual_net"]` (단위: 원, 순매수 거래대금)
  - `flows.fetch_flows(symbol: str, start: date, end: date) -> pd.DataFrame` (컬럼: `date`, `symbol`, + FLOW_COLUMNS)
  - `flows.update_flows(store, *, symbols, start=None, end=None, fetcher=fetch_flows) -> int`
  - `valuation.VALUATION_COLUMNS = ["per", "pbr", "eps", "bps", "div_yield"]`
  - `valuation.fetch_valuation(symbol, start, end) -> pd.DataFrame`
  - `valuation.update_valuation(store, *, symbols, start=None, end=None, fetcher=fetch_valuation) -> int`

- [ ] **Step 1: pykrx 의존성 추가 후 실제 반환 형태 확인**

`pyproject.toml`의 `dependencies` 목록에 추가 (`exchange-calendars` 다음 줄):

```toml
    "pykrx>=1.2.8",
```

설치:

```powershell
py -m uv sync --extra dev
```

**pykrx의 실제 함수 시그니처와 반환 컬럼을 눈으로 먼저 확인한다** — 아래 어댑터는 이 출력에 맞춰 작성해야 한다:

```powershell
.\.venv\Scripts\python.exe -c @'
from pykrx import stock
flows = stock.get_market_trading_value_by_date("20240102", "20240110", "005930")
print("FLOWS columns:", list(flows.columns))
print(flows.head(3))
fundamental = stock.get_market_fundamental("20240102", "20240110", "005930")
print("VALUATION columns:", list(fundamental.columns))
print(fundamental.head(3))
'@
```

기대: 수급은 `기관합계/기타법인/개인/외국인합계/전체` 계열 컬럼, 밸류에이션은 `BPS/PER/PBR/EPS/DIV/DPS` 컬럼을 날짜 인덱스로 반환한다. **실제 컬럼명이 다르면 아래 `_COLUMN_MAP`을 실제 출력에 맞게 고치고, 무엇을 어떻게 고쳤는지 보고에 남긴다.** 함수 시그니처가 다르면(예: 인자 이름/순서) 그에 맞춰 호출부를 고친다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data_flows.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.flows import FLOW_COLUMNS, normalize_flows, update_flows
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "flows", "KR")


def fake_fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "foreign_net": [1000.0, -500.0],
            "institution_net": [-200.0, 300.0],
            "individual_net": [-800.0, 200.0],
        }
    )


class TestNormalizeFlows:
    def test_maps_korean_columns_to_english(self):
        raw = pd.DataFrame(
            {"외국인합계": [1000], "기관합계": [-200], "개인": [-800], "전체": [0]},
            index=pd.DatetimeIndex(["2024-01-02"], name="날짜"),
        )
        result = normalize_flows(raw, "005930")
        assert list(result.columns) == ["date", "symbol"] + FLOW_COLUMNS
        assert result.loc[0, "foreign_net"] == 1000.0
        assert result.loc[0, "symbol"] == "005930"

    def test_missing_expected_column_raises(self):
        raw = pd.DataFrame({"외국인합계": [1]}, index=pd.DatetimeIndex(["2024-01-02"]))
        with pytest.raises(ValueError, match="column"):
            normalize_flows(raw, "005930")

    def test_empty_frame_returns_empty_with_schema(self):
        result = normalize_flows(pd.DataFrame(), "005930")
        assert result.empty
        assert list(result.columns) == ["date", "symbol"] + FLOW_COLUMNS


class TestUpdateFlows:
    def test_writes_rows_with_next_day_availability(self, store):
        written = update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert written == 2

        panel = store.read()
        first = panel.iloc[0]
        assert first["date"] == pd.Timestamp("2024-01-02")
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["foreign_net"] == 1000.0
        assert first["source"] == "pykrx"

    def test_as_of_read_hides_future_rows(self, store):
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read(as_of=date(2024, 1, 3))) == 1

    def test_rerun_is_idempotent(self, store):
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_one_failing_symbol_does_not_stop_the_rest(self, store):
        def flaky(symbol, start, end):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return fake_fetcher(symbol, start, end)

        written = update_flows(
            store, symbols=["BAD", "005930"], start=date(2024, 1, 1), fetcher=flaky
        )
        assert written == 2
        assert set(store.read()["symbol"]) == {"005930"}

    def test_empty_symbol_list_writes_nothing(self, store):
        assert update_flows(store, symbols=[], start=date(2024, 1, 1), fetcher=fake_fetcher) == 0
```

`tests/test_data_valuation.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.panel import PanelStore
from tradingbot.data.valuation import VALUATION_COLUMNS, normalize_valuation, update_valuation


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "valuation", "KR")


def fake_fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "per": [10.0, 10.5],
            "pbr": [1.2, 1.3],
            "eps": [5000.0, 5000.0],
            "bps": [40000.0, 40000.0],
            "div_yield": [2.0, 2.0],
        }
    )


class TestNormalizeValuation:
    def test_maps_krx_columns(self):
        raw = pd.DataFrame(
            {"BPS": [40000], "PER": [10.0], "PBR": [1.2], "EPS": [5000], "DIV": [2.0], "DPS": [100]},
            index=pd.DatetimeIndex(["2024-01-02"], name="날짜"),
        )
        result = normalize_valuation(raw, "005930")
        assert list(result.columns) == ["date", "symbol"] + VALUATION_COLUMNS
        assert result.loc[0, "per"] == 10.0
        assert result.loc[0, "div_yield"] == 2.0

    def test_zero_per_becomes_nan(self):
        # KRX reports 0 for loss-making companies; 0 would rank as "cheapest".
        raw = pd.DataFrame(
            {"BPS": [40000], "PER": [0.0], "PBR": [1.2], "EPS": [-100], "DIV": [0.0], "DPS": [0]},
            index=pd.DatetimeIndex(["2024-01-02"]),
        )
        result = normalize_valuation(raw, "005930")
        assert pd.isna(result.loc[0, "per"])

    def test_empty_frame_returns_empty_with_schema(self):
        result = normalize_valuation(pd.DataFrame(), "005930")
        assert result.empty
        assert list(result.columns) == ["date", "symbol"] + VALUATION_COLUMNS


class TestUpdateValuation:
    def test_writes_rows_with_next_day_availability(self, store):
        written = update_valuation(
            store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher
        )
        assert written == 2
        first = store.read().iloc[0]
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["per"] == 10.0
        assert first["source"] == "pykrx"

    def test_rerun_is_idempotent(self, store):
        update_valuation(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_valuation(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_one_failing_symbol_does_not_stop_the_rest(self, store):
        def flaky(symbol, start, end):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return fake_fetcher(symbol, start, end)

        assert update_valuation(
            store, symbols=["BAD", "005930"], start=date(2024, 1, 1), fetcher=flaky
        ) == 2
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_flows.py tests\test_data_valuation.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.flows'`

- [ ] **Step 4: flows 구현**

`src/tradingbot/data/flows.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FLOWS_DATA_VERSION = "1"
FLOWS_SOURCE = "pykrx"
FLOWS_DEFAULT_START = date(2015, 1, 1)

# Net buy value in KRW, per investor group.
FLOW_COLUMNS = ["foreign_net", "institution_net", "individual_net"]

# KRX column -> our column. Verified against pykrx output in Task 3 Step 1.
_COLUMN_MAP = {
    "외국인합계": "foreign_net",
    "기관합계": "institution_net",
    "개인": "individual_net",
}


def normalize_flows(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx investor-flow frame into the panel schema."""
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + FLOW_COLUMNS)

    missing = [column for column in _COLUMN_MAP if column not in raw.columns]
    if missing:
        raise ValueError(f"Flow response is missing column(s) {missing}; got {list(raw.columns)}")

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": str(symbol).upper(),
        }
    )
    for source_column, target_column in _COLUMN_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    return frame[["date", "symbol"] + FLOW_COLUMNS].reset_index(drop=True)


def fetch_flows(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily investor net-buy values for one symbol."""
    from pykrx import stock

    raw = stock.get_market_trading_value_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_flows(raw, symbol)


def update_flows(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_flows,
) -> int:
    """Incrementally collect investor flows. One symbol's failure is logged
    and skipped so a single bad ticker cannot abort the batch."""
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or FLOWS_DEFAULT_START)
        if fetch_start > fetch_end:
            continue
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except Exception:
            LOGGER.exception("Flow collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue
        tagged = attach_metadata(
            frame,
            source=FLOWS_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=FLOWS_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
```

- [ ] **Step 5: valuation 구현**

`src/tradingbot/data/valuation.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

VALUATION_DATA_VERSION = "1"
VALUATION_SOURCE = "pykrx"
VALUATION_DEFAULT_START = date(2015, 1, 1)

VALUATION_COLUMNS = ["per", "pbr", "eps", "bps", "div_yield"]

# KRX publishes these daily from the latest disclosed financials, so they are
# point-in-time correct as observed — no restatement backfill to undo.
_COLUMN_MAP = {"PER": "per", "PBR": "pbr", "EPS": "eps", "BPS": "bps", "DIV": "div_yield"}

# KRX reports 0 (not null) when a ratio is undefined, e.g. PER for a
# loss-making company. Left as 0 these would rank as "cheapest".
_ZERO_MEANS_MISSING = ["per", "pbr"]


def normalize_valuation(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx fundamental frame into the panel schema."""
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + VALUATION_COLUMNS)

    missing = [column for column in _COLUMN_MAP if column not in raw.columns]
    if missing:
        raise ValueError(
            f"Valuation response is missing column(s) {missing}; got {list(raw.columns)}"
        )

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": str(symbol).upper(),
        }
    )
    for source_column, target_column in _COLUMN_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    for column in _ZERO_MEANS_MISSING:
        frame[column] = frame[column].replace(0.0, float("nan"))
    return frame[["date", "symbol"] + VALUATION_COLUMNS].reset_index(drop=True)


def fetch_valuation(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily valuation ratios for one symbol."""
    from pykrx import stock

    raw = stock.get_market_fundamental(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_valuation(raw, symbol)


def update_valuation(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_valuation,
) -> int:
    """Incrementally collect valuation ratios, skipping failing symbols."""
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or VALUATION_DEFAULT_START)
        if fetch_start > fetch_end:
            continue
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except Exception:
            LOGGER.exception("Valuation collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue
        tagged = attach_metadata(
            frame,
            source=VALUATION_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=VALUATION_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
```

- [ ] **Step 6: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_flows.py tests\test_data_valuation.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (16 tests)

- [ ] **Step 7: 실데이터 스모크 (네트워크)**

```powershell
.\.venv\Scripts\python.exe -c @'
from datetime import date
from pathlib import Path
from tradingbot.data.flows import update_flows
from tradingbot.data.valuation import update_valuation
from tradingbot.data.panel import PanelStore
flows = PanelStore(Path("data/processed"), "flows", "KR")
val = PanelStore(Path("data/processed"), "valuation", "KR")
print("flow rows:", update_flows(flows, symbols=["005930"], start=date(2024, 1, 1)))
print(flows.read().tail(2))
print("valuation rows:", update_valuation(val, symbols=["005930"], start=date(2024, 1, 1)))
print(val.read().tail(2))
'@
```

Expected: 삼성전자 수급·밸류에이션이 각각 수백 행 기록되고 최근 2행이 출력된다. 실패하면 pykrx 반환 형태를 다시 확인하고 `_COLUMN_MAP`을 고친 뒤 재시도한다.

- [ ] **Step 8: 전체 회귀 + 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"` → 전체 PASS

```powershell
git add pyproject.toml uv.lock src/tradingbot/data/flows.py src/tradingbot/data/valuation.py tests/test_data_flows.py tests/test_data_valuation.py
git commit -m "M7(part): Add KRX investor-flow and valuation collection"
```

---

### Task 4: 재무 데이터 수집 (DART)

**Files:**
- Create: `src/tradingbot/data/fundamentals.py`
- Test: `tests/test_data_fundamentals.py`

**Interfaces:**
- Consumes: `PanelStore`, `attach_metadata`, `next_trading_day_availability` (Task 1); `requests` (기존)
- Produces:
  - `fundamentals.FUNDAMENTAL_COLUMNS = ["revenue", "operating_income", "net_income", "total_assets", "total_equity"]`
  - `fundamentals.REPORT_CODES: dict[str, str]` (분기 → DART reprt_code)
  - `fundamentals.MissingApiKeyError` — **`data/credentials.py`의 `MissingCredentialsError`를 상속한다** (Task 3 수정에서 도입된 공용 타입)
  - `fundamentals.dart_api_key() -> str` (환경변수 `DART_API_KEY`, 없으면 `MissingApiKeyError`)
  - `fundamentals.parse_financials(payload: dict, symbol: str) -> pd.DataFrame` (컬럼: `date`(=보고서 기준 분기말), `symbol`, `announcement_date`, + FUNDAMENTAL_COLUMNS)
  - `fundamentals.update_fundamentals(store, *, symbols, corp_codes: dict[str, str], years: Sequence[int], fetcher=fetch_financials) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_fundamentals.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.fundamentals import (
    FUNDAMENTAL_COLUMNS,
    MissingApiKeyError,
    dart_api_key,
    parse_financials,
    update_fundamentals,
)
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "fundamentals", "KR")


def payload(rcept_no: str = "20240315000123") -> dict:
    """Shape of a DART fnlttSinglAcnt response (주요계정)."""
    def row(name: str, amount: str, statement: str) -> dict:
        return {
            "rcept_no": rcept_no,
            "bsns_year": "2023",
            "reprt_code": "11011",
            "sj_div": statement,
            "account_nm": name,
            "thstrm_amount": amount,
        }

    return {
        "status": "000",
        "message": "정상",
        "list": [
            row("매출액", "1,000,000", "IS"),
            row("영업이익", "200,000", "IS"),
            row("당기순이익", "150,000", "IS"),
            row("자산총계", "5,000,000", "BS"),
            row("자본총계", "3,000,000", "BS"),
        ],
    }


class TestApiKey:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("DART_API_KEY", raising=False)
        with pytest.raises(MissingApiKeyError):
            dart_api_key()

    def test_key_is_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("DART_API_KEY", "secret")
        assert dart_api_key() == "secret"


class TestParseFinancials:
    def test_extracts_accounts_and_dates(self):
        frame = parse_financials(payload(), "005930")
        assert len(frame) == 1
        row = frame.iloc[0]
        assert row["symbol"] == "005930"
        assert row["revenue"] == 1_000_000
        assert row["operating_income"] == 200_000
        assert row["total_assets"] == 5_000_000
        # Annual report for 2023 -> period end is the fiscal year end.
        assert row["date"] == pd.Timestamp("2023-12-31")
        # Announcement date comes from the receipt number's leading date.
        assert row["announcement_date"] == pd.Timestamp("2024-03-15")

    def test_quarterly_report_period_end(self):
        data = payload()
        for item in data["list"]:
            item["reprt_code"] = "11013"  # 1분기
        frame = parse_financials(data, "005930")
        assert frame.iloc[0]["date"] == pd.Timestamp("2023-03-31")

    def test_negative_amount_in_parentheses(self):
        data = payload()
        data["list"][1]["thstrm_amount"] = "(50,000)"
        frame = parse_financials(data, "005930")
        assert frame.iloc[0]["operating_income"] == -50_000

    def test_blank_amount_becomes_nan(self):
        data = payload()
        data["list"][0]["thstrm_amount"] = "-"
        frame = parse_financials(data, "005930")
        assert pd.isna(frame.iloc[0]["revenue"])

    def test_no_data_status_returns_empty(self):
        frame = parse_financials({"status": "013", "message": "조회된 데이터가 없습니다."}, "005930")
        assert frame.empty
        assert list(frame.columns) == ["date", "symbol", "announcement_date"] + FUNDAMENTAL_COLUMNS

    def test_error_status_raises(self):
        with pytest.raises(RuntimeError, match="020"):
            parse_financials({"status": "020", "message": "요청 제한 초과"}, "005930")


class TestUpdateFundamentals:
    def test_availability_follows_announcement_not_period_end(self, store):
        def fetcher(corp_code, year, report_code):
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        written = update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=fetcher,
        )
        assert written == 1

        row = store.read().iloc[0]
        assert row["date"] == pd.Timestamp("2023-12-31")
        # Announced 2024-03-15 (Fri) -> usable from the next trading day.
        assert row["available_at"] == pd.Timestamp("2024-03-18")
        assert row["source"] == "dart"

    def test_as_of_before_announcement_hides_the_row(self, store):
        def fetcher(corp_code, year, report_code):
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=fetcher,
        )
        from datetime import date

        # Period ended 2023-12-31 but nobody knew the numbers until March.
        assert store.read(as_of=date(2024, 1, 15)).empty
        assert len(store.read(as_of=date(2024, 3, 18))) == 1

    def test_symbol_without_corp_code_is_skipped(self, store):
        def fetcher(corp_code, year, report_code):
            return payload()

        assert (
            update_fundamentals(
                store, symbols=["999999"], corp_codes={}, years=[2023], fetcher=fetcher
            )
            == 0
        )

    def test_fetch_failure_skips_symbol_without_aborting(self, store):
        def flaky(corp_code, year, report_code):
            if corp_code == "BAD":
                raise RuntimeError("boom")
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        written = update_fundamentals(
            store,
            symbols=["999999", "005930"],
            corp_codes={"999999": "BAD", "005930": "00126380"},
            years=[2023],
            fetcher=flaky,
        )
        assert written == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_fundamentals.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.fundamentals'`

- [ ] **Step 3: 구현**

`src/tradingbot/data/fundamentals.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, require_env
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FUNDAMENTALS_DATA_VERSION = "1"
FUNDAMENTALS_SOURCE = "dart"
DART_ENDPOINT = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

FUNDAMENTAL_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_equity",
]

# DART report code -> (fiscal quarter end month, day)
REPORT_CODES: dict[str, tuple[int, int]] = {
    "11013": (3, 31),   # 1분기보고서
    "11012": (6, 30),   # 반기보고서
    "11014": (9, 30),   # 3분기보고서
    "11011": (12, 31),  # 사업보고서
}

_ACCOUNT_MAP = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "자산총계": "total_assets",
    "자본총계": "total_equity",
}

_EMPTY_COLUMNS = ["date", "symbol", "announcement_date"] + FUNDAMENTAL_COLUMNS

# DART returns 013 when a filing simply does not exist — a normal outcome,
# not an error.
_NO_DATA_STATUS = "013"


class MissingApiKeyError(MissingCredentialsError):
    """Raised when DART_API_KEY is not set."""


def dart_api_key() -> str:
    try:
        return require_env(
            "DART_API_KEY",
            hint="Get a free key at https://opendart.fss.or.kr and set it as an environment "
            "variable; never commit it to the repository.",
        )
    except MissingCredentialsError as exc:
        raise MissingApiKeyError(str(exc)) from exc


def _parse_amount(raw: Any) -> float:
    """DART amounts are comma-grouped strings; negatives use parentheses."""
    text = str(raw).strip()
    if not text or text in {"-", "--"}:
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    digits = text.strip("()").replace(",", "").replace(" ", "")
    if digits.startswith("-"):
        negative = True
        digits = digits[1:]
    if not digits.replace(".", "", 1).isdigit():
        return float("nan")
    value = float(digits)
    return -value if negative else value


def parse_financials(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    """Turn one DART fnlttSinglAcnt response into a single panel row."""
    status = str(payload.get("status", ""))
    if status == _NO_DATA_STATUS:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)
    if status != "000":
        raise RuntimeError(f"DART request failed: status={status} message={payload.get('message')}")

    items = payload.get("list") or []
    if not items:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)

    first = items[0]
    year = int(first["bsns_year"])
    report_code = str(first["reprt_code"])
    if report_code not in REPORT_CODES:
        raise RuntimeError(f"Unknown DART report code: {report_code}")
    month, day = REPORT_CODES[report_code]

    receipt = str(first["rcept_no"])
    announcement = pd.Timestamp(f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}")

    row: dict[str, Any] = {
        "date": pd.Timestamp(year=year, month=month, day=day),
        "symbol": str(symbol).upper(),
        "announcement_date": announcement,
    }
    for column in FUNDAMENTAL_COLUMNS:
        row[column] = float("nan")
    for item in items:
        column = _ACCOUNT_MAP.get(str(item.get("account_nm", "")).strip())
        if column:
            row[column] = _parse_amount(item.get("thstrm_amount"))
    return pd.DataFrame([row], columns=_EMPTY_COLUMNS)


def fetch_financials(corp_code: str, year: int, report_code: str) -> dict[str, Any]:
    """One DART 주요계정 request. Network call; mocked in tests."""
    import requests

    response = requests.get(
        DART_ENDPOINT,
        params={
            "crtfc_key": dart_api_key(),
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": "CFS",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def update_fundamentals(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    corp_codes: dict[str, str],
    years: Sequence[int],
    fetcher: Callable[..., dict[str, Any]] = fetch_financials,
) -> int:
    """Collect quarterly financials.

    `available_at` follows the announcement date, never the period end — the
    numbers for a quarter are not knowable until they are filed."""
    written = 0
    for symbol in symbols:
        corp_code = corp_codes.get(str(symbol).upper()) or corp_codes.get(str(symbol))
        if not corp_code:
            LOGGER.warning("No DART corp_code for %s; skipping", symbol)
            continue
        for year in years:
            for report_code in REPORT_CODES:
                try:
                    payload = fetcher(corp_code, year, report_code)
                    frame = parse_financials(payload, symbol)
                except Exception:
                    LOGGER.exception(
                        "Fundamentals fetch failed for %s %s %s; skipping",
                        symbol,
                        year,
                        report_code,
                    )
                    continue
                if frame.empty:
                    continue
                tagged = attach_metadata(
                    frame,
                    source=FUNDAMENTALS_SOURCE,
                    available_at=next_trading_day_availability(
                        frame["announcement_date"], store.market
                    ),
                    data_version=FUNDAMENTALS_DATA_VERSION,
                )
                written += store.append(tagged)
    return written
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_fundamentals.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (13 tests)

- [ ] **Step 5: 실데이터 스모크 (선택 — DART_API_KEY가 있을 때만)**

키가 없으면 이 단계를 건너뛰고 보고에 "DART_API_KEY 미설정으로 스모크 생략"이라고 명시한다. 키를 새로 발급받거나 추측하지 않는다.

```powershell
if ($env:DART_API_KEY) {
  .\.venv\Scripts\python.exe -c @'
from tradingbot.data.fundamentals import fetch_financials, parse_financials
payload = fetch_financials("00126380", 2023, "11011")  # 삼성전자 2023 사업보고서
print(parse_financials(payload, "005930").T)
'@
} else { "DART_API_KEY not set - skipping live smoke" }
```

- [ ] **Step 6: 커밋**

```powershell
git add src/tradingbot/data/fundamentals.py tests/test_data_fundamentals.py
git commit -m "M7(part): Add DART fundamentals collection"
```

---

### Task 5: 데이터 품질 검사

**Files:**
- Create: `src/tradingbot/data/quality.py`
- Test: `tests/test_data_quality.py`

**Interfaces:**
- Consumes: `get_calendar` (기존)
- Produces:
  - `quality.Severity` (`"pass" | "warn" | "fail"` 문자열 상수: `PASS`, `WARN`, `FAIL`)
  - `quality.QualityIssue` (frozen dataclass: `check: str`, `severity: str`, `message: str`, `count: int`)
  - `quality.QualityReport` (frozen dataclass: `dataset: str`, `issues: list[QualityIssue]`; property `severity`, `ok`)
  - `quality.check_ohlcv(frame, *, dataset, market, max_jump=0.5) -> QualityReport`
  - `quality.check_panel(frame, *, dataset) -> QualityReport`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_quality.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.quality import FAIL, PASS, WARN, check_ohlcv, check_panel


def ohlcv(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    ).set_index("date")
    frame.index = pd.to_datetime(frame.index)
    return frame


CLEAN = ohlcv(
    [
        ("2024-01-02", 100.0, 105.0, 99.0, 104.0, 1000.0),
        ("2024-01-03", 104.0, 106.0, 103.0, 105.0, 1100.0),
        ("2024-01-04", 105.0, 108.0, 104.0, 107.0, 1200.0),
    ]
)


class TestCheckOhlcv:
    def test_clean_data_passes(self):
        report = check_ohlcv(CLEAN, dataset="prices", market="KR")
        assert report.ok
        assert report.severity == PASS
        assert report.issues == []

    def test_high_below_low_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "high"] = 1.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert report.severity == FAIL
        assert any(issue.check == "ohlc_logic" for issue in report.issues)

    def test_close_outside_high_low_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "close"] = 999.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert report.severity == FAIL

    def test_negative_volume_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "volume"] = -1.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert any(issue.check == "negative_volume" for issue in report.issues)
        assert report.severity == FAIL

    def test_duplicate_dates_fail(self):
        broken = pd.concat([CLEAN, CLEAN.iloc[[0]]])
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert any(issue.check == "duplicate_date" for issue in report.issues)

    def test_large_price_jump_warns(self):
        jumpy = CLEAN.copy()
        jumpy.loc[pd.Timestamp("2024-01-04"), ["open", "high", "low", "close"]] = [
            500.0,
            510.0,
            499.0,
            505.0,
        ]
        report = check_ohlcv(jumpy, dataset="prices", market="KR")
        assert any(issue.check == "price_jump" for issue in report.issues)
        assert report.severity == WARN

    def test_missing_trading_day_warns(self):
        # 2024-01-03 removed from an otherwise contiguous span.
        gapped = CLEAN.drop(index=pd.Timestamp("2024-01-03"))
        report = check_ohlcv(gapped, dataset="prices", market="KR")
        assert any(issue.check == "missing_trading_day" for issue in report.issues)

    def test_empty_frame_fails_loudly(self):
        report = check_ohlcv(ohlcv([]), dataset="prices", market="KR")
        assert report.severity == FAIL
        assert any(issue.check == "empty" for issue in report.issues)


def panel(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(d),
                "symbol": s,
                "available_at": pd.Timestamp(a),
                "value": 1.0,
            }
            for d, s, a in rows
        ]
    )


class TestCheckPanel:
    def test_clean_panel_passes(self):
        report = check_panel(
            panel([("2024-01-02", "005930", "2024-01-03")]), dataset="flows"
        )
        assert report.ok

    def test_duplicate_key_fails(self):
        report = check_panel(
            panel(
                [("2024-01-02", "005930", "2024-01-03"), ("2024-01-02", "005930", "2024-01-03")]
            ),
            dataset="flows",
        )
        assert any(issue.check == "duplicate_key" for issue in report.issues)
        assert report.severity == FAIL

    def test_availability_before_observation_fails(self):
        # available_at earlier than date means the data claims to be knowable
        # before it existed — a look-ahead leak.
        report = check_panel(
            panel([("2024-01-02", "005930", "2024-01-01")]), dataset="flows"
        )
        assert any(issue.check == "availability_precedes_date" for issue in report.issues)
        assert report.severity == FAIL

    def test_missing_meta_column_fails(self):
        frame = panel([("2024-01-02", "005930", "2024-01-03")]).drop(columns=["available_at"])
        report = check_panel(frame, dataset="flows")
        assert any(issue.check == "missing_column" for issue in report.issues)

    def test_empty_panel_warns_but_does_not_fail(self):
        report = check_panel(pd.DataFrame(), dataset="flows")
        assert report.severity == WARN
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_quality.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.quality'`

- [ ] **Step 3: 구현**

`src/tradingbot/data/quality.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradingbot.engine.calendar import get_calendar

PASS = "pass"
WARN = "warn"
FAIL = "fail"

_SEVERITY_ORDER = {PASS: 0, WARN: 1, FAIL: 2}

PANEL_REQUIRED_COLUMNS = ["date", "symbol", "available_at"]


@dataclass(frozen=True)
class QualityIssue:
    check: str
    severity: str
    message: str
    count: int = 0


@dataclass(frozen=True)
class QualityReport:
    dataset: str
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if not self.issues:
            return PASS
        return max((issue.severity for issue in self.issues), key=lambda s: _SEVERITY_ORDER[s])

    @property
    def ok(self) -> bool:
        return self.severity != FAIL

    def summary(self) -> str:
        if not self.issues:
            return f"{self.dataset}: pass"
        details = "; ".join(f"{i.check}({i.count})" for i in self.issues)
        return f"{self.dataset}: {self.severity} — {details}"


def check_ohlcv(
    frame: pd.DataFrame, *, dataset: str, market: str, max_jump: float = 0.5
) -> QualityReport:
    """Validate a daily OHLCV frame indexed by date."""
    issues: list[QualityIssue] = []
    if frame.empty:
        return QualityReport(dataset, [QualityIssue("empty", FAIL, "No rows", 0)])

    duplicates = int(frame.index.duplicated().sum())
    if duplicates:
        issues.append(QualityIssue("duplicate_date", FAIL, "Duplicate dates", duplicates))

    illogical = (
        (frame["high"] < frame["low"])
        | (frame["close"] > frame["high"])
        | (frame["close"] < frame["low"])
        | (frame["open"] > frame["high"])
        | (frame["open"] < frame["low"])
    )
    count = int(illogical.sum())
    if count:
        issues.append(QualityIssue("ohlc_logic", FAIL, "OHLC bounds violated", count))

    negative = int((frame["volume"] < 0).sum())
    if negative:
        issues.append(QualityIssue("negative_volume", FAIL, "Negative volume", negative))

    non_positive = int((frame["close"] <= 0).sum())
    if non_positive:
        issues.append(QualityIssue("non_positive_price", FAIL, "Non-positive close", non_positive))

    returns = frame["close"].pct_change().abs()
    jumps = int((returns > max_jump).sum())
    if jumps:
        issues.append(
            QualityIssue("price_jump", WARN, f"Daily move over {max_jump:.0%}", jumps)
        )

    expected = get_calendar(market).trading_days(
        frame.index.min().date(), frame.index.max().date()
    )
    present = {timestamp.date() for timestamp in frame.index}
    missing = [day for day in expected if day not in present]
    if missing:
        issues.append(
            QualityIssue("missing_trading_day", WARN, "Trading days absent", len(missing))
        )

    return QualityReport(dataset, issues)


def check_panel(frame: pd.DataFrame, *, dataset: str) -> QualityReport:
    """Validate a point-in-time panel frame."""
    if frame.empty:
        return QualityReport(dataset, [QualityIssue("empty", WARN, "No rows", 0)])

    missing_columns = [c for c in PANEL_REQUIRED_COLUMNS if c not in frame.columns]
    if missing_columns:
        return QualityReport(
            dataset,
            [
                QualityIssue(
                    "missing_column", FAIL, f"Missing columns: {missing_columns}", len(missing_columns)
                )
            ],
        )

    issues: list[QualityIssue] = []
    duplicates = int(frame.duplicated(subset=["date", "symbol"]).sum())
    if duplicates:
        issues.append(QualityIssue("duplicate_key", FAIL, "Duplicate (date, symbol)", duplicates))

    early = int((frame["available_at"] < frame["date"]).sum())
    if early:
        issues.append(
            QualityIssue(
                "availability_precedes_date",
                FAIL,
                "available_at earlier than the observation date",
                early,
            )
        )

    return QualityReport(dataset, issues)
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_quality.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (13 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/data/quality.py tests/test_data_quality.py
git commit -m "M7(part): Add data quality checks"
```

---

### Task 6: 파이프라인 오케스트레이션 + CLI + 배치 + 문서

**Files:**
- Create: `src/tradingbot/data/pipeline.py`
- Modify: `src/tradingbot/cli.py` (`data pipeline` 서브커맨드)
- Modify: `config/default.toml` (`[pipeline]` 섹션)
- Create: `데이터 수집.bat`
- Modify: `README.md`, `docs/architecture.md`
- Test: `tests/test_data_pipeline.py`

**Interfaces:**
- Consumes: Task 1~5의 모든 Produces + `resolve_project_path`, `load_config` (기존)
- Produces:
  - `pipeline.SourceResult` (frozen dataclass: `name, status, rows, message`), 상태값 `"ok" | "failed" | "skipped"`
  - `pipeline.PipelineResult` (frozen dataclass: `started_at, finished_at, market, results: list[SourceResult]`; property `ok`; method `to_dict()`)
  - `pipeline.with_retry(fn, *, attempts=3, base_delay=2.0, no_retry=(), sleep=time.sleep)`
  - `pipeline.run_pipeline(config, *, market, symbols, processed_root=None, log_root=None, include_fundamentals=True, collectors=None) -> PipelineResult`

- [ ] **Step 1: config 섹션 추가**

`config/default.toml` 파일 끝에 추가:

```toml
[pipeline]
# 일일 배치 수집 대상. 작업 스케줄러가 장 마감 후 하루 1회 실행한다.
processed_dir = "data/processed"
log_dir = "state/pipeline_log"
symbols = ["005930", "000660", "035420", "051910", "005380"]
fundamental_years = 3
retry_attempts = 3
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data_pipeline.py`:

```python
from __future__ import annotations

import json

import pytest

from tradingbot.cli import build_parser, cmd_data_pipeline
from tradingbot.data.pipeline import run_pipeline, with_retry


@pytest.fixture
def config(tmp_path):
    return {
        "pipeline": {
            "processed_dir": str(tmp_path / "processed"),
            "log_dir": str(tmp_path / "log"),
            "symbols": ["005930"],
            "fundamental_years": 1,
            "retry_attempts": 2,
        }
    }


def ok_collector(name: str, rows: int = 5):
    def collect(**kwargs):
        return rows

    collect.__name__ = name
    return collect


class TestWithRetry:
    def test_returns_first_success_without_sleeping(self):
        slept: list[float] = []
        assert with_retry(lambda: 42, attempts=3, sleep=slept.append) == 42
        assert slept == []

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        assert with_retry(flaky, attempts=3, base_delay=0.01, sleep=lambda _: None) == "ok"
        assert calls["n"] == 3

    def test_reraises_after_exhausting_attempts(self):
        with pytest.raises(RuntimeError, match="always"):
            with_retry(
                lambda: (_ for _ in ()).throw(RuntimeError("always")),
                attempts=2,
                base_delay=0.01,
                sleep=lambda _: None,
            )

    def test_backoff_grows(self):
        slept: list[float] = []

        def always_fails():
            raise RuntimeError("no")

        with pytest.raises(RuntimeError):
            with_retry(always_fails, attempts=3, base_delay=1.0, sleep=slept.append)
        assert slept == [1.0, 2.0]

    def test_no_retry_types_fail_immediately(self):
        slept: list[float] = []
        calls = {"n": 0}

        def missing_config():
            calls["n"] += 1
            raise KeyError("no api key")

        # A missing key is not transient — retrying only wastes the batch's time.
        with pytest.raises(KeyError):
            with_retry(
                missing_config, attempts=3, base_delay=1.0, no_retry=(KeyError,), sleep=slept.append
            )
        assert calls["n"] == 1
        assert slept == []


class TestRunPipeline:
    def test_all_sources_ok(self, config):
        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"macro": ok_collector("macro"), "flows": ok_collector("flows", 7)},
        )
        assert result.ok
        assert {r.name for r in result.results} == {"macro", "flows"}
        assert sum(r.rows for r in result.results) == 12

    def test_one_source_failure_does_not_stop_others(self, config):
        def boom(**kwargs):
            raise RuntimeError("source down")

        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"macro": boom, "flows": ok_collector("flows", 7)},
        )
        assert not result.ok
        by_name = {r.name: r for r in result.results}
        assert by_name["macro"].status == "failed"
        assert "source down" in by_name["macro"].message
        assert by_name["flows"].status == "ok"
        assert by_name["flows"].rows == 7

    def test_writes_run_log_json(self, config, tmp_path):
        run_pipeline(
            config, market="KR", symbols=["005930"], collectors={"macro": ok_collector("macro")}
        )
        logs = list((tmp_path / "log").glob("*.json"))
        assert len(logs) == 1
        record = json.loads(logs[0].read_text(encoding="utf-8"))
        assert record["market"] == "KR"
        assert record["results"][0]["name"] == "macro"
        assert record["results"][0]["status"] == "ok"

    def test_missing_credentials_skips_source_without_failing(self, config, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError

        def needs_key(**kwargs):
            raise MissingCredentialsError("no key")

        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"fundamentals": needs_key, "macro": ok_collector("macro")},
        )
        by_name = {r.name: r for r in result.results}
        assert by_name["fundamentals"].status == "skipped"
        # A missing optional key is not a pipeline failure.
        assert result.ok

    def test_result_is_serializable(self, config):
        result = run_pipeline(
            config, market="KR", symbols=["005930"], collectors={"macro": ok_collector("macro")}
        )
        payload = json.dumps(result.to_dict())
        assert "macro" in payload


class TestCli:
    def test_parser_wires_data_pipeline(self):
        parser = build_parser()
        args = parser.parse_args(["data", "pipeline", "--market", "KR"])
        assert args.handler is cmd_data_pipeline
        assert args.market == "KR"

    def test_symbols_are_optional(self):
        parser = build_parser()
        args = parser.parse_args(["data", "pipeline", "--market", "KR"])
        assert args.symbols is None
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_pipeline.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.pipeline'`

- [ ] **Step 4: pipeline.py 구현**

`src/tradingbot/data/pipeline.py`:

```python
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from tradingbot.config import resolve_project_path
from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.fundamentals import update_fundamentals
from tradingbot.data.flows import update_flows
from tradingbot.data.macro import update_macro
from tradingbot.data.panel import PanelStore
from tradingbot.data.valuation import update_valuation
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceResult:
    name: str
    status: str
    rows: int
    message: str


@dataclass(frozen=True)
class PipelineResult:
    started_at: datetime
    finished_at: datetime
    market: str
    results: list[SourceResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Skipped optional sources are not failures; failed ones are."""
        return all(result.status != STATUS_FAILED for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "market": self.market,
            "ok": self.ok,
            "results": [asdict(result) for result in self.results],
        }

    def summary(self) -> str:
        parts = [f"{r.name}={r.status}({r.rows})" for r in self.results]
        return " ".join(parts)


def with_retry(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    no_retry: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Run `fn`, retrying transient failures with exponential backoff.

    `no_retry` names failures that retrying cannot fix — a missing API key is
    the same on the third attempt as the first, and retrying it only delays
    the rest of the batch."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except no_retry:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (2**attempt)
            LOGGER.warning("Attempt %s/%s failed (%s); retrying in %.1fs", attempt + 1, attempts, exc, delay)
            sleep(delay)
    assert last is not None
    raise last


def _default_collectors(
    processed_root: Path, symbols: Sequence[str], market: str, fundamental_years: int
) -> dict[str, Callable[..., int]]:
    def macro(**_: Any) -> int:
        return update_macro(PanelStore(processed_root, "macro", market))

    def flows(**_: Any) -> int:
        return update_flows(PanelStore(processed_root, "flows", market), symbols=symbols)

    def valuation(**_: Any) -> int:
        return update_valuation(PanelStore(processed_root, "valuation", market), symbols=symbols)

    def fundamentals(**_: Any) -> int:
        from tradingbot.data.fundamentals import dart_api_key

        dart_api_key()  # raises MissingApiKeyError -> reported as skipped
        this_year = date.today().year
        years = list(range(this_year - fundamental_years + 1, this_year + 1))
        return update_fundamentals(
            PanelStore(processed_root, "fundamentals", market),
            symbols=symbols,
            corp_codes={},
            years=years,
        )

    return {"macro": macro, "flows": flows, "valuation": valuation, "fundamentals": fundamentals}


def run_pipeline(
    config: dict[str, Any],
    *,
    market: str,
    symbols: Sequence[str] | None = None,
    processed_root: str | Path | None = None,
    log_root: str | Path | None = None,
    collectors: dict[str, Callable[..., int]] | None = None,
) -> PipelineResult:
    """Run every collector once, isolating failures.

    A source that raises is recorded as failed and the batch continues; the
    next run's incremental fetch picks up whatever it missed."""
    settings = config.get("pipeline", {})
    processed = Path(processed_root or settings.get("processed_dir", "data/processed"))
    if not processed.is_absolute():
        processed = resolve_project_path(processed)
    logs = Path(log_root or settings.get("log_dir", "state/pipeline_log"))
    if not logs.is_absolute():
        logs = resolve_project_path(logs)

    active_symbols = list(symbols) if symbols else list(settings.get("symbols", []))
    attempts = int(settings.get("retry_attempts", 3))
    active = collectors or _default_collectors(
        processed, active_symbols, market.upper(), int(settings.get("fundamental_years", 3))
    )

    started = datetime.now(timezone.utc)
    results: list[SourceResult] = []
    for name, collector in active.items():
        try:
            rows = with_retry(
                lambda c=collector: c(market=market, symbols=active_symbols),
                attempts=attempts,
                no_retry=(MissingCredentialsError,),
            )
            results.append(SourceResult(name, STATUS_OK, int(rows), ""))
            LOGGER.info("Pipeline source %s collected %s rows", name, rows)
        except MissingCredentialsError as exc:
            results.append(SourceResult(name, STATUS_SKIPPED, 0, str(exc)))
            LOGGER.warning("Pipeline source %s skipped: %s", name, exc)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            results.append(SourceResult(name, STATUS_FAILED, 0, str(exc)))
            LOGGER.exception("Pipeline source %s failed", name)

    result = PipelineResult(started, datetime.now(timezone.utc), market.upper(), results)
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{started:%Y%m%dT%H%M%S}_{market.upper()}.json"
    log_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
```

- [ ] **Step 5: CLI 구현**

`src/tradingbot/cli.py`의 `build_parser()`에서 `update_parser.set_defaults(handler=cmd_data_update)` 다음 줄에 추가:

```python
    pipeline_parser = data_subparsers.add_parser(
        "pipeline", help="Run the daily collection batch (prices, flows, valuation, macro, fundamentals)"
    )
    pipeline_parser.add_argument("--market", choices=["KR", "US"], required=True)
    pipeline_parser.add_argument(
        "--symbols", nargs="+", default=None, help="Override config pipeline.symbols"
    )
    pipeline_parser.add_argument("--processed-root", default=None)
    pipeline_parser.add_argument("--log-root", default=None)
    pipeline_parser.set_defaults(handler=cmd_data_pipeline)
```

`src/tradingbot/cli.py` 파일 끝에 핸들러 추가:

```python
def cmd_data_pipeline(args) -> int:
    from tradingbot.data.pipeline import run_pipeline

    config = load_config(args.config)
    result = run_pipeline(
        config,
        market=args.market,
        symbols=args.symbols,
        processed_root=args.processed_root,
        log_root=args.log_root,
    )

    print(f"데이터 수집 배치: {result.market}")
    for source in result.results:
        label = {"ok": "성공", "failed": "실패", "skipped": "생략"}.get(source.status, source.status)
        line = f"  - {source.name}: {label} ({source.rows}행)"
        if source.message:
            line += f" — {source.message}"
        print(line)
    print(f"전체 결과: {'정상' if result.ok else '일부 실패'}")
    return 0 if result.ok else 1
```

- [ ] **Step 6: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_pipeline.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (12 tests)

- [ ] **Step 7: 전체 회귀**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS (기존 154 + 신규 약 85)

- [ ] **Step 8: 배치 파일 작성**

`데이터 수집.bat` (저장소 루트). **REM 뒤에 한글을 쓰지 않는다**:

```bat
@echo off
REM Daily data collection batch for Windows Task Scheduler.
REM Schedule: weekdays 19:00 KST (after KRX close).
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 가상환경이 없습니다. 먼저 uv sync를 실행하세요.
    exit /b 1
)
".venv\Scripts\python.exe" -m tradingbot data pipeline --market KR
if errorlevel 1 (
    echo 일부 소스 수집에 실패했습니다. state\pipeline_log의 최신 JSON을 확인하세요.
    exit /b 1
)
echo 데이터 수집이 완료되었습니다.
exit /b 0
```

- [ ] **Step 9: 실데이터 스모크 (네트워크)**

```powershell
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR --symbols 005930
```

Expected: macro/flows/valuation은 `성공`, fundamentals는 DART 키 유무에 따라 `성공` 또는 `생략`. `state/pipeline_log/`에 JSON 실행 로그가 생성된다. 실패한 소스가 있으면 그 원인을 보고에 남긴다 (값 자체는 검증하지 않는다).

- [ ] **Step 10: 문서 갱신**

`README.md`의 "현재까지 반영된 확장" 목록에 추가:

```markdown
- 자동 데이터 파이프라인(`data/`): Point-in-Time 패널 저장소, KRX 수급·밸류에이션,
  DART 재무, 시장·거시 시리즈 수집과 품질 검사, `data pipeline` 일일 배치 — M7
```

`README.md`에 새 절 추가 (「데이터 업데이트」 절 바로 뒤):

```markdown
## 자동 데이터 수집 배치

가격 외 데이터(수급·밸류에이션·재무·거시)를 Point-in-Time 스키마로 수집합니다.
모든 레코드는 `available_at`(그 데이터를 실제로 알 수 있게 된 날)을 함께 저장하며,
백테스트는 그 날짜 이전의 데이터를 볼 수 없습니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR
```

수집 대상 종목은 `config/default.toml`의 `[pipeline] symbols`에서 바꿉니다.
재무 데이터는 [DART OpenAPI](https://opendart.fss.or.kr) 키가 필요하며, 환경변수
`DART_API_KEY`로 전달합니다. 키가 없으면 재무만 건너뛰고 나머지는 계속 수집합니다.

매일 자동 실행하려면 **`데이터 수집.bat`**을 Windows 작업 스케줄러에 평일 19:00으로
등록하세요. 실행 결과는 `state/pipeline_log/`에 JSON으로 남습니다.
```

`docs/architecture.md` §7 "주요 코드 위치" 표에 행 추가:

```markdown
| PIT 패널 저장소 | `src/tradingbot/data/panel.py` |
| 수급·밸류에이션·재무·거시 수집 | `src/tradingbot/data/{flows,valuation,fundamentals,macro}.py` |
| 데이터 품질 검사 / 일일 배치 | `src/tradingbot/data/quality.py`, `data/pipeline.py` |
```

- [ ] **Step 11: 커밋**

```powershell
git add src/tradingbot/data/pipeline.py src/tradingbot/cli.py config/default.toml "데이터 수집.bat" tests/test_data_pipeline.py README.md docs/architecture.md
git commit -m "M7: Add daily data pipeline batch with CLI and quality reporting"
```

---

## 완료 기준 (스펙 §3 Phase 2)

- [ ] `tradingbot data pipeline --market KR` 한 번으로 수급·밸류에이션·거시(+키가 있으면 재무)가 증분 수집된다.
- [ ] 모든 레코드가 `available_at`을 갖고, `PanelStore.read(as_of=...)`가 그 날짜 이후 데이터를 숨긴다.
- [ ] 소스 하나가 실패해도 나머지는 수집되고, 실패가 로그와 결과 JSON에 남으며 종료코드가 1이 된다.
- [ ] `데이터 수집.bat`을 작업 스케줄러에 등록하면 매일 자동 실행된다.
- [ ] 전체 테스트가 통과하고 기존 회귀 테스트가 깨지지 않는다.

## 스펙과의 의도적 차이

스펙 §4.3은 "PER/PBR 등 파생 지표는 processed 단계에서 가격·주식수와 결합해 계산"이라고
했으나, 이 계획은 **pykrx가 제공하는 KRX 공식 일별 투자지표를 그대로 수집**한다
(`data/valuation.py`). 이유: KRX는 매 거래일 그 시점에 공시된 최신 재무를 기준으로
PER/PBR/EPS/BPS를 발표하므로 **관측 자체가 Point-in-Time**이다. 우리가 재무와 주식수로
직접 계산하면 재무 정정(restatement)이 과거 값에 소급 반영되어 오히려 미래 정보가
새어들 위험이 있다. DART 재무(`fundamentals.py`)는 성장률·수익성 팩터용 원자료로
별도 수집하며, 두 소스는 서로를 대체하지 않는다.

## 알려진 한계 (Phase 3에서 해소)

- `update_fundamentals`는 `corp_codes` 매핑을 인자로 받는다. DART corpCode.xml 다운로드·파싱은 Phase 3에서 종목 유니버스 작업과 함께 붙인다. 그전까지 파이프라인의 재무 수집은 빈 매핑으로 호출되어 실질적으로 아무것도 수집하지 않으며, 이는 의도된 단계적 도입이다.
- `data pipeline`은 아직 가격(OHLCV) 캐시를 갱신하지 않는다. 기존 `data update`가 그 역할을 하며, Phase 3에서 유니버스 기반으로 통합한다.
- 품질 검사(`quality.py`)는 이번 태스크에서 파이프라인에 자동 연결되지 않는다. Phase 3에서 수집 직후 검사·격리 단계로 편입한다.
