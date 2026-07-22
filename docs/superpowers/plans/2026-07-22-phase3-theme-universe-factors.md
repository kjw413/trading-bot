# Phase 3: 테마 유니버스 + 팩터 확장 (M8/M9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 날짜별 테마 유니버스를 정의하고, Phase 2가 수집한 수급·밸류에이션 데이터를 횡단면 팩터로 만들어, Phase 1의 IC·분위수 게이트로 검증 가능한 상태에 도달한다.

**Architecture:** `ParquetDataStore`에 Point-in-Time 패널 조회를 추가해 팩터가 가격 외 데이터를 볼 수 있게 하고(기존 모멘텀 팩터는 그대로 동작), 그 위에 수급·가치 팩터를 얹는다. 팩터 점수는 `transform.py`가 극단값 절삭 → Z-score → 방향 통일 → 가중 결합해 종합점수로 만든다. 테마 유니버스는 편입·편출 날짜를 가진 TOML로 정의하고 `universe.members(theme, date)`가 그 시점 멤버만 반환한다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-07-19-kr-theme-multifactor-design.md` §6(테마 유니버스), §7(팩터 확장)

## Global Constraints

- **신규 의존성 추가 금지.**
- **Point-in-Time 준수**: 팩터는 `price_history`(as-of cutoff)와 `panel(as_of=...)`만 사용한다. `close_series`는 라벨 전용이며 팩터 코드에서 호출 금지. 패널 조회는 `available_at <= as_of` 필터를 우회할 수 없다.
- **기존 `Factor` 인터페이스를 바꾸지 않는다**: `compute(dt, universe, data_store) -> pd.Series`. 새 팩터도 같은 시그니처를 쓰고, 필요한 추가 데이터는 `data_store`가 제공한다. 기존 모멘텀 팩터 4종과 그 테스트는 수정하지 않는다.
- **점수 없는 종목은 NaN**: 조용히 드롭하지 않는다. 호출자가 "유니버스에 없음"과 "점수 못 냄"을 구분할 수 있어야 한다.
- **테마 멤버 변경은 날짜와 함께 기록**: 편입일 없이 멤버를 추가하면 생존자 편향이 생긴다. `from` 없는 멤버는 로드 시 오류.
- 테스트에서 네트워크 접근 금지.
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 기존 테스트·기존 CLI 명령 동작 변경 금지.
- 커밋 접두사: 중간 `M9(part):`, 마지막 `M9:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 pytest 기본 임시 디렉터리 생성이 실패하므로 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

---

### Task 1: 팩터용 패널 조회 (PIT)

**Files:**
- Modify: `src/tradingbot/data/store.py`
- Test: `tests/test_data_store_panel.py`

**Interfaces:**
- Consumes: `PanelStore` (Phase 2, `data/panel.py`)
- Produces:
  - `store.PanelDataStore` 프로토콜: `panel(dataset, as_of, symbols=None, *, start=None) -> pd.DataFrame`
  - `ParquetDataStore.__init__(cache, market, processed_root=None)` — 하위호환(기본 None)
  - `ParquetDataStore.panel(...)` — 패널이 없으면 **빈 DataFrame**(예외 아님), `processed_root` 미설정이면 빈 DataFrame
  - `ParquetDataStore.panel_latest(dataset, as_of, symbols, column) -> pd.Series` — 종목별 **가장 최근 관측 1건**의 값 (가치 팩터용)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data_store_panel.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore


def panel_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), "symbol": s, "value": v} for d, s, v in rows]
    )


@pytest.fixture
def store(tmp_path):
    processed = tmp_path / "processed"
    panel = PanelStore(processed, "flows", "KR")
    panel.append(
        attach_metadata(
            panel_frame(
                [
                    ("2024-01-02", "005930", 1.0),
                    ("2024-01-03", "005930", 2.0),
                    ("2024-01-02", "000660", 3.0),
                ]
            ),
            source="test",
            available_at=pd.Series(
                [
                    pd.Timestamp("2024-01-03"),
                    pd.Timestamp("2024-01-04"),
                    pd.Timestamp("2024-01-03"),
                ]
            ),
            data_version="1",
        )
    )
    return ParquetDataStore(ParquetCache(tmp_path / "cache"), "KR", processed_root=processed)


class TestPanelAccess:
    def test_reads_rows_visible_at_as_of(self, store):
        frame = store.panel("flows", date(2024, 1, 3))
        assert len(frame) == 2  # the 2024-01-03 observation is not yet available

    def test_later_as_of_sees_more(self, store):
        assert len(store.panel("flows", date(2024, 1, 4))) == 3

    def test_as_of_before_anything_is_empty(self, store):
        assert store.panel("flows", date(2024, 1, 1)).empty

    def test_symbol_filter(self, store):
        frame = store.panel("flows", date(2024, 1, 4), symbols=["005930"])
        assert set(frame["symbol"]) == {"005930"}

    def test_unknown_dataset_is_empty_not_error(self, store):
        assert store.panel("nope", date(2024, 1, 4)).empty

    def test_store_without_processed_root_returns_empty(self, tmp_path):
        bare = ParquetDataStore(ParquetCache(tmp_path), "KR")
        assert bare.panel("flows", date(2024, 1, 4)).empty


class TestPanelLatest:
    def test_takes_the_most_recent_observation_per_symbol(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 4), ["005930", "000660"], "value")
        assert latest.loc["005930"] == 2.0  # newer of the two
        assert latest.loc["000660"] == 3.0

    def test_respects_the_as_of_barrier(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 3), ["005930"], "value")
        assert latest.loc["005930"] == 1.0  # the newer row is not yet available

    def test_symbol_without_data_is_nan(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 4), ["999999"], "value")
        assert np.isnan(latest.loc["999999"])

    def test_missing_column_raises(self, store):
        with pytest.raises(KeyError):
            store.panel_latest("flows", date(2024, 1, 4), ["005930"], "nope")

    def test_empty_dataset_yields_all_nan(self, store):
        latest = store.panel_latest("nope", date(2024, 1, 4), ["005930"], "value")
        assert np.isnan(latest.loc["005930"])
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_store_panel.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `TypeError: ParquetDataStore.__init__() got an unexpected keyword argument 'processed_root'`

- [ ] **Step 3: 구현**

`src/tradingbot/data/store.py` — 기존 내용 유지하고 다음을 반영한다.

파일 상단 import에 추가:

```python
from pathlib import Path
from typing import Sequence
```

`PriceDataStore` 아래에 프로토콜 추가:

```python
class PanelDataStore(Protocol):
    """Point-in-time access to the non-price panels the pipeline collects."""

    def panel(
        self,
        dataset: str,
        as_of: date,
        symbols: Sequence[str] | None = None,
        *,
        start: date | None = None,
    ) -> pd.DataFrame:
        ...
```

`ParquetDataStore`를 다음으로 교체:

```python
class ParquetDataStore:
    """Local-only store: Parquet price cache plus the point-in-time panels.

    `processed_root` is optional so existing price-only callers keep working;
    without it the panel methods return empty results rather than failing,
    which lets a price-only factor run on a machine that has never run the
    data pipeline.
    """

    def __init__(
        self, cache: ParquetCache, market: str, processed_root: str | Path | None = None
    ) -> None:
        self.cache = cache
        self.market = market.upper()
        self.processed_root = Path(processed_root) if processed_root else None

    def price_history(self, symbol: str, end: date, lookback: int) -> pd.DataFrame:
        df = self.cache.read(self.market, symbol)
        cutoff = pd.Timestamp(end)
        return df.loc[df.index <= cutoff].tail(lookback)

    def close_series(self, symbol: str) -> pd.Series:
        """Full close history for research labels (look-ahead by design)."""
        return self.cache.read(self.market, symbol)["close"].dropna()

    def panel(
        self,
        dataset: str,
        as_of: date,
        symbols: Sequence[str] | None = None,
        *,
        start: date | None = None,
    ) -> pd.DataFrame:
        """Panel rows knowable at `as_of`. Empty when the dataset is absent."""
        if self.processed_root is None:
            return pd.DataFrame()
        from tradingbot.data.panel import PanelStore

        return PanelStore(self.processed_root, dataset, self.market).read(
            as_of=as_of, start=start, symbols=symbols
        )

    def panel_latest(
        self, dataset: str, as_of: date, symbols: Sequence[str], column: str
    ) -> pd.Series:
        """Each symbol's most recent knowable value of `column`.

        Symbols with no observation get NaN so callers can tell "no data" from
        a real value.
        """
        result = pd.Series(
            [float("nan")] * len(symbols),
            index=[str(s).upper() for s in symbols],
            dtype=float,
        )
        frame = self.panel(dataset, as_of, symbols)
        if frame.empty:
            return result
        if column not in frame.columns:
            raise KeyError(f"Panel {dataset} has no column {column}: {list(frame.columns)}")
        newest = frame.sort_values("date").groupby("symbol")[column].last()
        for symbol, value in newest.items():
            if symbol in result.index:
                result.loc[symbol] = float(value)
        return result
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_store_panel.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (11 tests)

- [ ] **Step 5: 회귀 + 커밋**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"` → 전체 PASS (기존 팩터 테스트가 깨지지 않아야 한다)

```powershell
git add src/tradingbot/data/store.py tests/test_data_store_panel.py
git commit -m "M9(part): Add point-in-time panel access to the data store"
```

---

### Task 2: 테마 유니버스

**Files:**
- Create: `config/themes.toml`
- Create: `src/tradingbot/data/universe.py`
- Test: `tests/test_data_universe.py`

**Interfaces:**
- Consumes: `tradingbot.config.PROJECT_ROOT`
- Produces:
  - `universe.ThemeMember` (frozen dataclass: `symbol: str`, `start: date`, `end: date | None`)
  - `universe.Theme` (frozen dataclass: `key: str`, `name: str`, `market: str`, `members: tuple[ThemeMember, ...]`)
  - `universe.load_themes(path=None) -> dict[str, Theme]`
  - `universe.members(theme: Theme, dt: date) -> list[str]`
  - `universe.THEMES_PATH`

- [ ] **Step 1: config 작성**

`config/themes.toml`:

```toml
# 테마별 종목 유니버스. 편입·편출 날짜를 반드시 함께 적는다.
#
# from 없이 종목을 추가하면 "그 종목이 처음부터 이 테마였다"는 뜻이 되어
# 생존자 편향이 생긴다 (지금 잘나가는 종목만 과거에 심는 것). 로더는 from이
# 없는 멤버를 거부한다.
#
# 한계: 과거 편입일을 지금 지식으로 소급 작성하면 편향은 남는다. 그래서 멤버를
# 바꿀 때는 날짜와 사유를 커밋 메시지에 함께 남긴다.

[themes.ai_semiconductor]
name = "AI 반도체"
market = "KR"
members = [
    { symbol = "005930", from = "2023-01-01" },  # 삼성전자
    { symbol = "000660", from = "2023-01-01" },  # SK하이닉스
    { symbol = "042700", from = "2023-01-01" },  # 한미반도체
    { symbol = "058470", from = "2023-01-01" },  # 리노공업
    { symbol = "240810", from = "2023-01-01" },  # 원익IPS
]

[themes.secondary_battery]
name = "2차전지"
market = "KR"
members = [
    { symbol = "373220", from = "2023-01-01" },  # LG에너지솔루션
    { symbol = "006400", from = "2023-01-01" },  # 삼성SDI
    { symbol = "096770", from = "2023-01-01" },  # SK이노베이션
    { symbol = "247540", from = "2023-01-01" },  # 에코프로비엠
    { symbol = "066970", from = "2023-01-01" },  # 엘앤에프
]
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data_universe.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from tradingbot.data.universe import load_themes, members

THEMES_TOML = """
[themes.demo]
name = "데모"
market = "KR"
members = [
    { symbol = "005930", from = "2023-01-01" },
    { symbol = "000660", from = "2023-06-01", to = "2024-03-01" },
]
"""


@pytest.fixture
def themes(tmp_path):
    path = tmp_path / "themes.toml"
    path.write_text(THEMES_TOML, encoding="utf-8")
    return load_themes(path)


class TestLoadThemes:
    def test_parses_theme_metadata(self, themes):
        theme = themes["demo"]
        assert theme.key == "demo"
        assert theme.name == "데모"
        assert theme.market == "KR"
        assert len(theme.members) == 2

    def test_member_dates_are_parsed(self, themes):
        first, second = themes["demo"].members
        assert first.start == date(2023, 1, 1)
        assert first.end is None
        assert second.end == date(2024, 3, 1)

    def test_member_without_from_is_rejected(self, tmp_path):
        path = tmp_path / "bad.toml"
        path.write_text(
            '[themes.x]\nname="x"\nmarket="KR"\nmembers=[{symbol="005930"}]\n',
            encoding="utf-8",
        )
        # Undated members would silently backdate today's winners into the past.
        with pytest.raises(ValueError, match="from"):
            load_themes(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_themes(tmp_path / "nope.toml")

    def test_repo_themes_file_loads(self):
        themes = load_themes()
        assert themes
        for theme in themes.values():
            assert theme.members
            assert theme.market in {"KR", "US"}


class TestMembers:
    def test_only_symbols_already_included(self, themes):
        assert members(themes["demo"], date(2023, 3, 1)) == ["005930"]

    def test_includes_symbol_on_its_start_date(self, themes):
        assert set(members(themes["demo"], date(2023, 6, 1))) == {"005930", "000660"}

    def test_excludes_symbol_after_removal(self, themes):
        # Removed 2024-03-01; a backtest on 2024-06-01 must not see it.
        assert members(themes["demo"], date(2024, 6, 1)) == ["005930"]

    def test_includes_symbol_on_its_end_date(self, themes):
        assert set(members(themes["demo"], date(2024, 3, 1))) == {"005930", "000660"}

    def test_before_any_member_is_empty(self, themes):
        assert members(themes["demo"], date(2022, 1, 1)) == []

    def test_result_is_sorted_for_reproducibility(self, themes):
        assert members(themes["demo"], date(2023, 12, 1)) == sorted(
            members(themes["demo"], date(2023, 12, 1))
        )
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_universe.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.data.universe'`

- [ ] **Step 4: 구현**

`src/tradingbot/data/universe.py`:

```python
"""Date-aware theme universes.

A theme is a hand-maintained list of symbols with inclusion and removal
dates. `members(theme, dt)` answers "which symbols were in this theme on that
date" — without that, a backtest silently trades companies that had not yet
joined the theme, or keeps trading ones that left.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tradingbot.config import PROJECT_ROOT

THEMES_PATH = PROJECT_ROOT / "config" / "themes.toml"


@dataclass(frozen=True)
class ThemeMember:
    symbol: str
    start: date
    end: date | None = None

    def active_on(self, dt: date) -> bool:
        """Inclusive on both ends: a symbol counts on the day it joins and leaves."""
        if dt < self.start:
            return False
        return self.end is None or dt <= self.end


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    market: str
    members: tuple[ThemeMember, ...]


def _parse_member(theme_key: str, raw: dict) -> ThemeMember:
    symbol = str(raw.get("symbol", "")).strip()
    if not symbol:
        raise ValueError(f"Theme {theme_key} has a member without a symbol")
    if "from" not in raw:
        raise ValueError(
            f"Theme {theme_key} member {symbol} has no `from` date. Undated members "
            "backdate today's winners into the past (survivorship bias)."
        )
    end = raw.get("to")
    return ThemeMember(
        symbol=symbol.upper(),
        start=date.fromisoformat(str(raw["from"])),
        end=date.fromisoformat(str(end)) if end else None,
    )


def load_themes(path: str | Path | None = None) -> dict[str, Theme]:
    """Load every theme definition, keyed by theme id."""
    themes_path = Path(path) if path else THEMES_PATH
    if not themes_path.exists():
        raise FileNotFoundError(f"Themes file not found: {themes_path}")
    with themes_path.open("rb") as handle:
        raw = tomllib.load(handle)

    themes: dict[str, Theme] = {}
    for key, body in raw.get("themes", {}).items():
        themes[key] = Theme(
            key=key,
            name=str(body.get("name", key)),
            market=str(body.get("market", "KR")).upper(),
            members=tuple(_parse_member(key, member) for member in body.get("members", [])),
        )
    return themes


def members(theme: Theme, dt: date) -> list[str]:
    """Symbols that belonged to `theme` on `dt`, sorted for reproducibility."""
    return sorted(member.symbol for member in theme.members if member.active_on(dt))


def get_theme(key: str, path: str | Path | None = None) -> Theme:
    themes = load_themes(path)
    try:
        return themes[key]
    except KeyError as exc:
        available = ", ".join(sorted(themes))
        raise ValueError(f"Unknown theme: {key}. Available: {available}") from exc
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_data_universe.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (11 tests)

- [ ] **Step 6: 커밋**

```powershell
git add config/themes.toml src/tradingbot/data/universe.py tests/test_data_universe.py
git commit -m "M9(part): Add date-aware theme universes"
```

---

### Task 3: 수급 팩터

**Files:**
- Create: `src/tradingbot/factors/flow.py`
- Test: `tests/test_factors_flow.py`

**Interfaces:**
- Consumes: `Factor` (기존), `ParquetDataStore.panel` (Task 1), Phase 2의 `flows` 패널 (`foreign_net`, `institution_net`, `individual_net`)
- Produces:
  - `flow.NetBuyIntensityFactor(investor: str, days: int)` — 이름 `{investor}_net_{days}d`
  - 누적 순매수를 같은 기간 거래대금으로 나눈 값 (규모 중립화)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_factors_flow.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors.flow import NetBuyIntensityFactor

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_flows(store, symbol: str, foreign: list[float], end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(foreign))
    frame = pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "foreign_net": foreign,
            "institution_net": [0.0] * len(foreign),
            "individual_net": [0.0] * len(foreign),
        }
    )
    panel = PanelStore(store.processed_root, "flows", "KR")
    panel.append(
        attach_metadata(
            frame,
            source="test",
            # Same-day availability keeps the fixture simple; the PIT barrier
            # itself is covered by the dedicated look-ahead test below.
            available_at=frame["date"],
            data_version="1",
        )
    )


def write_prices(store, symbol: str, closes: list[float], volume: float, end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [volume] * len(closes),
            },
            index=index,
        ),
    )


class TestNetBuyIntensityFactor:
    def test_positive_flow_scores_positive(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert result.name == "foreign_net_20d"
        # 20 days x 100 net buy / (20 days x 10 price x 100 volume) = 0.1
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_selling_scores_negative(self, store):
        write_flows(store, "AAA", [-100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert result.loc["AAA"] < 0

    def test_scales_out_size(self, store):
        # Same relative flow, ten times the traded value: identical score.
        write_flows(store, "SMALL", [100.0] * 20)
        write_prices(store, "SMALL", [10.0] * 20, volume=100.0)
        write_flows(store, "BIG", [1000.0] * 20)
        write_prices(store, "BIG", [10.0] * 20, volume=1000.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["SMALL", "BIG"], store)
        assert result.loc["SMALL"] == pytest.approx(result.loc["BIG"])

    def test_no_lookahead_past_the_as_of_date(self, store):
        # A huge buy recorded after AS_OF must not move the score.
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        baseline = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)

        future = date(2024, 4, 1)
        write_flows(store, "AAA", [999999.0] * 5, end=future)
        after = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert after.loc["AAA"] == pytest.approx(baseline.loc["AAA"])

    def test_missing_flows_is_nan(self, store):
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_missing_prices_is_nan(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_zero_traded_value_is_nan_not_infinite(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=0.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_institution_investor_variant(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("institution", 20).compute(AS_OF, ["AAA"], store)
        assert result.name == "institution_net_20d"
        assert result.loc["AAA"] == pytest.approx(0.0)  # fixture writes zeros

    def test_unknown_investor_rejected(self):
        with pytest.raises(ValueError, match="investor"):
            NetBuyIntensityFactor("martian", 20)

    def test_invalid_days_rejected(self):
        with pytest.raises(ValueError):
            NetBuyIntensityFactor("foreign", 0)

    def test_empty_universe(self, store):
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, [], store)
        assert result.empty
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_flow.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.factors.flow'`

- [ ] **Step 3: 구현**

`src/tradingbot/factors/flow.py`:

```python
"""Investor-flow factors.

Raw net-buy amounts are dominated by company size — a large cap absorbs more
money on a quiet day than a small cap does on a frantic one. Dividing by the
traded value over the same window makes the number comparable across the
universe: "how much of this stock's turnover was this investor group buying".
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor

INVESTOR_COLUMNS = {
    "foreign": "foreign_net",
    "institution": "institution_net",
    "individual": "individual_net",
}

# Calendar days to span a given number of trading days, with slack for
# holidays. Over-fetching is harmless — the window is trimmed by row count.
_CALENDAR_SLACK = 2.0


class NetBuyIntensityFactor(Factor):
    """Cumulative net buying over `days`, scaled by traded value.

    Positive means the investor group was a net buyer relative to how much of
    the stock changed hands.
    """

    def __init__(self, investor: str, days: int) -> None:
        if investor not in INVESTOR_COLUMNS:
            available = ", ".join(sorted(INVESTOR_COLUMNS))
            raise ValueError(f"Unknown investor: {investor}. Available: {available}")
        if days <= 0:
            raise ValueError("days must be positive")
        self.investor = investor
        self.days = days
        self.column = INVESTOR_COLUMNS[investor]
        self.name = f"{investor}_net_{days}d"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values

        start = dt - timedelta(days=int(self.days * _CALENDAR_SLACK) + 7)
        flows = data_store.panel("flows", dt, list(values.index), start=start)
        if flows.empty:
            return values

        for symbol in values.index:
            rows = flows[flows["symbol"] == symbol].sort_values("date").tail(self.days)
            if rows.empty:
                continue
            try:
                prices = data_store.price_history(symbol, dt, self.days)
            except (FileNotFoundError, KeyError):
                continue
            traded_value = float((prices["close"] * prices["volume"]).sum())
            if traded_value <= 0:
                continue
            values.loc[symbol] = float(rows[self.column].sum()) / traded_value
        return values
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_flow.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (11 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/factors/flow.py tests/test_factors_flow.py
git commit -m "M9(part): Add investor net-buy intensity factors"
```

---

### Task 4: 가치 팩터

**Files:**
- Create: `src/tradingbot/factors/value.py`
- Test: `tests/test_factors_value.py`

**Interfaces:**
- Consumes: `Factor`, `ParquetDataStore.panel_latest` (Task 1), Phase 2의 `valuation` 패널 (`per`, `pbr`)
- Produces:
  - `value.EarningsYieldFactor()` — 이름 `earnings_yield`, 값 `1/PER`
  - `value.BookToMarketFactor()` — 이름 `book_to_market`, 값 `1/PBR`

**설계 근거:** PER·PBR을 그대로 쓰지 않고 역수를 쓴다. (1) 역수는 "높을수록 저평가"로 방향이 통일되어 종합점수 결합이 단순해진다. (2) PER은 이익이 0에 가까울 때 무한대로 발산하지만 역수는 0에 수렴해 극단값이 순위를 지배하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_factors_value.py`:

```python
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors.value import BookToMarketFactor, EarningsYieldFactor

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_valuation(store, rows: list[tuple[str, str, float, float]], available: str | None = None):
    """rows: (date, symbol, per, pbr)"""
    frame = pd.DataFrame(
        [
            {"date": pd.Timestamp(d), "symbol": s, "per": per, "pbr": pbr}
            for d, s, per, pbr in rows
        ]
    )
    panel = PanelStore(store.processed_root, "valuation", "KR")
    panel.append(
        attach_metadata(
            frame,
            source="test",
            available_at=pd.Timestamp(available) if available else frame["date"],
            data_version="1",
        )
    )


class TestEarningsYieldFactor:
    def test_inverts_per(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert result.name == "earnings_yield"
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_cheaper_stock_scores_higher(self, store):
        write_valuation(store, [("2024-02-28", "CHEAP", 5.0, 1.0), ("2024-02-28", "RICH", 50.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["CHEAP", "RICH"], store)
        assert result.loc["CHEAP"] > result.loc["RICH"]

    def test_uses_the_most_recent_observation(self, store):
        write_valuation(store, [("2024-02-01", "AAA", 20.0, 1.0), ("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_respects_availability(self, store):
        # Observed before AS_OF but only publishable afterwards.
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)], available="2024-04-01")
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_missing_per_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", float("nan"), 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_non_positive_per_is_nan(self, store):
        # A loss-making company has no meaningful earnings yield; 1/-5 would
        # rank it between two profitable companies.
        write_valuation(store, [("2024-02-28", "AAA", -5.0, 1.0), ("2024-02-28", "BBB", 0.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA", "BBB"], store)
        assert np.isnan(result.loc["AAA"])
        assert np.isnan(result.loc["BBB"])

    def test_symbol_without_data_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA", "ZZZ"], store)
        assert np.isnan(result.loc["ZZZ"])

    def test_no_panel_yields_all_nan(self, store):
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_empty_universe(self, store):
        assert EarningsYieldFactor().compute(AS_OF, [], store).empty


class TestBookToMarketFactor:
    def test_inverts_pbr(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 2.0)])
        result = BookToMarketFactor().compute(AS_OF, ["AAA"], store)
        assert result.name == "book_to_market"
        assert result.loc["AAA"] == pytest.approx(0.5)

    def test_non_positive_pbr_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 0.0)])
        result = BookToMarketFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_value.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.factors.value'`

- [ ] **Step 3: 구현**

`src/tradingbot/factors/value.py`:

```python
"""Valuation factors from KRX's published daily ratios.

Ratios are inverted (1/PER, 1/PBR) for two reasons: the inverted form points
the same way as every other factor here — higher is better — and it stays
bounded as earnings approach zero, where PER itself explodes and would
dominate any cross-sectional ranking.

Non-positive ratios yield NaN rather than a negative score: a loss-making
company has no meaningful earnings yield, and ranking it between two
profitable companies would be worse than not ranking it at all.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor


class _InverseRatioFactor(Factor):
    """Shared shape for 1/ratio valuation factors."""

    dataset = "valuation"

    def __init__(self, name: str, column: str) -> None:
        self.name = name
        self.column = column

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values

        latest = data_store.panel_latest(self.dataset, dt, list(values.index), self.column)
        for symbol, ratio in latest.items():
            if pd.isna(ratio) or ratio <= 0:
                continue
            values.loc[symbol] = 1.0 / float(ratio)
        return values


class EarningsYieldFactor(_InverseRatioFactor):
    """1/PER — higher means cheaper relative to earnings."""

    def __init__(self) -> None:
        super().__init__("earnings_yield", "per")


class BookToMarketFactor(_InverseRatioFactor):
    """1/PBR — higher means cheaper relative to book value."""

    def __init__(self) -> None:
        super().__init__("book_to_market", "pbr")
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_value.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (11 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/factors/value.py tests/test_factors_value.py
git commit -m "M9(part): Add earnings-yield and book-to-market factors"
```

---

### Task 5: 팩터 변환과 종합점수

**Files:**
- Create: `src/tradingbot/factors/transform.py`
- Test: `tests/test_factors_transform.py`

**Interfaces:**
- Produces:
  - `transform.winsorize(values, limit=0.02) -> pd.Series`
  - `transform.zscore(values) -> pd.Series`
  - `transform.standardize(values, *, limit=0.02) -> pd.Series` (winsorize → zscore)
  - `transform.combine(scores: dict[str, pd.Series], weights: dict[str, float], *, min_factors=1) -> pd.Series`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_factors_transform.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.factors.transform import combine, standardize, winsorize, zscore


class TestWinsorize:
    def test_clips_extremes_to_the_boundary(self):
        values = pd.Series({f"S{i}": float(i) for i in range(100)})
        values.loc["OUTLIER"] = 1e9
        clipped = winsorize(values, limit=0.05)
        assert clipped.max() < 1e9
        assert clipped.loc["OUTLIER"] == clipped.drop("OUTLIER").max()

    def test_preserves_the_middle(self):
        values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})
        clipped = winsorize(values, limit=0.0)
        pd.testing.assert_series_equal(clipped, values)

    def test_nan_is_preserved_not_clipped(self):
        values = pd.Series({"A": 1.0, "B": float("nan"), "C": 3.0})
        assert np.isnan(winsorize(values).loc["B"])

    def test_all_nan_returns_all_nan(self):
        values = pd.Series({"A": float("nan")})
        assert np.isnan(winsorize(values).loc["A"])

    def test_invalid_limit_rejected(self):
        with pytest.raises(ValueError):
            winsorize(pd.Series({"A": 1.0}), limit=0.6)


class TestZscore:
    def test_centers_and_scales(self):
        values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})
        scored = zscore(values)
        assert scored.mean() == pytest.approx(0.0)
        assert scored.loc["B"] == pytest.approx(0.0)
        assert scored.loc["C"] > 0

    def test_constant_input_is_all_zero_not_nan(self):
        # Every name is equally (un)attractive; zero is the honest score.
        scored = zscore(pd.Series({"A": 5.0, "B": 5.0}))
        assert (scored == 0).all()

    def test_nan_stays_nan_and_is_excluded_from_stats(self):
        values = pd.Series({"A": 1.0, "B": 3.0, "C": float("nan")})
        scored = zscore(values)
        assert np.isnan(scored.loc["C"])
        assert scored.loc["A"] == pytest.approx(-scored.loc["B"])

    def test_single_value_is_zero(self):
        assert zscore(pd.Series({"A": 7.0})).loc["A"] == pytest.approx(0.0)

    def test_empty_series(self):
        assert zscore(pd.Series(dtype=float)).empty


class TestStandardize:
    def test_applies_winsorize_then_zscore(self):
        values = pd.Series({f"S{i}": float(i) for i in range(50)})
        values.loc["OUTLIER"] = 1e9
        scored = standardize(values, limit=0.05)
        # Without winsorizing, the outlier would compress everything else to ~0.
        assert scored.loc["OUTLIER"] < 10
        assert scored.std() == pytest.approx(1.0, rel=0.2)


class TestCombine:
    def test_weighted_average_of_standardized_scores(self):
        scores = {
            "a": pd.Series({"X": 1.0, "Y": -1.0}),
            "b": pd.Series({"X": -1.0, "Y": 1.0}),
        }
        combined = combine(scores, {"a": 0.75, "b": 0.25})
        assert combined.loc["X"] == pytest.approx(0.5)
        assert combined.loc["Y"] == pytest.approx(-0.5)

    def test_weights_are_renormalized_over_present_factors(self):
        # Y is missing factor b; its score must use a alone, not be dragged
        # toward zero by treating the missing factor as 0.
        scores = {
            "a": pd.Series({"X": 1.0, "Y": 2.0}),
            "b": pd.Series({"X": 1.0, "Y": float("nan")}),
        }
        combined = combine(scores, {"a": 0.5, "b": 0.5})
        assert combined.loc["Y"] == pytest.approx(2.0)
        assert combined.loc["X"] == pytest.approx(1.0)

    def test_symbol_below_min_factors_is_nan(self):
        scores = {
            "a": pd.Series({"X": 1.0, "Y": 1.0}),
            "b": pd.Series({"X": 1.0, "Y": float("nan")}),
        }
        combined = combine(scores, {"a": 0.5, "b": 0.5}, min_factors=2)
        assert np.isnan(combined.loc["Y"])
        assert combined.loc["X"] == pytest.approx(1.0)

    def test_unknown_weight_key_rejected(self):
        with pytest.raises(ValueError, match="weight"):
            combine({"a": pd.Series({"X": 1.0})}, {"nope": 1.0})

    def test_zero_total_weight_rejected(self):
        with pytest.raises(ValueError):
            combine({"a": pd.Series({"X": 1.0})}, {"a": 0.0})

    def test_empty_scores_returns_empty(self):
        assert combine({}, {}).empty

    def test_union_of_symbols_is_covered(self):
        scores = {"a": pd.Series({"X": 1.0}), "b": pd.Series({"Y": 1.0})}
        combined = combine(scores, {"a": 0.5, "b": 0.5})
        assert set(combined.index) == {"X", "Y"}
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_transform.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.factors.transform'`

- [ ] **Step 3: 구현**

`src/tradingbot/factors/transform.py`:

```python
"""Turn raw factor values into comparable, combinable scores.

Raw factors live on incompatible scales — a momentum return and an earnings
yield cannot be averaged directly. Standardizing each to a z-score after
clipping extremes makes them additive, and combining with renormalized
weights means a symbol missing one factor is scored on the ones it has
rather than being penalized for the gap.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_WINSOR_LIMIT = 0.02


def winsorize(values: pd.Series, limit: float = DEFAULT_WINSOR_LIMIT) -> pd.Series:
    """Clip the tails to their quantile boundary.

    A single mis-scaled value would otherwise set the whole cross-section's
    mean and standard deviation, flattening every real difference.
    """
    if not 0.0 <= limit < 0.5:
        raise ValueError("limit must be in [0, 0.5)")
    clean = values.dropna()
    if clean.empty or limit == 0.0:
        return values.copy()
    lower = clean.quantile(limit)
    upper = clean.quantile(1.0 - limit)
    return values.clip(lower=lower, upper=upper)


def zscore(values: pd.Series) -> pd.Series:
    """Center and scale to unit standard deviation.

    A constant cross-section scores all zeros rather than NaN: every name
    being equally attractive is information, not missing data.
    """
    result = values.copy()
    clean = values.dropna()
    if clean.empty:
        return result
    std = float(clean.std(ddof=0))
    if std == 0:
        result.loc[clean.index] = 0.0
        return result
    return (values - float(clean.mean())) / std


def standardize(values: pd.Series, *, limit: float = DEFAULT_WINSOR_LIMIT) -> pd.Series:
    """Winsorize then z-score — the standard pre-combination treatment."""
    return zscore(winsorize(values, limit))


def combine(
    scores: dict[str, pd.Series],
    weights: dict[str, float],
    *,
    min_factors: int = 1,
) -> pd.Series:
    """Weighted blend of standardized factor scores.

    Weights are renormalized per symbol over the factors that symbol actually
    has, so a missing factor neither counts as zero nor drops the symbol.
    Symbols scored by fewer than `min_factors` factors get NaN.
    """
    if not scores:
        return pd.Series(dtype=float)

    unknown = [name for name in weights if name not in scores]
    if unknown:
        available = ", ".join(sorted(scores))
        raise ValueError(f"weight given for unknown factor(s) {unknown}. Available: {available}")
    active = {name: float(weights.get(name, 0.0)) for name in scores}
    if sum(abs(w) for w in active.values()) == 0:
        raise ValueError("weights sum to zero")

    frame = pd.DataFrame(scores)
    weight_row = pd.Series(active)
    present = frame.notna()
    weighted_sum = (frame.fillna(0.0) * weight_row).sum(axis=1)
    weight_total = (present * weight_row).sum(axis=1)

    combined = weighted_sum / weight_total.replace(0.0, float("nan"))
    combined[present.sum(axis=1) < min_factors] = float("nan")
    return combined
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_factors_transform.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (18 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/factors/transform.py tests/test_factors_transform.py
git commit -m "M9(part): Add factor standardization and weighted combination"
```

---

### Task 6: 시장 국면 필터

**Files:**
- Create: `src/tradingbot/research/regime.py`
- Test: `tests/test_research_regime.py`

**Interfaces:**
- Consumes: `ParquetDataStore.panel` (Task 1), Phase 2의 `macro` 패널 (`kospi` 등의 `close`)
- Produces:
  - `regime.BULL = "bull"`, `regime.BEAR = "bear"`, `regime.UNKNOWN = "unknown"`
  - `regime.market_regime(data_store, dt, *, series="kospi", ma_days=200) -> str`
  - `regime.equity_exposure(regime_state, *, bull=1.0, bear=0.5) -> float`

**설계 근거:** 거시 데이터는 종목 팩터가 아니라 **노출도 조절**에 쓴다 (스펙 §7). 지수가 200일 이동평균 위면 상승 국면, 아래면 하락 국면으로 보고 주식 비중을 줄인다. 데이터가 부족하면 `UNKNOWN`이고, 이때는 노출을 줄이지 않는다 — 모르는 것을 하락 신호로 취급하면 조용히 계속 현금만 들고 있게 된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research_regime.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.research.regime import BEAR, BULL, UNKNOWN, equity_exposure, market_regime

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_macro(store, closes: list[float], series: str = "kospi", end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    frame = pd.DataFrame({"date": index, "symbol": series, "close": closes})
    PanelStore(store.processed_root, "macro", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


class TestMarketRegime:
    def test_price_above_moving_average_is_bull(self, store):
        write_macro(store, [100.0] * 200 + [150.0])
        assert market_regime(store, AS_OF, ma_days=200) == BULL

    def test_price_below_moving_average_is_bear(self, store):
        write_macro(store, [100.0] * 200 + [50.0])
        assert market_regime(store, AS_OF, ma_days=200) == BEAR

    def test_insufficient_history_is_unknown(self, store):
        write_macro(store, [100.0] * 10)
        assert market_regime(store, AS_OF, ma_days=200) == UNKNOWN

    def test_no_macro_data_is_unknown(self, store):
        assert market_regime(store, AS_OF) == UNKNOWN

    def test_respects_the_as_of_barrier(self, store):
        write_macro(store, [100.0] * 200 + [150.0])
        # Before any observation exists, the regime is unknowable.
        assert market_regime(store, date(2020, 1, 1), ma_days=200) == UNKNOWN

    def test_unknown_series_is_unknown_not_error(self, store):
        write_macro(store, [100.0] * 201)
        assert market_regime(store, AS_OF, series="nasdaq", ma_days=200) == UNKNOWN


class TestEquityExposure:
    def test_full_exposure_in_a_bull(self):
        assert equity_exposure(BULL) == pytest.approx(1.0)

    def test_reduced_exposure_in_a_bear(self):
        assert equity_exposure(BEAR) == pytest.approx(0.5)

    def test_unknown_does_not_reduce_exposure(self):
        # Treating "no data" as bearish would quietly park the strategy in cash.
        assert equity_exposure(UNKNOWN) == pytest.approx(1.0)

    def test_custom_levels(self):
        assert equity_exposure(BEAR, bear=0.25) == pytest.approx(0.25)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_regime.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.research.regime'`

- [ ] **Step 3: 구현**

`src/tradingbot/research/regime.py`:

```python
"""Market regime from a macro index.

Macro series are not per-stock factors — they say nothing about which stock
to prefer. They are used to decide how much equity exposure to carry at all:
a theme's best names still fall in a broad drawdown.
"""

from __future__ import annotations

from datetime import date, timedelta

from tradingbot.data.store import PanelDataStore

BULL = "bull"
BEAR = "bear"
UNKNOWN = "unknown"


def market_regime(
    data_store: PanelDataStore,
    dt: date,
    *,
    series: str = "kospi",
    ma_days: int = 200,
) -> str:
    """Compare the index to its own moving average as of `dt`.

    Returns UNKNOWN when there is not enough history to judge — the caller
    must decide what to do with that rather than have it silently read as
    bearish.
    """
    if ma_days <= 0:
        raise ValueError("ma_days must be positive")

    start = dt - timedelta(days=int(ma_days * 2.0) + 30)
    panel = data_store.panel("macro", dt, [series], start=start)
    if panel.empty:
        return UNKNOWN

    closes = panel.sort_values("date")["close"].dropna()
    if len(closes) < ma_days:
        return UNKNOWN

    window = closes.tail(ma_days)
    return BULL if float(window.iloc[-1]) > float(window.mean()) else BEAR


def equity_exposure(regime_state: str, *, bull: float = 1.0, bear: float = 0.5) -> float:
    """Target equity exposure for a regime.

    UNKNOWN keeps full exposure: an unmeasurable regime is not evidence of a
    downturn, and defaulting to defensive would strand the strategy in cash
    whenever the macro panel is briefly missing.
    """
    return bear if regime_state == BEAR else bull
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_research_regime.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/research/regime.py tests/test_research_regime.py
git commit -m "M9(part): Add macro-based market regime filter"
```

---

### Task 7: 통합 — 레지스트리, 테마 리서치 CLI, 파이프라인 완성, 문서

**Files:**
- Modify: `src/tradingbot/factors/registry.py`
- Modify: `src/tradingbot/cli.py` (`research report --theme`)
- Modify: `src/tradingbot/data/pipeline.py` (품질 검사 + 가격 갱신 배선)
- Modify: `config/research.toml` (`[factor_weights]`)
- Modify: `README.md`, `docs/architecture.md`
- Test: `tests/test_factors_registry_phase3.py`, `tests/test_pipeline_quality.py`

**Interfaces:**
- Produces:
  - 레지스트리에 `foreign_net_20d`, `foreign_net_60d`, `institution_net_20d`, `earnings_yield`, `book_to_market` 등록
  - CLI: `tradingbot research report --theme <key>` — 유니버스를 테마에서 해결
  - `pipeline`: 가격 캐시 갱신(`prices` 소스)과 수집 후 품질 검사 결과를 `SourceResult.message`에 포함

- [ ] **Step 1: 레지스트리 등록**

`src/tradingbot/factors/registry.py` 파일 끝에 추가:

```python
from tradingbot.factors.flow import NetBuyIntensityFactor
from tradingbot.factors.value import BookToMarketFactor, EarningsYieldFactor

register_factor("foreign_net_20d", lambda: NetBuyIntensityFactor("foreign", 20))
register_factor("foreign_net_60d", lambda: NetBuyIntensityFactor("foreign", 60))
register_factor("institution_net_20d", lambda: NetBuyIntensityFactor("institution", 20))
register_factor("earnings_yield", lambda: EarningsYieldFactor())
register_factor("book_to_market", lambda: BookToMarketFactor())
```

`tests/test_factors_registry_phase3.py`:

```python
from __future__ import annotations

from tradingbot.factors import get_factor, list_factors


def test_phase3_factors_are_registered():
    for name in [
        "foreign_net_20d",
        "foreign_net_60d",
        "institution_net_20d",
        "earnings_yield",
        "book_to_market",
    ]:
        assert name in list_factors()
        assert get_factor(name).name == name


def test_momentum_factors_still_registered():
    for name in ["momentum_3m", "momentum_6m", "momentum_12m", "momentum_12m_ex1m"]:
        assert name in list_factors()
```

- [ ] **Step 2: config에 팩터 가중치 추가**

`config/research.toml` 끝에 추가:

```toml
[factor_weights]
# 종합점수 결합 가중치. 게이트를 통과한 팩터만 여기에 올린다
# (docs/quant_research_spec.md 5장). 합이 1일 필요는 없다 — 종목별로
# 존재하는 팩터에 대해 재정규화된다.
momentum_6m = 0.3
momentum_12m_ex1m = 0.3
foreign_net_20d = 0.2
earnings_yield = 0.2
```

- [ ] **Step 3: 파이프라인에 가격 갱신과 품질 검사 배선**

`src/tradingbot/data/pipeline.py`의 `_default_collectors`에 가격 수집기를 추가하고, 패널 수집 뒤 품질 검사를 돌린다. `prices` 항목을 반환 dict의 **맨 앞**에 넣어 가격이 먼저 갱신되게 한다:

```python
    def prices(**_: Any) -> int:
        """Refresh the OHLCV cache the factor layer reads."""
        from tradingbot.data.cache import ParquetCache
        from tradingbot.data.quality import FAIL, check_ohlcv

        cache = ParquetCache(cache_root)
        rows = 0
        for symbol in symbols:
            try:
                frame = cache.update(market, symbol)
            except Exception:
                LOGGER.exception("Price update failed for %s; skipping", symbol)
                continue
            report = check_ohlcv(frame, dataset=f"prices/{symbol}", market=market)
            if report.severity == FAIL:
                LOGGER.error("Price quality check failed for %s: %s", symbol, report.issues)
            rows += len(frame)
        return rows
```

그리고 `run_pipeline`에서 각 패널 소스 수집 후 품질 검사를 실행해 결과 메시지에 남긴다. `run_pipeline`의 성공 분기를 다음으로 교체:

```python
            rows = with_retry(
                lambda c=collector: c(market=market, symbols=active_symbols),
                attempts=attempts,
                no_retry=(MissingCredentialsError,),
            )
            message = _panel_quality_message(processed, name, market.upper())
            results.append(SourceResult(name, STATUS_OK, int(rows), message))
            LOGGER.info("Pipeline source %s collected %s rows%s", name, rows, f" ({message})" if message else "")
```

같은 파일에 헬퍼 추가:

```python
def _panel_quality_message(processed_root: Path, dataset: str, market: str) -> str:
    """Quality summary for a freshly collected panel, or '' when not applicable."""
    from tradingbot.data.panel import PanelStore
    from tradingbot.data.quality import PASS, check_panel

    if dataset == "prices":
        return ""
    try:
        panel = PanelStore(processed_root, dataset, market).read()
    except Exception:
        LOGGER.exception("Quality check could not read panel %s", dataset)
        return "quality check unavailable"
    report = check_panel(panel, dataset=dataset)
    if report.severity == PASS:
        return ""
    return f"quality={report.severity}: " + "; ".join(
        f"{issue.check}({issue.count})" for issue in report.issues
    )
```

`tests/test_pipeline_quality.py`:

```python
from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.pipeline import run_pipeline


@pytest.fixture
def config(tmp_path):
    return {
        "pipeline": {
            "processed_dir": str(tmp_path / "processed"),
            "log_dir": str(tmp_path / "log"),
            "symbols": ["005930"],
            "retry_attempts": 1,
        },
        "data": {"cache_dir": str(tmp_path / "cache")},
    }


def write_bad_panel(root, dataset="flows"):
    frame = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-02"), "symbol": "005930", "value": 1.0},
            {"date": pd.Timestamp("2024-01-02"), "symbol": "005930", "value": 2.0},
        ]
    )
    PanelStore(root, dataset, "KR").append(
        attach_metadata(frame, source="t", available_at="2024-01-03", data_version="1")
    )


def test_clean_panel_reports_no_quality_message(config, tmp_path):
    result = run_pipeline(
        config, market="KR", symbols=["005930"], collectors={"flows": lambda **k: 1}
    )
    assert result.results[0].message == ""


def test_duplicate_keys_surface_in_the_result_message(config, tmp_path):
    def collector(**_):
        write_bad_panel(tmp_path / "processed")
        return 2

    result = run_pipeline(config, market="KR", symbols=["005930"], collectors={"flows": collector})
    source = result.results[0]
    # Collection "succeeded" but the data is unusable — the operator must see it.
    assert source.status == "ok"
    assert "quality=fail" in source.message
    assert "duplicate_key" in source.message
```

Note: `PanelStore.append` dedupes on `(date, symbol)`, so the duplicate must be written by two separate `append` calls with different values to survive — adjust `write_bad_panel` to call `append` twice with differing values if the single-call version does not reproduce a duplicate.

- [ ] **Step 4: `research report --theme` 추가**

`src/tradingbot/cli.py`의 `factor_report_parser`에 인자 추가:

```python
    factor_report_parser.add_argument(
        "--theme", default=None, help="Resolve the universe from config/themes.toml"
    )
```

`cmd_research_report`에서 유니버스 해결부를 교체:

```python
    if args.theme:
        from tradingbot.data.universe import get_theme, members as theme_members

        theme = get_theme(args.theme)
        market = theme.market
        universe = theme_members(theme, end)
        if not universe:
            print(f"테마 {args.theme}에 {end} 기준 종목이 없습니다.")
            return 1
    else:
        market = research["universe"]["market"]
        universe = research["universe"]["symbols"]
```

그리고 store 생성 시 `processed_root`를 넘긴다:

```python
    store = ParquetDataStore(
        ParquetCache(resolve_project_path(args.data_root or "data/cache")),
        market,
        processed_root=resolve_project_path("data/processed"),
    )
```

`tests/test_research_report.py`에 CLI 배선 테스트 추가:

```python
def test_cli_parser_accepts_theme():
    parser = build_parser()
    args = parser.parse_args(["research", "report", "--theme", "ai_semiconductor"])
    assert args.theme == "ai_semiconductor"
```

- [ ] **Step 5: 전체 테스트**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"`
Expected: 전체 PASS (기존 353 + 신규 약 75)

- [ ] **Step 6: 실데이터 검증 (완료 기준)**

`KRX_ID`/`KRX_PW`가 설정되어 있으면 수급·밸류에이션이 수집되어 신규 팩터가 실제 값을 갖는다. 없으면 그 팩터들은 NaN이 되고 게이트에서 FAIL로 보고되는데, **이는 정상 동작이다** — 데이터가 없다는 사실이 조용히 숨지 않고 드러나는 것이다. 어느 쪽이든 실행 결과를 보고에 남긴다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot data pipeline --market KR
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 000660 042700 058470 240810 --start 2022-01-01
.\.venv\Scripts\python.exe -m tradingbot research report --theme ai_semiconductor --start 2023-01-01 --end 2025-12-31
```

Expected: 테마 5종목에 대해 모멘텀·수급·가치 팩터의 IC/분위수/Walk-forward 표가 출력되고 `reports/research/*.md`가 생성된다. 팩터별 게이트 PASS/FAIL이 표시된다. **IC 값 자체는 검증하지 않는다** — 데이터가 말하는 대로 기록한다.

- [ ] **Step 7: 문서 갱신**

`README.md`의 확장 목록에 추가:

```markdown
- 테마 유니버스와 팩터 확장(`data/universe.py`, `factors/{flow,value,transform}.py`,
  `research/regime.py`): 날짜별 테마 멤버, 수급·가치 팩터, 표준화·가중결합,
  200일선 국면 필터와 `research report --theme` — M8/M9
```

`README.md`의 "현재 범위 안내"에서 두 항목을 제거한다 (이제 사실이 아니다): 가격 캐시 미갱신, 품질 검사 미연결. 대신 한 줄 추가:

```markdown
`data pipeline`이 가격 캐시 갱신과 수집 후 품질 검사까지 수행합니다. 품질 문제는
배치 결과의 각 소스 줄에 `quality=...`로 표시됩니다.
```

`docs/architecture.md` §7 표에 행 추가:

```markdown
| 테마 유니버스 | `src/tradingbot/data/universe.py` |
| 수급·가치 팩터 / 표준화 | `src/tradingbot/factors/{flow,value,transform}.py` |
| 시장 국면 필터 | `src/tradingbot/research/regime.py` |
```

- [ ] **Step 8: 커밋**

```powershell
git add src/tradingbot/factors/registry.py src/tradingbot/cli.py src/tradingbot/data/pipeline.py config/research.toml tests/test_factors_registry_phase3.py tests/test_pipeline_quality.py tests/test_research_report.py README.md docs/architecture.md
git commit -m "M9: Wire theme research, new factors, and pipeline quality checks"
```

---

## 완료 기준 (스펙 §3 Phase 3)

- [ ] `tradingbot research report --theme ai_semiconductor`가 테마 종목에 대해 모멘텀·수급·가치 팩터의 IC/분위수/Walk-forward/게이트 결과를 낸다.
- [ ] 테마 유니버스가 날짜별로 해결되어, 편입 전·편출 후 종목이 조회되지 않는다.
- [ ] 신규 팩터가 `available_at` 배리어를 통과한 데이터만 사용한다 (룩어헤드 테스트로 고정).
- [ ] `data pipeline`이 가격 갱신과 품질 검사까지 수행하고, 품질 문제가 배치 결과에 드러난다.
- [ ] 전체 테스트 통과, 기존 회귀 없음.

## 알려진 한계 (Phase 4에서 다룸)

- 종합점수(`transform.combine`)는 만들어지지만 아직 **주문으로 이어지지 않는다**. 목표 비중 산출과 리밸런싱은 Phase 4다.
- 국면 필터는 계산만 하고 노출도를 실제로 조절하지 않는다 — 소비자가 Phase 4의 전략이다.
- 테마 멤버는 수동 정의이며 과거 편입일의 소급 작성 편향은 남는다 (스펙 §6에 기록됨).
- 섹터 중립화는 하지 않는다: 테마 유니버스는 정의상 한 섹터에 몰려 있어 중립화할 축이 없다.
