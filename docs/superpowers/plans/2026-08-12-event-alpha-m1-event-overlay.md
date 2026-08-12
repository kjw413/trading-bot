# Event Alpha M1 — 이벤트 캘린더 + 이벤트 리스크 오버레이 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실적 발표일을 Point-in-Time으로 수집하고, 발표를 앞둔 종목의 비중을 줄이는 오버레이를 기존 전략에 붙여, `research evaluate`로 그 효과만 분리해 잴 수 있게 한다.

**Architecture:** 새 예측 모델을 만들지 않는다. 이벤트 **날짜**만 쓴다. 수집기는 `flows.py` 패턴, 저장은 기존 `PanelStore`, 측정은 기존 `research evaluate`의 두 설정 비교. 추가되는 것은 순수 함수 두 개(다음 이벤트 추정, 비중 축소)와 전략의 일별 경로 하나다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-08-12-event-alpha-design.md`
**배경:** `docs/event_alpha_design_review.md` §8.3(이벤트 날짜 결함), §8.4(오버레이), §8.8(첫 삽)

## Global Constraints

- **신규 의존성 추가 금지.**
- **오버레이가 꺼져 있으면 기존 백테스트 결과가 한 자릿수까지 동일해야 한다.** 이것이 깨지면 이후 모든 비교가 오염된다. `tests/test_smoke_backtest.py`와 `tests/test_theme_multifactor_backtest.py`가 회귀 방지선이다.
- **정기보고서 접수일을 이벤트일로 쓰지 않는다.** 잠정실적 공시가 있으면 그것이 이벤트다 (스펙 §3, §6.2).
- **추정 불가는 "이벤트 없음"이 아니다.** 다음 이벤트일을 모르면 오버레이는 아무것도 하지 않는다. None을 0이나 큰 수로 대체하지 않는다.
- **미래 정보 금지.** 다음 이벤트일은 `available_at <= dt`인 과거 이벤트만으로 추정한다.
- **파라미터 탐색 금지.** `window_days`와 `scale`은 처음 정한 값(5, 0.5)을 쓰고 in-sample에서 스윕하지 않는다.
- 테스트에서 네트워크 접근 금지 (canned DART 응답 + 합성 가격 fixture만).
- 기존 CLI 명령 동작 변경 금지.
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 접두사: 중간 `EVENT(part):`, 마지막 `EVENT:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `Disclosure` (`data/fundamentals.py`, frozen): `rcept_no: str`, `report_name: str`, `rcept_dt: date`
- `DartClient.disclosure_list(corp_code: str, start: date, end: date) -> list[Disclosure]` — 페이지네이션 처리됨
- `DartClient(api_key, transport, base_url=...)`; `Transport = Callable[[str, dict], dict]`
- `build_client() -> DartClient` (`data/fundamentals_panel.py`) — 키 없으면 `MissingApiKeyError`
- `MissingApiKeyError` (`data/fundamentals_panel.py`) — `MissingCredentialsError` 하위. 파이프라인이 `skipped`로 기록
- `PanelStore(root, dataset, market)` (`data/panel.py`): `.append(frame) -> int` (변경 행수), `.read(*, as_of=None, start=None, end=None, symbols=None) -> pd.DataFrame`, `.last_date(symbol=None) -> date | None`
- `attach_metadata(frame, *, source, available_at, data_version, ingested_at=None) -> pd.DataFrame` — `date`·`symbol` 컬럼 필수
- `next_trading_day_availability(dates: pd.Series, market: str) -> pd.Series`
- `PANEL_KEY_COLUMNS = ["date", "symbol"]` — 같은 키의 행은 마지막만 남는다
- `CorpCodeStore.corp_code_for(symbols, *, fetch=True) -> dict[str, str]` (`data/corp_codes.py`)
- `ParquetDataStore.panel(dataset, as_of, symbols=None, *, start=None) -> pd.DataFrame` (`data/store.py`)
- `get_calendar(market) -> ExchangeCalendar` (`engine/calendar.py`): `.next_trading_day(day)`, `.trading_days(start, end) -> list[date]`
- `Strategy` (`strategies/base.py`): `snapshot_state() -> dict`, `restore_state(state: dict)`, `persist_state()`
- `StrategyContext`: `.position(symbol) -> Position`, `.equity() -> float`, `.sell(symbol, qty, ...) -> Order`
- `Position` (`models.py`): `.qty: int`, `.market_value: float`
- `SignalLedger(name, state_store)` + `make_signal_id(strategy, dt, symbol, side, weight)` (`strategies/signals.py`)
- `COLLECTOR_MARKETS` (`data/pipeline.py`): 수집기명 → 적용 시장 튜플
- `[strategies.theme_multifactor]` TOML 키가 전략 생성자 파라미터로 그대로 전달된다 (`services.build_strategy`)

---

### Task 1: 공시 분류와 이벤트 패널 수집

**Files:**
- Create: `src/tradingbot/data/events.py`
- Test: `tests/test_data_events.py`

**Interfaces:**
- Consumes: `Disclosure`, `DartClient`, `PanelStore`, `attach_metadata`, `next_trading_day_availability` (기존)
- Produces:
  - `events.classify_report(report_name: str) -> str | None` — `"provisional"` / `"periodic"` / `None`
  - `events.disclosures_to_events(disclosures: Sequence[Disclosure], symbol: str) -> pd.DataFrame`
  - `events.update_events(store, *, symbols, corp_codes, start, end, fetcher=None) -> int`
  - 상수: `EVENT_COLUMNS`, `EVENTS_SOURCE`, `EVENTS_DATA_VERSION`, `EVENTS_DEFAULT_START`

**설계 근거:** 분류를 순수 함수로 분리해야 "어느 공시가 이벤트인가"를 네트워크 없이 실제 DART 보고서명 문자열로 검증할 수 있다. 이 판정이 틀리면 이후 전부가 무효다(스펙 §3).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_events.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.events import (
    EVENT_COLUMNS,
    classify_report,
    disclosures_to_events,
    update_events,
)
from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.panel import PanelStore


class TestClassifyReport:
    @pytest.mark.parametrize(
        "name",
        [
            "연결재무제표기준영업(잠정)실적(공정공시)",
            "영업(잠정)실적(공정공시)",
            "매출액또는손익구조30%(대규모법인은15%)이상변동",
        ],
    )
    def test_provisional_results_are_the_event(self, name):
        assert classify_report(name) == "provisional"

    @pytest.mark.parametrize("name", ["분기보고서 (2024.03)", "반기보고서 (2024.06)", "사업보고서 (2023.12)"])
    def test_periodic_reports_are_the_fallback(self, name):
        assert classify_report(name) == "periodic"

    @pytest.mark.parametrize(
        "name",
        ["주요사항보고서(유상증자결정)", "임원ㆍ주요주주특정증권등소유상황보고서", "기업설명회(IR)개최(안내공시)"],
    )
    def test_unrelated_filings_are_not_events(self, name):
        assert classify_report(name) is None

    def test_whitespace_and_prefixes_do_not_break_matching(self):
        # DART prefixes filings with the filer type, e.g. "[기재정정]".
        assert classify_report("  [기재정정]연결재무제표기준영업(잠정)실적(공정공시) ") == "provisional"

    def test_empty_name_is_not_an_event(self):
        assert classify_report("") is None


def disclosure(name: str, day: str, rcept_no: str = "1") -> Disclosure:
    return Disclosure(rcept_no=rcept_no, report_name=name, rcept_dt=date.fromisoformat(day))


class TestDisclosuresToEvents:
    def test_keeps_only_earnings_filings(self):
        frame = disclosures_to_events(
            [
                disclosure("연결재무제표기준영업(잠정)실적(공정공시)", "2024-01-09", "a"),
                disclosure("주요사항보고서(유상증자결정)", "2024-01-15", "b"),
                disclosure("사업보고서 (2023.12)", "2024-03-11", "c"),
            ],
            "005930",
        )
        assert list(frame["event_kind"]) == ["provisional", "periodic"]
        assert list(frame.columns) == EVENT_COLUMNS

    def test_event_date_is_the_receipt_date(self):
        frame = disclosures_to_events(
            [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], "005930"
        )
        assert frame["date"].iloc[0] == pd.Timestamp("2024-01-09")

    def test_symbol_is_upper_cased(self):
        frame = disclosures_to_events(
            [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], "005930"
        )
        assert frame["symbol"].iloc[0] == "005930"

    def test_provisional_wins_when_both_land_on_one_day(self):
        # PanelStore keys on (date, symbol), so two filings on one day collapse
        # to one row. The provisional release is the market event; keeping the
        # periodic report instead would reintroduce the very error this module
        # exists to fix.
        frame = disclosures_to_events(
            [
                disclosure("사업보고서 (2023.12)", "2024-03-11", "c"),
                disclosure("연결재무제표기준영업(잠정)실적(공정공시)", "2024-03-11", "d"),
            ],
            "005930",
        )
        assert len(frame) == 1
        assert frame["event_kind"].iloc[0] == "provisional"

    def test_sorted_by_date(self):
        frame = disclosures_to_events(
            [
                disclosure("영업(잠정)실적(공정공시)", "2024-04-05", "b"),
                disclosure("영업(잠정)실적(공정공시)", "2024-01-09", "a"),
            ],
            "005930",
        )
        assert list(frame["date"]) == [pd.Timestamp("2024-01-09"), pd.Timestamp("2024-04-05")]

    def test_no_earnings_filings_gives_empty_frame_with_schema(self):
        frame = disclosures_to_events([disclosure("주요사항보고서(유상증자결정)", "2024-01-15")], "005930")
        assert frame.empty
        assert list(frame.columns) == EVENT_COLUMNS

    def test_empty_input_gives_empty_frame_with_schema(self):
        frame = disclosures_to_events([], "005930")
        assert frame.empty
        assert list(frame.columns) == EVENT_COLUMNS


class TestUpdateEvents:
    def test_writes_rows_with_availability_on_the_next_trading_day(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            # 2024-01-06 is a Saturday; the next KRX trading day is Monday.
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-06")], symbol
            )

        written = update_events(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1
        panel = store.read()
        assert panel["available_at"].iloc[0] == pd.Timestamp("2024-01-08")
        assert panel["source"].iloc[0] == "dart"

    def test_recollecting_unchanged_data_writes_nothing(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        kwargs = dict(
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert update_events(store, **kwargs) == 1
        assert update_events(store, **kwargs) == 0

    def test_a_symbol_without_a_corp_code_is_skipped_not_fatal(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        written = update_events(
            store,
            symbols=["005930", "999999"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1

    def test_one_failing_symbol_does_not_abort_the_batch(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            if symbol == "000660":
                raise RuntimeError("DART timeout")
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        written = update_events(
            store,
            symbols=["000660", "005930"],
            corp_codes={"000660": "00164779", "005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_events.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.events'`

- [ ] **Step 3: 구현**

`src/tradingbot/data/events.py`:

```python
"""Earnings event calendar from DART disclosures.

The event is the day the market learned the numbers, which is *not* the day
the periodic report was filed. Korean issuers publish provisional results
through a fair-disclosure filing weeks before the quarterly or annual report
carries the same figures: Samsung's Q4 provisional release lands in early
January, the annual report in March. The price moves in January.

Dating events by the periodic report would measure pre-event state after the
move already happened and measure the reaction on a quiet day — and it would
do so silently, producing plausible numbers the whole way. That is why the
classification lives in a pure function with the real DART report names in
its tests.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

EVENTS_DATA_VERSION = "1"
EVENTS_SOURCE = "dart"
EVENTS_DEFAULT_START = date(2015, 1, 1)

EVENT_COLUMNS = ["date", "symbol", "event_kind", "report_name", "rcept_no"]

# The provisional release is the market event. Ordered by priority: when two
# filings share a day, the earlier entry wins (see `_KIND_PRIORITY`).
_PROVISIONAL_MARKERS = ("영업(잠정)실적", "매출액또는손익구조")
_PERIODIC_MARKERS = ("분기보고서", "반기보고서", "사업보고서")

_KIND_PRIORITY = {"provisional": 0, "periodic": 1}

# How far back to look when a symbol has no events yet.
_LOOKBACK_ON_EMPTY = EVENTS_DEFAULT_START


def classify_report(report_name: str) -> str | None:
    """Which kind of earnings event this filing is, or None if it is not one.

    Matching is on substrings because DART prefixes filings with tags like
    `[기재정정]` (amended) and suffixes them with periods and filer names.
    An amended provisional release is still a provisional release.
    """
    name = (report_name or "").strip()
    if not name:
        return None
    if any(marker in name for marker in _PROVISIONAL_MARKERS):
        return "provisional"
    if any(marker in name for marker in _PERIODIC_MARKERS):
        return "periodic"
    return None


def disclosures_to_events(disclosures: Sequence[Disclosure], symbol: str) -> pd.DataFrame:
    """Filter disclosures down to earnings events, one row per (date, symbol).

    `PanelStore` keys on (date, symbol), so two filings received the same day
    would collapse to whichever was written last. That is decided here instead
    of by accident: the provisional release wins.
    """
    rows = []
    for item in disclosures:
        kind = classify_report(item.report_name)
        if kind is None:
            continue
        rows.append(
            {
                "date": pd.Timestamp(item.rcept_dt),
                "symbol": str(symbol).upper(),
                "event_kind": kind,
                "report_name": item.report_name,
                "rcept_no": item.rcept_no,
            }
        )
    if not rows:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    frame["_priority"] = frame["event_kind"].map(_KIND_PRIORITY)
    frame = (
        frame.sort_values(["date", "_priority"])
        .drop_duplicates(subset=["date", "symbol"], keep="first")
        .drop(columns="_priority")
        .reset_index(drop=True)
    )
    return frame


def fetch_events(corp_code: str, start: date, end: date, symbol: str) -> pd.DataFrame:
    """Real network fetch: one company's earnings filings in a date range."""
    from tradingbot.data.fundamentals_panel import build_client

    client = build_client()
    return disclosures_to_events(client.disclosure_list(corp_code, start, end), symbol)


def update_events(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    corp_codes: dict[str, str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_events,
) -> int:
    """Incrementally collect earnings events for each symbol.

    A symbol without a corp_code, or one whose fetch fails, is logged and
    skipped so a single bad company cannot abort the batch. A missing API key
    is a batch-level configuration problem and propagates.
    """
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        corp_code = corp_codes.get(str(symbol).upper()) or corp_codes.get(str(symbol))
        if not corp_code:
            LOGGER.warning("No DART corp_code for %s; skipping", symbol)
            continue

        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or _LOOKBACK_ON_EMPTY)
        if fetch_start > fetch_end:
            continue

        try:
            frame = fetcher(corp_code, fetch_start, fetch_end, symbol)
        except MissingCredentialsError:
            raise
        except Exception:
            LOGGER.exception("Event collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue

        tagged = attach_metadata(
            frame,
            source=EVENTS_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=EVENTS_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_events.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (17 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/data/events.py tests/test_data_events.py
git commit -m "EVENT(part): Collect earnings events from DART provisional filings"
```

---

### Task 2: 다음 이벤트까지 남은 일수 추정

**Files:**
- Create: `src/tradingbot/research/event_calendar.py`
- Test: `tests/test_research_event_calendar.py`

**Interfaces:**
- Consumes: 없음 (순수 함수, pandas 불필요)
- Produces:
  - `event_calendar.days_to_next_event(event_dates: Sequence[date], as_of: date) -> int | None`
  - 상수: `MIN_EVENTS_FOR_ESTIMATE = 4`

**설계 근거:** 미래 발표일을 지금 지식으로 채우면 look-ahead다. 과거 이벤트 간격의 중앙값만 쓴다. **추정 불가는 None이고, None은 "이벤트 없음"이 아니라 "모른다"이며 오버레이는 아무것도 하지 않는다.**

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_event_calendar.py`:

```python
from __future__ import annotations

from datetime import date

from tradingbot.research.event_calendar import MIN_EVENTS_FOR_ESTIMATE, days_to_next_event

# Four quarterly events, roughly 91 days apart.
QUARTERLY = [date(2023, 1, 9), date(2023, 4, 10), date(2023, 7, 10), date(2023, 10, 10)]


class TestDaysToNextEvent:
    def test_estimates_from_the_median_gap(self):
        # Last event 2023-10-10, median gap ~91 days -> around 2024-01-09.
        # Asking on 2023-12-10 leaves roughly a month.
        days = days_to_next_event(QUARTERLY, date(2023, 12, 10))
        assert days is not None
        assert 25 <= days <= 35

    def test_zero_when_the_estimate_has_just_passed(self):
        # An overdue quarterly report means the release is due now, not that
        # there is no event.
        assert days_to_next_event(QUARTERLY, date(2024, 1, 15)) == 0

    def test_none_when_the_estimate_is_long_overdue(self):
        # More than one median gap past the estimate means collection stopped,
        # not that an announcement is imminent. Without this guard a dead
        # pipeline would hold every position permanently reduced.
        assert days_to_next_event(QUARTERLY, date(2024, 6, 1)) is None

    def test_none_when_there_are_too_few_events(self):
        assert days_to_next_event(QUARTERLY[: MIN_EVENTS_FOR_ESTIMATE - 1], date(2023, 12, 10)) is None

    def test_none_for_no_events(self):
        assert days_to_next_event([], date(2023, 12, 10)) is None

    def test_ignores_events_after_as_of(self):
        # A future-dated row must never influence the estimate — that is the
        # look-ahead this function exists to prevent.
        with_future = QUARTERLY + [date(2024, 1, 9)]
        assert days_to_next_event(with_future, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_unsorted_input_gives_the_same_answer(self):
        shuffled = [QUARTERLY[2], QUARTERLY[0], QUARTERLY[3], QUARTERLY[1]]
        assert days_to_next_event(shuffled, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_duplicate_dates_do_not_create_a_zero_gap(self):
        # A zero gap would drag the median down and make every symbol look
        # permanently about to report.
        doubled = QUARTERLY + [QUARTERLY[1]]
        assert days_to_next_event(doubled, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_semiannual_reporter_gets_a_wider_estimate(self):
        semiannual = [date(2022, 3, 1), date(2022, 9, 1), date(2023, 3, 1), date(2023, 9, 1)]
        days = days_to_next_event(semiannual, date(2023, 12, 1))
        assert days is not None
        assert 80 <= days <= 100

    def test_as_of_on_the_estimated_day_is_zero(self):
        # 2023-10-10 + 91 days = 2024-01-09.
        assert days_to_next_event(QUARTERLY, date(2024, 1, 9)) == 0
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_event_calendar.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.event_calendar'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/event_calendar.py`:

```python
"""Estimate when a company will next report, using only what was knowable.

The next announcement date is not something the bot may look up: filling it
from present-day knowledge is exactly the look-ahead the point-in-time layer
exists to prevent. So it is extrapolated from the spacing of past events —
the median gap between consecutive announcements, which is about a quarter
for most filers and about half a year for semiannual ones.

Returning None means "unknown", never "no event". Callers must not substitute
a number for it; an unknown schedule is a reason to leave a position alone.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Sequence

# Three gaps is the fewest that gives a median with any resistance to one
# irregular filing. Below this the estimate is noise wearing a number.
MIN_EVENTS_FOR_ESTIMATE = 4


def days_to_next_event(event_dates: Sequence[date], as_of: date) -> int | None:
    """Calendar days until this company is next expected to report.

    Returns 0 when the estimate has already passed but not by much — an
    overdue quarterly report means the release is due, not absent.

    Returns None when the schedule cannot be estimated: too few past events,
    or an estimate more than one median gap in the past. That second case is
    a stalled collection job rather than an imminent announcement, and
    treating it as imminent would hold every position reduced indefinitely.
    """
    past = sorted({day for day in event_dates if day <= as_of})
    if len(past) < MIN_EVENTS_FOR_ESTIMATE:
        return None

    gaps = [(later - earlier).days for earlier, later in zip(past, past[1:])]
    median_gap = statistics.median(gaps)
    if median_gap <= 0:
        return None

    estimate = past[-1] + timedelta(days=round(median_gap))
    if estimate >= as_of:
        return (estimate - as_of).days
    if (as_of - estimate).days <= round(median_gap):
        return 0
    return None
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_event_calendar.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/event_calendar.py tests/test_research_event_calendar.py
git commit -m "EVENT(part): Estimate the next report date from past event spacing"
```

---

### Task 3: 오버레이 순수 함수

**Files:**
- Create: `src/tradingbot/allocation/event_overlay.py`
- Test: `tests/test_allocation_event_overlay.py`

**Interfaces:**
- Consumes: 없음 (순수 dict 변환)
- Produces:
  - `event_overlay.reduce_for_events(weights: dict[str, float], *, days_to_event: dict[str, int | None], window_days: int, scale: float) -> dict[str, float]`

**설계 근거:** `apply_constraints`와 같은 자리, 같은 원칙이다 — 줄어든 비중은 현금으로 가고 다른 종목에 재분배되지 않는다. 축소만 하므로 이미 성립한 집중도·현금버퍼 제약을 깨뜨릴 수 없고, 따라서 `apply_constraints` **뒤**에 적용해도 안전하다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_allocation_event_overlay.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.allocation.event_overlay import reduce_for_events

WEIGHTS = {"AAA": 0.4, "BBB": 0.3, "CCC": 0.2}


def reduced(days: dict[str, int | None], window_days: int = 5, scale: float = 0.5):
    return reduce_for_events(WEIGHTS, days_to_event=days, window_days=window_days, scale=scale)


class TestReduceForEvents:
    def test_a_name_inside_the_window_is_scaled_down(self):
        assert reduced({"AAA": 3})["AAA"] == pytest.approx(0.2)

    def test_a_name_outside_the_window_is_untouched(self):
        assert reduced({"AAA": 30})["AAA"] == pytest.approx(0.4)

    def test_the_boundary_day_is_inside_the_window(self):
        assert reduced({"AAA": 5})["AAA"] == pytest.approx(0.2)

    def test_the_day_after_the_boundary_is_outside(self):
        assert reduced({"AAA": 6})["AAA"] == pytest.approx(0.4)

    def test_the_event_day_itself_is_inside(self):
        assert reduced({"AAA": 0})["AAA"] == pytest.approx(0.2)

    def test_unknown_schedule_leaves_the_weight_alone(self):
        # None means "we don't know", which is not a reason to trade.
        assert reduced({"AAA": None})["AAA"] == pytest.approx(0.4)

    def test_a_missing_symbol_leaves_the_weight_alone(self):
        assert reduced({})["AAA"] == pytest.approx(0.4)

    def test_freed_weight_goes_to_cash_not_to_other_names(self):
        # Redistributing would relocate the exposure this overlay exists to
        # remove — the same rule apply_constraints follows for its cap.
        result = reduced({"AAA": 1})
        assert result["BBB"] == pytest.approx(0.3)
        assert result["CCC"] == pytest.approx(0.2)
        assert sum(result.values()) == pytest.approx(0.7)

    def test_scales_every_affected_name(self):
        result = reduced({"AAA": 1, "BBB": 2})
        assert result["AAA"] == pytest.approx(0.2)
        assert result["BBB"] == pytest.approx(0.15)

    def test_scale_of_one_is_a_no_op(self):
        assert reduce_for_events(
            WEIGHTS, days_to_event={"AAA": 1}, window_days=5, scale=1.0
        ) == pytest.approx(WEIGHTS)

    def test_window_of_zero_still_catches_the_event_day(self):
        result = reduce_for_events(WEIGHTS, days_to_event={"AAA": 0}, window_days=0, scale=0.5)
        assert result["AAA"] == pytest.approx(0.2)

    def test_negative_window_disables_the_overlay(self):
        result = reduce_for_events(WEIGHTS, days_to_event={"AAA": 0}, window_days=-1, scale=0.5)
        assert result == pytest.approx(WEIGHTS)

    def test_empty_weights_stay_empty(self):
        assert reduce_for_events({}, days_to_event={"AAA": 1}, window_days=5, scale=0.5) == {}

    def test_does_not_mutate_the_input(self):
        original = dict(WEIGHTS)
        reduced({"AAA": 1})
        assert WEIGHTS == original

    @pytest.mark.parametrize("bad_scale", [-0.1, 1.1])
    def test_scale_outside_zero_to_one_is_rejected(self, bad_scale):
        # A scale above 1 would make this an entry signal, which it is not.
        with pytest.raises(ValueError):
            reduce_for_events(WEIGHTS, days_to_event={}, window_days=5, scale=bad_scale)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_event_overlay.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.allocation.event_overlay'`

- [ ] **Step 3: 구현**

`src/tradingbot/allocation/event_overlay.py`:

```python
"""Reduce exposure to names that are about to report.

One rule: a name whose next expected announcement falls inside the window is
scaled down, and the freed weight becomes cash. It is never handed to the
other names — redistributing would relocate the exposure this overlay exists
to remove, the same reason `apply_constraints` sends capped excess to cash.

This only ever reduces, so it cannot break a concentration cap or a cash
buffer that already held. That is what makes it safe to apply after
`apply_constraints` rather than before.
"""

from __future__ import annotations


def reduce_for_events(
    weights: dict[str, float],
    *,
    days_to_event: dict[str, int | None],
    window_days: int,
    scale: float,
) -> dict[str, float]:
    """Scale down weights for names reporting within `window_days`.

    A symbol whose schedule is unknown (None, or absent from the mapping) is
    left alone. Unknown is not the same as "no event coming", and guessing
    would trade on nothing.

    A negative `window_days` disables the overlay entirely, matching how
    `abs_momentum_ma_days = 0` disables its filter.
    """
    if not 0.0 <= scale <= 1.0:
        raise ValueError("scale must be in [0, 1]")
    if window_days < 0 or not weights:
        return dict(weights)

    adjusted = {}
    for symbol, weight in weights.items():
        days = days_to_event.get(symbol)
        if days is not None and 0 <= days <= window_days:
            adjusted[symbol] = weight * scale
        else:
            adjusted[symbol] = weight
    return adjusted
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_allocation_event_overlay.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (16 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/allocation/event_overlay.py tests/test_allocation_event_overlay.py
git commit -m "EVENT(part): Add the pre-announcement weight reduction rule"
```

---

### Task 4: 전략 통합 — 일별 축소 경로

**Files:**
- Modify: `src/tradingbot/strategies/theme_multifactor.py`
- Test: `tests/test_theme_multifactor_event_overlay.py`

**Interfaces:**
- Consumes: `days_to_next_event` (Task 2), `reduce_for_events` (Task 3), `ParquetDataStore.panel` (기존), `SignalLedger`·`make_signal_id` (기존)
- Produces:
  - `default_params`에 `event_overlay_window_days: -1` (기본 비활성), `event_overlay_scale: 0.5` 추가
  - `ThemeMultifactorStrategy._event_days_to_next(dt, symbols, data_store) -> dict[str, int | None]`
  - `ThemeMultifactorStrategy._apply_event_trims(ctx, dt) -> None` — 매일 호출
  - `snapshot_state()`에 `event_trims: dict[symbol, ISO date]` 추가

**설계 근거 (스펙 §6.5):** 오버레이는 `is_rebalance_date` 게이트 **앞**에서 매일 평가한다. 월말에만 보면 월 초 이벤트를 놓친다. 같은 이벤트에 반복 축소하지 않도록, 종목별로 "어느 추정 이벤트일 때문에 축소했는지"를 상태에 남긴다.

**기본값이 비활성인 이유:** 이 태스크만으로 기존 결과가 달라지면 안 된다. 활성화는 Task 5의 새 설정 파일에서만 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_theme_multifactor_event_overlay.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.models import Bar, Position
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

EVENTS = pd.DataFrame(
    {
        "date": pd.to_datetime(
            ["2023-01-09", "2023-04-10", "2023-07-10", "2023-10-10"]
        ),
        "symbol": ["005930"] * 4,
        "event_kind": ["provisional"] * 4,
    }
)


class FakeStore:
    """Panel-only store: the overlay path never reads prices."""

    market = "KR"

    def __init__(self, events: pd.DataFrame = EVENTS) -> None:
        self.events = events
        self.queried_as_of: list[date] = []

    def panel(self, dataset, as_of, symbols=None, *, start=None):
        assert dataset == "events"
        self.queried_as_of.append(as_of)
        frame = self.events
        if symbols is not None:
            frame = frame[frame["symbol"].isin({str(s).upper() for s in symbols})]
        return frame.reset_index(drop=True)


class FakeContext:
    def __init__(self, positions: dict[str, int]) -> None:
        self._positions = positions
        self.sells: list[tuple[str, int]] = []

    def position(self, symbol):
        return Position(symbol=symbol, qty=self._positions.get(symbol, 0), avg_price=100.0)

    def equity(self):
        return 1_000_000.0

    def sell(self, symbol, qty, **kwargs):
        self.sells.append((symbol, qty))
        return None

    def buy(self, symbol, **kwargs):  # pragma: no cover - overlay never buys
        raise AssertionError("the event overlay must never buy")


def strategy(**overrides) -> ThemeMultifactorStrategy:
    params = {
        "theme": "ai_semiconductor",
        "market": "KR",
        "event_overlay_window_days": 5,
        "event_overlay_scale": 0.5,
    }
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


class TestEventDaysToNext:
    def test_reads_the_events_panel_as_of_the_date(self):
        store = FakeStore()
        days = strategy()._event_days_to_next(date(2024, 1, 5), ["005930"], store)
        assert store.queried_as_of == [date(2024, 1, 5)]
        assert days["005930"] is not None

    def test_a_symbol_with_no_events_is_unknown(self):
        days = strategy()._event_days_to_next(date(2024, 1, 5), ["000660"], FakeStore())
        assert days["000660"] is None

    def test_an_empty_panel_makes_every_symbol_unknown(self):
        empty = pd.DataFrame(columns=["date", "symbol", "event_kind"])
        days = strategy()._event_days_to_next(date(2024, 1, 5), ["005930"], FakeStore(empty))
        assert days == {"005930": None}


class TestApplyEventTrims:
    def test_trims_a_held_name_inside_the_window(self):
        # Estimated next event 2024-01-09; on 2024-01-05 that is 4 days out.
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == [("005930", 50)]

    def test_does_not_trim_outside_the_window(self):
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2023, 12, 1))
        assert ctx.sells == []

    def test_does_not_trim_the_same_event_twice(self):
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        ctx._positions["005930"] = 50
        strat._apply_event_trims(ctx, date(2024, 1, 8))
        assert ctx.sells == [("005930", 50)]

    def test_does_nothing_without_a_position(self):
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_a_position_too_small_to_halve_is_left_alone(self):
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 1})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_disabled_by_default(self):
        # The default must not change any existing backtest.
        strat = ThemeMultifactorStrategy(theme="ai_semiconductor", market="KR")
        assert strat.params["event_overlay_window_days"] < 0
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_missing_events_panel_trims_nothing(self):
        empty = pd.DataFrame(columns=["date", "symbol", "event_kind"])
        strat = strategy()
        strat._data_store = FakeStore(empty)
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_an_order_failure_does_not_stop_the_other_symbols(self):
        class Failing(FakeContext):
            def sell(self, symbol, qty, **kwargs):
                if symbol == "005930":
                    raise RuntimeError("broker rejected")
                return super().sell(symbol, qty, **kwargs)

        events = pd.concat(
            [EVENTS, EVENTS.assign(symbol="000660")], ignore_index=True
        )
        strat = strategy()
        strat._data_store = FakeStore(events)
        ctx = Failing({"005930": 100, "000660": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == [("000660", 50)]


class TestStatePersistence:
    def test_trims_survive_a_restart(self):
        strat = strategy()
        strat._data_store = FakeStore()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))

        restored = strategy()
        restored._data_store = FakeStore()
        restored.restore_state(strat.snapshot_state())
        ctx2 = FakeContext({"005930": 50})
        restored._apply_event_trims(ctx2, date(2024, 1, 8))
        assert ctx2.sells == []

    def test_snapshot_keeps_the_existing_keys(self):
        state = strategy().snapshot_state()
        assert {"last_seen_date", "last_rebalance_date", "last_targets"} <= set(state)
        assert "event_trims" in state


class TestSignalIdIsolation:
    def test_an_event_trim_does_not_block_a_rebalance_sell(self):
        # Both paths can sell the same symbol on the same day. If they shared
        # a signal id the ledger would treat the second as already handled and
        # drop it, silently losing a rebalance order.
        from tradingbot.strategies.signals import make_signal_id

        trim_id = make_signal_id("theme_multifactor:event_trim", date(2024, 1, 5), "005930", "SELL", 0.5)
        rebalance_id = make_signal_id("theme_multifactor", date(2024, 1, 5), "005930", "SELL", 0.5)
        assert trim_id != rebalance_id


class TestOnBarWiring:
    def test_trims_run_on_a_non_rebalance_day(self, monkeypatch):
        # The whole point of the daily path: an event early in the month must
        # not wait for month-end.
        strat = strategy()
        strat._data_store = FakeStore()
        called: list[date] = []
        monkeypatch.setattr(
            strat, "_apply_event_trims", lambda ctx, dt: called.append(dt)
        )
        ctx = FakeContext({"005930": 100})
        strat.on_bar(ctx, Bar(symbol="005930", dt=date(2024, 1, 5),
                              open=1.0, high=1.0, low=1.0, close=1.0, volume=1))
        assert called == [date(2024, 1, 5)]

    def test_trims_run_once_per_day_not_once_per_symbol(self, monkeypatch):
        strat = strategy()
        strat._data_store = FakeStore()
        called: list[date] = []
        monkeypatch.setattr(
            strat, "_apply_event_trims", lambda ctx, dt: called.append(dt)
        )
        ctx = FakeContext({"005930": 100})
        for symbol in ("005930", "000660", "042700"):
            strat.on_bar(ctx, Bar(symbol=symbol, dt=date(2024, 1, 5),
                                  open=1.0, high=1.0, low=1.0, close=1.0, volume=1))
        assert called == [date(2024, 1, 5)]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_event_overlay.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `AttributeError: 'ThemeMultifactorStrategy' object has no attribute '_event_days_to_next'`

`Bar`와 `Position`의 실제 생성자 시그니처를 `src/tradingbot/models.py`에서 확인하고, 위 테스트의 인자를 실제 필드에 맞춘다. 필드명이 다르면 **테스트를 실제 모델에 맞춰 고친다** (모델을 바꾸지 않는다).

- [ ] **Step 3: 구현**

`src/tradingbot/strategies/theme_multifactor.py` 상단 임포트에 추가:

```python
from tradingbot.allocation.event_overlay import reduce_for_events  # noqa: F401  (Task 5에서 사용)
from tradingbot.research.event_calendar import days_to_next_event
```

`default_params`에 추가 (`abs_momentum_ma_days` 아래):

```python
        # 실적 발표를 앞둔 종목의 보유 수량을 줄인다. 음수면 비활성(기본).
        # 발표 후 복원은 다음 정기 리밸런싱에 맡긴다 — 리밸런싱 밖에서 매수
        # 경로를 새로 만들지 않기 위해서다.
        "event_overlay_window_days": -1,
        "event_overlay_scale": 0.5,
```

`__init__`의 상태 초기화에 추가:

```python
        self._event_trims: dict[str, str] = {}
```

`_apply_absolute_momentum` 아래에 다음 두 메서드를 추가:

```python
    def _event_days_to_next(self, dt: date, symbols: Sequence[str], data_store) -> dict[str, int | None]:
        """Days until each symbol is next expected to report, or None.

        Reads the events panel as of `dt`, so only announcements the bot could
        already have seen contribute to the estimate. An absent panel yields
        None for every symbol, which the overlay treats as "leave it alone" —
        a missing dataset must never read as "no event coming".
        """
        wanted = [str(symbol).upper() for symbol in symbols]
        try:
            panel = data_store.panel("events", dt, wanted)
        except (FileNotFoundError, KeyError):
            panel = None
        if panel is None or panel.empty:
            return {symbol: None for symbol in wanted}

        result: dict[str, int | None] = {}
        for symbol in wanted:
            rows = panel[panel["symbol"] == symbol]
            dates = [value.date() for value in pd.to_datetime(rows["date"])]
            result[symbol] = days_to_next_event(dates, dt)
        return result

    def _apply_event_trims(self, ctx, dt: date) -> None:
        """Reduce held positions whose announcement falls inside the window.

        Runs every day, ahead of the rebalance gate: an event early in the
        month would otherwise wait until month-end, by which point the window
        it was meant to protect has passed.

        Each estimated event is trimmed once. The estimate is stable while no
        new announcement is observed, so recording the date it was trimmed for
        is enough to keep a daily pass from selling the position down to
        nothing over a week.
        """
        window = int(self.params["event_overlay_window_days"])
        if window < 0:
            return

        scale = float(self.params["event_overlay_scale"])
        theme = get_theme(str(self.params["theme"]), self.params["themes_path"])
        universe = theme_members(theme, dt)
        held = [symbol for symbol in universe if ctx.position(symbol).qty > 0]
        if not held:
            return

        days_map = self._event_days_to_next(dt, held, self._store())
        ledger = self._signal_ledger()
        trimmed_any = False
        for symbol in held:
            days = days_map.get(symbol)
            if days is None or not 0 <= days <= window:
                continue
            estimated = (dt + timedelta(days=days)).isoformat()
            if self._event_trims.get(symbol) == estimated:
                continue

            qty = ctx.position(symbol).qty
            sell_qty = int(qty - round(qty * scale))
            if sell_qty <= 0:
                # Too small to halve. Record it anyway so the next daily pass
                # does not retry the same event.
                self._event_trims[symbol] = estimated
                trimmed_any = True
                continue

            # Namespaced apart from the rebalance path on purpose: a plain
            # make_signal_id(self.name, dt, symbol, "SELL", ...) could collide
            # with a rebalance sell for the same symbol on a rebalance day,
            # and the ledger would silently drop one of the two orders.
            signal_id = make_signal_id(f"{self.name}:event_trim", dt, symbol, "SELL", scale)
            if not ledger.claim(signal_id):
                continue
            try:
                ctx.sell(symbol, qty=sell_qty)
            except Exception:
                LOGGER.exception(
                    "theme_multifactor: event trim failed for %s; continuing with the rest",
                    symbol,
                )
                continue
            LOGGER.info(
                "theme_multifactor: trimming %s by %s shares — report expected in %s day(s)",
                symbol,
                sell_qty,
                days,
            )
            self._event_trims[symbol] = estimated
            trimmed_any = True

        if trimmed_any:
            self.persist_state()
```

`timedelta` 임포트를 파일 상단에 추가한다: `from datetime import date, timedelta`.

`on_bar`의 날짜 중복 방지 직후, `is_rebalance_date` 검사 **앞**에 한 줄을 넣는다:

```python
        dt = bar.dt
        if dt == self._last_seen_date:
            return
        self._last_seen_date = dt

        # Runs before the rebalance gate on purpose: an event early in the
        # month must not wait for month-end.
        self._apply_event_trims(ctx, dt)

        calendar = get_calendar(str(self.params["market"]))
        if not is_rebalance_date(dt, str(self.params["rebalance"]), calendar):
            return
```

`snapshot_state`와 `restore_state`에 상태를 추가:

```python
    def snapshot_state(self) -> dict:
        return {
            ...,  # 기존 키 유지
            "event_trims": dict(self._event_trims),
        }

    def restore_state(self, state: dict) -> None:
        ...  # 기존 복원 유지
        self._event_trims = {
            str(symbol): str(value)
            for symbol, value in (state.get("event_trims") or {}).items()
        }
```

- [ ] **Step 4: 통과 확인 + 회귀**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_multifactor_event_overlay.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (17 tests)

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: **전체 PASS.** 특히 `tests/test_smoke_backtest.py`, `tests/test_theme_multifactor_backtest.py`, `tests/test_theme_multifactor_targets.py`, `tests/test_strategy_state.py`의 기존 고정값이 그대로여야 한다 — 오버레이 기본값이 비활성이므로 어떤 수치도 움직이면 안 된다. 움직였다면 구현이 잘못된 것이므로 여기서 멈추고 원인을 찾는다.

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/strategies/theme_multifactor.py tests/test_theme_multifactor_event_overlay.py
git commit -m "EVENT(part): Trim positions daily ahead of expected announcements"
```

---

### Task 5: 파이프라인 등록·설정·측정·문서

**Files:**
- Modify: `src/tradingbot/data/pipeline.py`
- Create: `config/kr_theme_event_overlay.toml`
- Modify: `README.md`, `docs/architecture.md`
- Test: `tests/test_pipeline_events.py`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces:
  - `COLLECTOR_MARKETS`에 `"events": ("KR",)` 등록
  - 파이프라인이 `events` 수집기를 실행하고 결과를 `SourceResult`로 보고
  - `config/kr_theme_event_overlay.toml` — `config/default.toml`과 **오버레이 두 줄만** 다른 설정

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pipeline_events.py`:

```python
from __future__ import annotations

from tradingbot.data.pipeline import COLLECTOR_MARKETS


class TestEventsCollectorRegistration:
    def test_events_is_kr_only(self):
        # DART is Korean-only. Without the guard a --market US run would send
        # US tickers to DART and write the result under processed/events/US/.
        assert COLLECTOR_MARKETS["events"] == ("KR",)

    def test_existing_guards_unchanged(self):
        assert COLLECTOR_MARKETS["prices"] == ("KR", "US")
        assert COLLECTOR_MARKETS["fundamentals"] == ("KR",)
```

`tests/test_data_pipeline.py`의 기존 테스트 구조(수집기 주입 방식)를 읽고, 그와 같은 방식으로 "events 수집기가 실행되고 행 수가 보고된다"는 테스트를 하나 추가한다. 기존 파일의 fixture와 헬퍼를 재사용하고, 새 파일에 중복 구현하지 않는다.

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_pipeline_events.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `KeyError: 'events'`

- [ ] **Step 3: 파이프라인에 수집기 등록**

`src/tradingbot/data/pipeline.py`:

- 상단 임포트에 `from tradingbot.data.events import update_events` 추가
- `COLLECTOR_MARKETS`에 `"events": ("KR",)` 추가하고, 주석에 DART 전용임을 명시
- `fundamentals` 수집기를 등록·실행하는 기존 자리를 그대로 따라 `events`를 추가한다. `corp_codes`는 `fundamentals`가 이미 로드하므로 **같은 `CorpCodeStore` 결과를 재사용**한다 — 같은 배치에서 두 번 내려받지 않는다
- API 키 부재는 `MissingApiKeyError`가 전파되어 `skipped`로 기록된다 (`fundamentals`와 동일 경로)

- [ ] **Step 4: 오버레이 설정 파일 생성**

`config/kr_theme_event_overlay.toml`을 만든다. **`config/default.toml`을 그대로 복사한 뒤 `[strategies.theme_multifactor]`에 두 줄만 추가한다.** 다른 값이 하나라도 다르면 §6.6의 비교가 성립하지 않는다.

파일 맨 위 주석:

```toml
# 이벤트 리스크 오버레이 설정.
# 설계: docs/superpowers/specs/2026-08-12-event-alpha-design.md §6
#
# default.toml과의 차이는 [strategies.theme_multifactor]의 두 줄뿐이다.
# 그 두 줄이 유일한 차이여야 아래 비교가 오버레이 효과만 재는 것이 된다:
#
#   python -m tradingbot --config config/kr_theme_event_overlay.toml research evaluate
#     --strategy theme_multifactor --market KR
#     --symbols 005930 000660 042700 058470 240810 --start 2023-01-01
#     --benchmark-config config/default.toml
#
# window_days=5, scale=0.5은 처음 정한 값이며 in-sample 스윕으로 고른 것이
# 아니다. 이 저장소는 스윕 승자를 채택했다가 held-out에서 뒤집힌 사례를 이미
# 갖고 있다 (docs/us_etf_rotation_review.md).
```

`[strategies.theme_multifactor]`에 추가할 두 줄:

```toml
event_overlay_window_days = 5
event_overlay_scale = 0.5
```

- [ ] **Step 5: 통과 확인 + 전체 회귀**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS

- [ ] **Step 6: 실데이터 수집 (완료 기준)**

`DART_API_KEY`가 설정된 환경에서:

```powershell
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR
```

Expected: `events` 수집기가 성공으로 보고되고 행 수가 0보다 크다. 수집된 패널을 눈으로 확인한다:

```powershell
.\.venv\Scripts\python.exe -c "import pandas as pd, glob; print(pd.concat([pd.read_parquet(p) for p in glob.glob('data/processed/events/KR/*.parquet')]).sort_values(['symbol','date']).to_string())"
```

**확인할 것 — 이것이 이 태스크의 핵심 검증이다:**

1. `event_kind`에 `provisional`이 실제로 존재하는가. 전부 `periodic`이면 분류기가 잠정실적을 못 잡고 있다는 뜻이고, 그러면 스펙 §3의 결함을 고치지 못한 것이다
2. 삼성전자(005930)의 4분기 이벤트가 **1월**에 찍히는가 (3월이 아니라)
3. `available_at`이 `date`의 다음 거래일인가
4. 종목당 이벤트가 분기당 1건 수준인가 (중복이 많으면 분류기가 과하게 잡고 있다)

**결과를 그대로 기록한다.** 1번이 실패하면 Task 1의 `_PROVISIONAL_MARKERS`를 실제 보고서명에 맞춰 고치고, 그 보고서명 문자열을 테스트에 추가한 뒤 재실행한다.

- [ ] **Step 7: 오버레이 효과 측정 (완료 기준)**

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\kr_theme_event_overlay.toml research evaluate --strategy theme_multifactor --market KR --symbols 005930 000660 042700 058470 240810 --start 2023-01-01 --benchmark-config config\default.toml
```

Expected: 승격 기준 6개 표가 출력되고 `reports/evaluation/`에 저장된다.

**판정 결과가 무엇이든 그대로 기록한다.** 이 단계의 목적은 오버레이가 좋다는 것을 보이는 게 아니라 **배관이 붙는지 확인하는 것**이다. 다음 중 어느 쪽이든 정상적인 결과다:

- 초과수익이 음수다 → 오버레이가 이 표본에서 손해였다. 5종목·3년 표본에서는 결론이 될 수 없다
- 초과수익이 0.00이다 → **경고 신호.** 오버레이가 한 번도 발동하지 않았을 가능성이 크다. 로그에서 `trimming ...` 메시지를 확인한다. 한 줄도 없으면 배관이 안 붙은 것이므로 원인을 찾는다
- Walk-forward가 "측정 불가"다 → 정상이다. 데이터가 2023년부터라 구간이 부족하다

측정 결과를 `docs/event_alpha_design_review.md`에 절을 추가해 기록한다 (수치, 발동 횟수, 해석). 판정문 형식은 기존 `docs/*_review.md`를 따른다.

- [ ] **Step 8: 문서 갱신**

`docs/architecture.md` §7 표에 세 행 추가:

```markdown
| 실적 이벤트 캘린더 수집 (DART 잠정실적) | `src/tradingbot/data/events.py` |
| 다음 발표일 추정 (PIT) | `src/tradingbot/research/event_calendar.py` |
| 발표 전 비중 축소 오버레이 | `src/tradingbot/allocation/event_overlay.py` |
```

`README.md`의 확장 목록에 추가:

```markdown
- 이벤트 리스크 오버레이(`data/events.py`, `allocation/event_overlay.py`):
  실적 발표를 앞둔 종목의 비중을 미리 줄입니다. 발표일은 DART 잠정실적
  공시에서 수집하며, 다음 발표일은 과거 발표 간격으로만 추정합니다 —
  미래 일정을 지금 지식으로 채우면 백테스트가 거짓말을 하기 때문입니다.
```

- [ ] **Step 9: 커밋**

```powershell
git add src/tradingbot/data/pipeline.py config/kr_theme_event_overlay.toml tests/test_pipeline_events.py tests/test_data_pipeline.py README.md docs/architecture.md docs/event_alpha_design_review.md
git commit -m "EVENT: Wire the event calendar into the pipeline and measure the overlay"
```

---

## 실행 중 발견해 계획에서 벗어난 것 (2026-08-12 구현 시점)

계획대로 되지 않은 부분과 그 이유를 남긴다. 다음 마일스톤의 계획을 쓸 때
같은 함정을 피하기 위해서다.

### 1. 리밸런싱이 축소를 되돌린다 → `generate_targets`에도 오버레이 적용

계획은 일별 축소 경로만 두었다. 그러면 1일에 축소한 포지션을 3일 리밸런싱이
원래 목표비중으로 **되사간다** — 이벤트는 아직 앞에 있는데. `generate_targets`가
`apply_constraints` 뒤에 `reduce_for_events`를 적용하도록 추가했다. 계획이
`reduce_for_events`를 `noqa: F401`로 임포트하게 시킨 것 자체가 신호였다.
쓰이지 않는 함수를 만들고 있었다는 뜻이다.

### 2. `round()`의 banker's rounding이 1주 포지션을 청산한다

`round(1 * 0.5) = 0`이라 `sell_qty = 1`, 즉 전량 매도. 축소 오버레이가 청산을
하면 안 된다. half-up 반올림(`int(qty * scale + 0.5)`)으로 바꾸고, 남는 수량이
0이면 **아예 건드리지 않는** 규칙을 명시했다.

### 3. **중복 방지 키가 매일 바뀌어 포지션이 소멸한다** (E2E가 잡은 실제 버그)

계획의 키는 `dt + days_to_event`였다. 추정일이 지나면 `days_to_next_event`가
매일 0을 반환하므로 이 키는 `dt`가 되어 **날마다 달라진다.** 결과: 같은 발표를
매일 다시 축소해 19102 → 9551 → 4776 → … 로 포지션이 녹는다. 합성 데이터
백테스트에서 체결 4건이어야 할 것이 **45건**으로 나왔다.

단위 테스트 전부가 이걸 놓쳤다. 계획의 `test_does_not_trim_the_same_event_twice`가
추정일이 **미래인** 두 날짜만 비교했기 때문이다.

수정: 키를 **마지막으로 관측된 발표일**로 바꿨다. 이 값은 새 공시가 들어오기
전까지 안정적이고, 바뀌는 시점이 정확히 새 축소가 정당한 시점이다.

교훈으로 남길 것: **순수 함수 단위 테스트만으로는 이 종류의 버그를 못 잡는다.**
M2 이후의 계획에도 "합성 데이터로 파이프라인 전체를 돌려 결과가 실제로 달라지는지
확인하는" 테스트를 반드시 포함시킨다.

### 4. 과기한(overdue) 유예가 너무 길었다 → `MAX_OVERDUE_DAYS = 14`

계획은 추정일이 지나도 "중앙값 간격만큼"은 발표 임박으로 봤다. 분기 보고
기업이면 **약 3개월** 동안 그 종목이 계속 이벤트 창 안에 있다는 뜻이고, 그
사이 모든 리밸런싱이 목표비중을 절반으로 깎는다. 14일로 줄였다.

### 5. 계획에 없던 E2E 테스트 추가 (`tests/test_event_overlay_backtest.py`)

계획의 Step 7(실데이터 측정)은 DART 키·네트워크·가격 캐시가 필요해 개발
환경에서 실행할 수 없다. 그 자리를 메우는 것이 아니라 **다른 질문에 답하기
위해** 합성 데이터 E2E 백테스트를 넣었다: "이벤트가 브로커까지 도달하는가."
3번 버그를 잡은 것이 이 테스트다.

### 6. 설정 격리를 테스트로 고정

`config/kr_theme_event_overlay.toml`이 `default.toml`과 오버레이 두 줄만
다르다는 것은 주석으로 적어두는 것으로 부족하다. 나중에 한쪽만 고치면 그
차이가 초과수익에 섞여 들어와 오버레이 효과로 읽힌다. 두 설정을 실제로 로드해
비교하는 테스트를 넣었다.

## 완료 기준 (스펙 §6)

- [ ] `events` 패널에 `provisional` 이벤트가 실제로 수집된다 — 삼성전자 4분기가 1월에 찍힌다
- [ ] 다음 발표일이 과거 이벤트만으로 추정되고, 추정 불가는 None으로 남아 오버레이가 아무것도 하지 않는다
- [ ] 오버레이가 리밸런싱이 아닌 날에도 발동하며, 같은 이벤트에 두 번 발동하지 않는다
- [ ] 축소분이 다른 종목에 재분배되지 않는다
- [ ] 오버레이가 꺼져 있으면 기존 백테스트 결과가 **한 자릿수까지 동일하다**
- [ ] `research evaluate`가 오버레이 유무 두 설정을 비교한 판정표를 낸다
- [ ] 측정 결과가 판정 방향과 무관하게 문서에 기록된다
- [ ] 전체 테스트 통과

## 알려진 한계 (범위 밖)

- **표본이 5종목·3년이다.** 이 단계의 성과 수치는 근거가 되지 못한다. 산출물은 판정이 아니라 작동하는 배관이다
- 오버레이는 축소만 하고 복원은 다음 정기 리밸런싱에 맡긴다 — 발표 직후 반등을 놓칠 수 있다
- 다음 발표일은 과거 간격의 중앙값 추정이다. `실적발표(예정)일` 공시를 PIT로 수집하면 정확해지지만 별도 과제다
- 서프라이즈·컨센서스·뉴스·ML은 이번 범위가 아니다 (M3 이후)
- 미국 시장은 이번 범위가 아니다. EDGAR 8-K Item 2.02 수집은 M3다
- **선결 결함:** 주문 사이징이 `min_cash_weight`를 미리 떼지 않아 주문의 17~19%가 거부된다(`docs/us_etf_rotation_review.md`). 오버레이 효과 측정도 이 결함을 통과해서 나온 값이므로, Step 7 결과를 읽을 때 감안한다
