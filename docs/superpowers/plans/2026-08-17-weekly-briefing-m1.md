# 주간 브리핑 M1 — 계좌 읽기·쉬운 워딩·모바일 전달 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PC를 켜고 `주간 브리핑.bat`을 더블클릭하면, 봇이 토스증권 계좌를 읽어 마지막 실행 이후 무슨 일이 있었는지 주식 비전문가의 말로 정리해 텔레그램으로 보낸다. PC를 꺼도 그 메시지는 폰에 남는다.

**Architecture:** 주문 코드를 만들지 않는다. `Broker`(주문·체결 11개 메서드)와 별개로 `AccountReader` 포트 하나(`snapshot()`)를 신설하고, 그 위에 순수 계산(수익률)·순수 표현(브리핑)·단방향 전달(텔레그램)을 쌓는다. 기존 `ParquetCache`·`services.update_data`·`cli.py` 서브파서 패턴을 재사용한다.

**Tech Stack:** Python 3.13, pandas, requests(기존 의존성), pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-08-17-weekly-briefing-m1-design.md`
**배경:** `docs/weekly_briefing_design_review.md` §4.1(읽기 전용 포트 부재), §4.3(워딩), §8(로컬 실행 결정)

## Global Constraints

- **주문 관련 코드를 M1 diff에 넣지 않는다.** `broker/`는 열지도 않는다. 이것이 "M1의 어떤 버그도 돈을 움직이지 못한다"의 유일한 근거다.
- **토스 API 응답 스펙을 지어내지 않는다.** 이 계획을 쓴 세션에서 `developers.tossinvest.com` 접속이 차단되어 필드명을 확인하지 못했다. Task 6은 **실제 호출로 응답을 먼저 기록**하고 그 fixture에 맞춰 구현한다. 기억으로 필드명을 쓰면 통과하는 테스트가 틀린 코드를 보증한다 (`2026-08-12-event-alpha` 계획의 교훈 §7과 같은 함정).
- **증권사가 준 숫자를 우리가 다시 만들지 않는다.** 평단·수량·환율은 응답 값이 진실이다. `Portfolio.apply_fill`을 재사용하지 않는다.
- **모르는 것은 "측정 불가"로 남긴다. 0이나 추정치로 채우지 않는다.** `research/evaluation.py:269`가 walk-forward 승률에 쓰는 규칙과 같다.
- **기존 CLI 명령·백테스트·모의투자 동작 변경 금지.** 기존 테스트가 회귀 방지선이다.
- 테스트에서 **네트워크 접근 금지** (기록된 fixture + 주입된 fake transport만).
- 자격증명을 저장소에 커밋 금지. 실제 계좌 응답을 fixture로 저장할 때 **계좌번호·잔고를 가공값으로 바꾼다** (Task 6 Step 1).
- 파일 쓰기는 `encoding="utf-8"` 명시. **단 `.bat`은 예외** — 아래 참조.
- **`.bat` 파일은 ANSI/CP949로 저장한다.** `트레이딩봇 실행.bat` 상단 주석이 경고하는 대로, cmd는 OEM 코드페이지로 파싱해서 UTF-8 한글이 들어가면 파서가 어긋나 파일 전체가 깨진다. 새 `.bat`의 주석은 ASCII로만 쓴다.
- 커밋 접두사: 중간 `BRIEF(part):`, 마지막 `BRIEF:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `require_env(name: str, *, hint: str) -> str` (`data/credentials.py`) — 빈 값이면 `MissingCredentialsError`
- `MissingCredentialsError` (`data/credentials.py`) — 재시도로 고칠 수 없는 설정 문제
- `load_config(path=None) -> dict` / `resolve_project_path(value) -> Path` (`config.py`)
- `ParquetCache(root)` (`data/cache.py`): `.read(market, symbol) -> pd.DataFrame` (없으면 `FileNotFoundError`), `.path(market, symbol) -> Path`, `.update(market, symbol, start=None, end=None) -> pd.DataFrame`
  - `read()`가 돌려주는 프레임은 `DatetimeIndex` + `open/high/low/close/volume` 컬럼
- `update_data(config, *, market, symbols, start=None, end=None, data_root=None) -> list[DataUpdateResult]` (`services.py`)
- `build_cache(config, data_root=None) -> ParquetCache` (`services.py`)
- `get_logger(name) -> logging.Logger` (`utils/log.py`)
- `SourceResult(name, status, rows, message)` / `PipelineResult(...).to_dict()` (`data/pipeline.py:42`) — 실행 로그 JSON 형태의 본보기
- `cli.py` 서브파서 관례: `subparsers.add_parser(...)` → `parser.set_defaults(handler=cmd_xxx)`, `cmd_xxx(args) -> int`
- `.gitignore`가 `state/`를 통째로 무시한다 — 토큰 캐시와 스냅샷은 자동으로 커밋에서 제외된다

## 태스크 순서와 그 이유

1~5는 **네트워크 없이 전부 확정 가능한 부분**이라 지금 테스트까지 다 쓸 수 있다. 6이 유일한 미지 구간(토스 응답 형태)이고, 7이 배선이다. 미지를 한 태스크에 가둬서, 그 앞의 다섯 개가 응답 스펙과 무관하게 완성되게 한다.

| Task | 산출물 | 네트워크 |
|---|---|---|
| 1 | `report/glossary.py` — 용어 사전 + 금칙어 검사 | 없음 |
| 2 | `account/base.py` — 포트·스냅샷·저장/로드 | 없음 |
| 3 | `account/returns.py` — 구간 수익률·환율 분해·측정 불가 | 없음 |
| 4 | `report/briefing.py` — 렌더러 | 없음 |
| 5 | `notify/` — 텔레그램 | 주입된 transport |
| 6 | `account/toss.py` — 토스 어댑터 | **실호출 1회로 fixture 확보** |
| 7 | CLI · `.bat` · `.env.template` | 전체 연결 |

---

### Task 1: 용어 사전과 금칙어 검사

**Files:**
- Create: `src/tradingbot/report/glossary.py`
- Test: `tests/test_report_glossary.py`

**Interfaces:**
- Consumes: 없음 (순수)
- Produces:
  - `glossary.TERMS: dict[str, Term]`, `Term(label, one_line, fmt)`
  - `glossary.label(key) -> str`, `glossary.explain(key) -> str`, `glossary.format_value(key, value) -> str`
  - `glossary.find_banned_terms(text: str) -> list[str]`
  - 상수: `BANNED`

**설계 근거:** 요구사항 3은 "이 세션에서 개발 후 보고, 알림 등에서도 유효"를 요구한다. 사전을 한 곳에 두고 **테스트로 강제**하지 않으면 출력 경로가 늘 때마다 워딩이 갈라진다. 금칙어 검사를 먼저 만드는 이유는 Task 4의 렌더러 테스트가 이것을 쓰기 때문이다.

**"설명을 붙이면 허용" 예외를 두지 않는다.** 예외가 생기는 순간 검사가 무의미해진다. 필요한 개념에는 새 이름을 준다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_report_glossary.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.report import glossary


class TestTerms:
    def test_every_term_has_a_korean_label_and_explanation(self):
        for key, term in glossary.TERMS.items():
            assert term.label.strip(), f"{key} has no label"
            assert term.one_line.strip(), f"{key} has no explanation"

    def test_no_label_or_explanation_contains_a_banned_word(self):
        # The dictionary is the thing that keeps jargon out; it must not be
        # the thing that smuggles jargon in.
        for key, term in glossary.TERMS.items():
            assert glossary.find_banned_terms(term.label) == [], key
            assert glossary.find_banned_terms(term.one_line) == [], key

    def test_the_terms_the_briefing_needs_exist(self):
        required = {
            "total_return",
            "period_return",
            "holding_return",
            "price_part",
            "fx_part",
            "cash_weight",
            "unmeasured",
        }
        assert required <= set(glossary.TERMS)

    def test_label_and_explain_read_from_the_dictionary(self):
        assert glossary.label("total_return") == glossary.TERMS["total_return"].label
        assert glossary.explain("total_return") == glossary.TERMS["total_return"].one_line

    def test_an_unknown_key_fails_loudly(self):
        # A typo must not silently render as an empty string in a report the
        # user is meant to trust.
        with pytest.raises(KeyError):
            glossary.label("no_such_term")


class TestFormatValue:
    def test_percent_terms_get_a_sign_and_one_decimal(self):
        assert glossary.format_value("period_return", 0.0123) == "+1.2%"
        assert glossary.format_value("period_return", -0.0456) == "-4.6%"

    def test_zero_is_not_signed_as_negative(self):
        assert glossary.format_value("period_return", 0.0) == "+0.0%"

    def test_none_renders_as_the_unmeasured_label(self):
        # "측정 불가" must survive formatting; a None that becomes 0.0% is the
        # exact lie this project refuses to tell.
        assert glossary.format_value("period_return", None) == glossary.label("unmeasured")


class TestFindBannedTerms:
    @pytest.mark.parametrize(
        "text",
        [
            "Sharpe 비율은 1.5입니다",
            "샤프 지수가 높습니다",
            "MDD는 30%입니다",
            "max drawdown was large",
            "exposure 94%",
            "profit factor 3.5",
        ],
    )
    def test_jargon_is_reported(self, text):
        assert glossary.find_banned_terms(text) != []

    def test_matching_ignores_case(self):
        assert glossary.find_banned_terms("sharpe ratio") != []

    def test_plain_korean_passes(self):
        text = "지난 12일 동안 전체 자산은 1.2% 늘었습니다. 현금 비중은 8%입니다."
        assert glossary.find_banned_terms(text) == []

    def test_every_hit_is_reported_not_just_the_first(self):
        hits = glossary.find_banned_terms("Sharpe와 MDD를 함께 봅니다")
        assert len(hits) >= 2

    def test_empty_text_has_no_hits(self):
        assert glossary.find_banned_terms("") == []
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_glossary.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.report.glossary'`

- [x] **Step 3: 구현**

`src/tradingbot/report/glossary.py`:

```python
"""One place that decides how numbers are named for a non-expert reader.

Requirement 3 asks that the plain wording hold not just in this milestone but
in every report and alert that comes later. Wording spread across renderers
drifts apart the moment a second renderer exists, so it lives here and a test
enforces it: anything user-facing goes through these labels, and the jargon
list must not appear in the output at all.

There is deliberately no "allowed if you explain it" escape hatch. An
exception makes the check unenforceable, and the concepts that need saying
are better served by a new plain name than by a glossary footnote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


def _pct(value: float) -> str:
    return f"{value:+.1%}".replace("+-", "-")


def _plain(value: float) -> str:
    return f"{value:,.0f}"


@dataclass(frozen=True)
class Term:
    label: str
    one_line: str
    fmt: Callable[[float], str]


TERMS: dict[str, Term] = {
    "total_return": Term(
        "전체 수익률",
        "처음 넣은 돈 대비 지금까지 얼마나 늘었는지",
        _pct,
    ),
    "period_return": Term(
        "이번 기간 수익률",
        "지난번에 확인한 뒤로 얼마나 달라졌는지",
        _pct,
    ),
    "holding_return": Term(
        "종목 수익률",
        "이 종목을 산 가격 대비 지금 가격이 얼마나 달라졌는지",
        _pct,
    ),
    "price_part": Term(
        "주가가 움직인 몫",
        "수익률 중 주가 자체가 오르내려서 생긴 부분",
        _pct,
    ),
    "fx_part": Term(
        "환율이 움직인 몫",
        "수익률 중 원달러 환율이 달라져서 생긴 부분",
        _pct,
    ),
    "cash_weight": Term(
        "현금 비중",
        "전체 자산 중 아직 투자하지 않고 남겨둔 돈의 비율",
        _pct,
    ),
    "total_value": Term(
        "전체 평가금액",
        "지금 계좌에 있는 돈과 주식을 원화로 모두 더한 값",
        _plain,
    ),
    "unmeasured": Term(
        "측정 불가",
        "숫자를 믿을 수 없어서 계산하지 않았다는 뜻",
        _pct,
    ),
}

# Jargon that must never reach the reader as-is. Matched case-insensitively
# against the rendered text.
BANNED: tuple[str, ...] = (
    "sharpe",
    "샤프",
    "mdd",
    "drawdown",
    "드로다운",
    "exposure",
    "익스포저",
    "profit factor",
    "equity curve",
    "cagr",
    "twr",
    "volatility",
    "변동성 역가중",
    "alpha",
    "beta",
    "rebalanc",
)


def label(key: str) -> str:
    """Reader-facing name. Raises on an unknown key, on purpose.

    A typo that renders as an empty string would ship a report with a blank
    where a number's name should be, and nothing would fail.
    """
    return TERMS[key].label


def explain(key: str) -> str:
    return TERMS[key].one_line


def format_value(key: str, value: float | None) -> str:
    """Format a value, or say it could not be measured.

    None is not zero. A missing return rendered as +0.0% is precisely the
    false statement this project refuses to make.
    """
    if value is None:
        return label("unmeasured")
    return TERMS[key].fmt(value)


def find_banned_terms(text: str) -> list[str]:
    """Every jargon term present in `text`, for the enforcement test."""
    lowered = (text or "").lower()
    return [term for term in BANNED if term in lowered]
```

- [x] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_glossary.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

> `_pct`가 `f"{value:+.1%}"`로 음수에 `+-`를 만들지 않는지 실행으로 확인한다. 파이썬은 `-4.6%`를 바르게 내지만, 확인 없이 넘어가면 첫 마이너스 수익률 주에 처음 발견하게 된다.

- [x] **Step 5: 커밋**

```powershell
git add src/tradingbot/report/glossary.py tests/test_report_glossary.py
git commit -m "BRIEF(part): Put the reader-facing wording in one enforceable place"
```

---

### Task 2: 읽기 전용 계좌 포트와 스냅샷 저장소

**Files:**
- Create: `src/tradingbot/account/__init__.py`, `src/tradingbot/account/base.py`
- Test: `tests/test_account_base.py`

**Interfaces:**
- Consumes: 없음 (순수 + 파일 IO)
- Produces:
  - `Holding(symbol, market, qty, qty_display, avg_price, last_price, currency)` — frozen
  - `AccountSnapshot(as_of, holdings, cash, fx_to_krw, fx_source)` — frozen
    - `.value_krw() -> float`, `.holding_value_krw(h) -> float`, `.cash_krw() -> float`, `.weights_krw() -> dict[str, float]`
  - `AccountReader` Protocol: `snapshot() -> AccountSnapshot`
  - `save_snapshot(snapshot, root) -> Path`, `load_snapshots(root) -> list[AccountSnapshot]`, `load_latest(root) -> AccountSnapshot | None`
  - 상수: `SNAPSHOT_SCHEMA_VERSION`

**설계 근거:** `Broker`와 별개 포트인 이유는 스펙 §4.1에 있다 — 계좌를 읽으려고 주문 메서드 11개를 구현할 이유가 없고, 만들지 않은 주문 코드는 오작동할 수 없다.

**파일명이 타임스탬프인 이유:** 손으로 켜므로 실행 간격이 불규칙하다. 주차(`YYYY-Www`)를 키로 쓰면 자주 켠 주에는 덮어쓰고 안 켠 주에는 구멍이 난다.

**`weights_krw()`가 지금 필요한 이유:** M3의 `plan_rebalance(current_weights=...)` 입력이 바로 이 형태다. 계산은 지금 해두고 소비는 M3에서 한다 — 새 추상화를 만드는 게 아니라 이미 있는 함수의 입력 형태에 맞추는 것뿐이다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_account_base.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import (
    AccountSnapshot,
    Holding,
    load_latest,
    load_snapshots,
    save_snapshot,
)

KST = timezone(timedelta(hours=9))


def holding(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=77000.0, market="KR"):
    return Holding(
        symbol=symbol,
        market=market,
        qty=qty,
        qty_display=str(qty),
        avg_price=avg,
        last_price=last,
        currency=currency,
    )


def snapshot(when="2026-08-15T18:00:00+09:00", holdings=None, cash=None, fx=None):
    return AccountSnapshot(
        as_of=datetime.fromisoformat(when),
        holdings=tuple(holdings if holdings is not None else [holding()]),
        cash=cash if cash is not None else {"KRW": 300_000.0},
        fx_to_krw=fx if fx is not None else {"KRW": 1.0, "USD": 1350.0},
        fx_source="broker",
    )


class TestValuation:
    def test_krw_holding_is_qty_times_price(self):
        snap = snapshot()
        assert snap.holding_value_krw(snap.holdings[0]) == pytest.approx(770_000.0)

    def test_usd_holding_is_converted_at_the_snapshot_rate(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[soxl])
        assert snap.holding_value_krw(soxl) == pytest.approx(5 * 30.0 * 1350.0)

    def test_total_value_adds_cash_in_every_currency(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[holding(), soxl], cash={"KRW": 300_000.0, "USD": 100.0})
        expected = 770_000.0 + 5 * 30.0 * 1350.0 + 300_000.0 + 100.0 * 1350.0
        assert snap.value_krw() == pytest.approx(expected)

    def test_a_currency_without_a_rate_fails_loudly(self):
        # Silently treating an unknown currency as 1:1 would understate a US
        # position by a factor of ~1350 and look like a crash.
        odd = holding(symbol="7203", currency="JPY", market="US")
        snap = snapshot(holdings=[odd])
        with pytest.raises(KeyError):
            snap.value_krw()

    def test_weights_sum_with_cash_to_one(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[holding(), soxl])
        weights = snap.weights_krw()
        cash_weight = snap.cash_krw() / snap.value_krw()
        assert sum(weights.values()) + cash_weight == pytest.approx(1.0)

    def test_an_empty_account_has_no_weights_and_does_not_divide_by_zero(self):
        snap = snapshot(holdings=[], cash={"KRW": 0.0})
        assert snap.value_krw() == pytest.approx(0.0)
        assert snap.weights_krw() == {}


class TestPersistence:
    def test_round_trips_through_json(self, tmp_path):
        original = snapshot()
        save_snapshot(original, tmp_path)
        assert load_latest(tmp_path) == original

    def test_the_timezone_survives(self, tmp_path):
        # A snapshot that comes back naive would silently shift every interval
        # length by nine hours.
        original = snapshot()
        save_snapshot(original, tmp_path)
        assert load_latest(tmp_path).as_of.utcoffset() == original.as_of.utcoffset()

    def test_two_runs_on_one_day_both_survive(self, tmp_path):
        save_snapshot(snapshot("2026-08-15T09:00:00+09:00"), tmp_path)
        save_snapshot(snapshot("2026-08-15T18:00:00+09:00"), tmp_path)
        assert len(load_snapshots(tmp_path)) == 2

    def test_snapshots_come_back_oldest_first(self, tmp_path):
        save_snapshot(snapshot("2026-08-15T18:00:00+09:00"), tmp_path)
        save_snapshot(snapshot("2026-08-01T18:00:00+09:00"), tmp_path)
        loaded = load_snapshots(tmp_path)
        assert [s.as_of.isoformat() for s in loaded] == [
            "2026-08-01T18:00:00+09:00",
            "2026-08-15T18:00:00+09:00",
        ]

    def test_latest_is_none_on_a_fresh_install(self, tmp_path):
        assert load_latest(tmp_path) is None

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert load_snapshots(tmp_path / "nope") == []

    def test_display_quantity_is_preserved_verbatim(self, tmp_path):
        # The whole point: the report must show what the Toss app shows.
        odd = Holding(
            symbol="SOXL", market="US", qty=1.2345678, qty_display="1.2345678",
            avg_price=20.0, last_price=30.0, currency="USD",
        )
        save_snapshot(snapshot(holdings=[odd]), tmp_path)
        assert load_latest(tmp_path).holdings[0].qty_display == "1.2345678"

    def test_a_corrupt_snapshot_raises_rather_than_being_skipped(self, tmp_path):
        save_snapshot(snapshot(), tmp_path)
        broken = tmp_path / "20260101T000000.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_snapshots(tmp_path)

    def test_an_unknown_schema_version_raises(self, tmp_path):
        import json

        path = save_snapshot(snapshot(), tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 999
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_snapshots(tmp_path)
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_account_base.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.account'`

- [x] **Step 3: 구현**

`src/tradingbot/account/__init__.py`: 빈 파일.

`src/tradingbot/account/base.py`:

```python
"""Read-only view of a real brokerage account.

Deliberately separate from `broker.base.Broker`. That interface exists to
simulate fills — submit, cancel, expire, mark-to-market, eleven methods in
all — and none of it is needed to answer "what do I hold right now". Keeping
the read path apart means this milestone ships without a single line that
could place an order.

Everything here is what the broker said, not what we computed. Average price
especially: rebuilding it from fills drifts away from the app's number as
soon as fees, splits, or a trade made by hand in the app enter the picture,
and a report that disagrees with the app is a report nobody believes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Holding:
    symbol: str
    market: str        # "KR" | "US"
    qty: float         # for arithmetic
    qty_display: str   # exactly as the broker printed it; use this on screen
    avg_price: float   # broker's figure, in `currency`
    last_price: float
    currency: str      # "KRW" | "USD"


@dataclass(frozen=True)
class AccountSnapshot:
    as_of: datetime                # the broker's timestamp, not our clock
    holdings: tuple[Holding, ...]
    cash: dict[str, float]         # currency -> deposit
    fx_to_krw: dict[str, float]    # currency -> won per unit
    fx_source: str                 # "broker" | "macro" — which rate this is

    def rate(self, currency: str) -> float:
        """Won per unit of `currency`. Missing is an error, not 1.0.

        Defaulting would value a US position at a 1350th of its worth and
        render it as a catastrophic loss.
        """
        return self.fx_to_krw[currency]

    def holding_value_krw(self, holding: Holding) -> float:
        return holding.qty * holding.last_price * self.rate(holding.currency)

    def cash_krw(self) -> float:
        return sum(amount * self.rate(cur) for cur, amount in self.cash.items())

    def value_krw(self) -> float:
        return sum(self.holding_value_krw(h) for h in self.holdings) + self.cash_krw()

    def weights_krw(self) -> dict[str, float]:
        """Each holding's share of total value — `plan_rebalance`'s input shape."""
        total = self.value_krw()
        if total <= 0:
            return {}
        return {h.symbol: self.holding_value_krw(h) / total for h in self.holdings}


class AccountReader(Protocol):
    """One method. That is the entire contract this milestone needs."""

    def snapshot(self) -> AccountSnapshot: ...


def save_snapshot(snapshot: AccountSnapshot, root: str | Path) -> Path:
    """Write one snapshot, keyed by when it was taken.

    Timestamped rather than keyed by ISO week because runs are manual and
    irregular: a week key would overwrite the eager weeks and leave holes in
    the quiet ones, and this file is the only record the return chain has.
    """
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "as_of": snapshot.as_of.isoformat(),
        "holdings": [asdict(h) for h in snapshot.holdings],
        "cash": snapshot.cash,
        "fx_to_krw": snapshot.fx_to_krw,
        "fx_source": snapshot.fx_source,
    }
    path = directory / f"{snapshot.as_of.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_one(path: Path) -> AccountSnapshot:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt account snapshot: {path}") from exc

    version = payload.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Unknown snapshot schema {version} in {path} "
            f"(this build reads {SNAPSHOT_SCHEMA_VERSION})"
        )
    return AccountSnapshot(
        as_of=datetime.fromisoformat(payload["as_of"]),
        holdings=tuple(Holding(**row) for row in payload["holdings"]),
        cash=dict(payload["cash"]),
        fx_to_krw=dict(payload["fx_to_krw"]),
        fx_source=payload["fx_source"],
    )


def load_snapshots(root: str | Path) -> list[AccountSnapshot]:
    """Every stored snapshot, oldest first.

    A corrupt or unreadable file raises rather than being skipped: a silently
    dropped snapshot would stretch the next interval across it and report a
    return for a period nobody measured.
    """
    directory = Path(root)
    if not directory.exists():
        return []
    snapshots = [_load_one(path) for path in sorted(directory.glob("*.json"))]
    return sorted(snapshots, key=lambda s: s.as_of)


def load_latest(root: str | Path) -> AccountSnapshot | None:
    snapshots = load_snapshots(root)
    return snapshots[-1] if snapshots else None
```

- [x] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_account_base.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

- [x] **Step 5: 커밋**

```powershell
git add src/tradingbot/account tests/test_account_base.py
git commit -m "BRIEF(part): Read an account without implementing an order path"
```

---

### Task 3: 구간 수익률 — 환율 분해와 측정 불가

**Files:**
- Create: `src/tradingbot/account/returns.py`
- Test: `tests/test_account_returns.py`

**Interfaces:**
- Consumes: `AccountSnapshot`, `Holding` (Task 2)
- Produces:
  - `IntervalReturn(days, start_value_krw, end_value_krw, measured, reason, return_pct, price_part_pct, fx_part_pct)` — frozen
  - `interval_return(prev, curr, *, net_flow_krw=None, tolerance=0.01) -> IntervalReturn`
  - `holding_return(h) -> float` — 통화 기준
  - `explained_change_krw(prev, curr) -> float`
  - 상수: `DEFAULT_TOLERANCE = 0.01`

**설계 근거 — 스펙 §4.2를 한 군데 고친다.** 스펙은 "종목별 수익률에 환율 기여를 분리해 두 줄로 낸다"고 썼지만, **누적 종목 수익률에는 그게 불가능하다.** 평단은 USD로 오고 **매수 시점 환율은 응답에 없다.** 그래서:

- **종목별 누적 수익률은 통화 기준으로만** 낸다 ("달러 기준 +12%"). 원화 환산 누적은 매입 원화금액이 응답에 있을 때만 (Task 6에서 확인).
- **환율 분해는 구간 단위로 한다.** 두 스냅샷 모두 환율을 갖고 있으므로 정확히 나눌 수 있다. 브리핑이 매번 보여주는 것도 구간이다.

**입출금 판별.** 토스에 입출금 내역 API가 있으면 `net_flow_krw`로 넘어온다. 없으면 `None`이고, 그때는 **가격으로 설명되는 변화**와 실제 변화를 비교한다.

```
explained = Σ (직전 보유수량 × (지금 원화가격 − 직전 원화가격))
unexplained = (지금 총액 − 직전 총액) − explained
|unexplained| / 직전 총액 > tolerance  →  측정 불가
```

이 판별은 입금과 직접 매매를 **구분하지 못한다.** 그래서 사유 문구가 둘 다 묻는다. 구분하려 억지 규칙을 넣지 않는 이유는, 틀린 구분이 틀린 수익률을 만들기 때문이다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_account_returns.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.account.returns import holding_return, interval_return

KST = timezone(timedelta(hours=9))


def h(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=70000.0, market="KR"):
    return Holding(
        symbol=symbol, market=market, qty=qty, qty_display=str(qty),
        avg_price=avg, last_price=last, currency=currency,
    )


def snap(day, holdings, cash=0.0, usd=1350.0):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, 9, 0, tzinfo=KST),
        holdings=tuple(holdings),
        cash={"KRW": cash},
        fx_to_krw={"KRW": 1.0, "USD": usd},
        fx_source="broker",
    )


class TestHoldingReturn:
    def test_gain_against_the_brokers_average_price(self):
        assert holding_return(h(avg=70000.0, last=77000.0)) == pytest.approx(0.10)

    def test_loss_is_negative(self):
        assert holding_return(h(avg=70000.0, last=63000.0)) == pytest.approx(-0.10)

    def test_a_zero_average_price_is_unmeasured_not_infinite(self):
        assert holding_return(h(avg=0.0, last=100.0)) is None


class TestIntervalReturn:
    def test_days_come_from_the_broker_timestamps(self):
        result = interval_return(snap(1, [h()]), snap(13, [h()]))
        assert result.days == 12

    def test_a_pure_price_gain_is_measured(self):
        prev = snap(1, [h(last=70000.0)])
        curr = snap(8, [h(last=77000.0)])
        result = interval_return(prev, curr)
        assert result.measured
        assert result.return_pct == pytest.approx(0.10)

    def test_price_and_fx_are_separated_for_a_usd_holding(self):
        # Price +10% in dollars, won weakens 1350 -> 1400 (+3.7%).
        prev = snap(1, [h(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=20.0, market="US")], usd=1350.0)
        curr = snap(8, [h(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=22.0, market="US")], usd=1400.0)
        result = interval_return(prev, curr)
        assert result.measured
        assert result.price_part_pct == pytest.approx(0.10, abs=1e-9)
        assert result.fx_part_pct == pytest.approx(1400 / 1350 - 1, abs=1e-9)

    def test_a_krw_only_account_reports_no_fx_part(self):
        result = interval_return(snap(1, [h(last=70000.0)]), snap(8, [h(last=77000.0)]))
        assert result.fx_part_pct == pytest.approx(0.0)

    def test_a_known_deposit_is_excluded_from_the_return(self):
        # Value doubles, but half of it was deposited. The return is the price
        # move only.
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        result = interval_return(prev, curr, net_flow_krw=700_000.0)
        assert result.measured
        assert result.return_pct == pytest.approx(0.0)

    def test_an_unexplained_jump_is_reported_unmeasured(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        result = interval_return(prev, curr)
        assert not result.measured
        assert result.return_pct is None
        assert result.reason

    def test_the_unmeasured_reason_asks_about_both_causes(self):
        # The check cannot tell a deposit from a trade made by hand, so the
        # question must cover both rather than assert one.
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        reason = interval_return(prev, curr).reason
        assert "입출금" in reason and "매매" in reason

    def test_small_drift_stays_within_tolerance(self):
        # Fees and rounding must not push every week into "unmeasured".
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=1_000.0)  # 0.14% of 700,000
        assert interval_return(prev, curr).measured

    def test_a_new_symbol_bought_in_the_interval_is_flagged(self):
        prev = snap(1, [h(last=70000.0)], cash=700_000.0)
        curr = snap(8, [h(last=70000.0), h(symbol="000660", qty=10.0, avg=70000.0, last=70000.0)], cash=0.0)
        # Cash moved into shares — total is unchanged, so this stays measured.
        assert interval_return(prev, curr).measured

    def test_a_fully_sold_symbol_does_not_crash(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [], cash=700_000.0)
        assert interval_return(prev, curr).measured

    def test_a_zero_starting_value_is_unmeasured_not_a_division_error(self):
        prev = snap(1, [], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=0.0)
        result = interval_return(prev, curr)
        assert not result.measured
        assert result.return_pct is None

    def test_snapshots_in_the_wrong_order_are_rejected(self):
        with pytest.raises(ValueError):
            interval_return(snap(8, [h()]), snap(1, [h()]))

    def test_start_and_end_values_are_always_reported(self):
        # Even when the return is unmeasured, the reader still gets to see
        # what the account was worth at each end.
        result = interval_return(snap(1, [h(last=70000.0)], cash=0.0), snap(8, [h(last=70000.0)], cash=700_000.0))
        assert result.start_value_krw == pytest.approx(700_000.0)
        assert result.end_value_krw == pytest.approx(1_400_000.0)
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_account_returns.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.account.returns'`

- [x] **Step 3: 구현**

`src/tradingbot/account/returns.py`:

```python
"""Returns on a real account, where money comes and goes.

`report/metrics.py` divides by the starting cash, which is right for a
backtest and wrong here: a deposit would show up as a gain. So each interval
is measured between two snapshots with external cash flows taken out.

When the flow is unknown the interval is not guessed at. The check compares
what prices explain against what actually happened, and anything left over
means something happened that prices do not account for — a transfer, or a
trade made by hand in the app. It cannot tell those two apart, so it says so
and reports no number, the same way an under-sampled walk-forward win rate is
reported unmeasured instead of passed.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.account.base import AccountSnapshot, Holding

DEFAULT_TOLERANCE = 0.01

_UNEXPLAINED = (
    "이 기간에 설명되지 않는 금액 변화가 있어서 수익률을 계산하지 않았습니다. "
    "입출금을 하셨거나, 앱에서 직접 매매하신 적이 있나요?"
)
_NO_START_VALUE = "직전 기록 시점의 평가금액이 0이라 수익률을 낼 수 없습니다."


@dataclass(frozen=True)
class IntervalReturn:
    days: int
    start_value_krw: float
    end_value_krw: float
    measured: bool
    reason: str | None
    return_pct: float | None
    price_part_pct: float | None
    fx_part_pct: float | None


def holding_return(holding: Holding) -> float | None:
    """Gain against the broker's average price, in the holding's own currency.

    Not converted to won: the purchase-time exchange rate is not in the
    balance response, so a won-denominated figure since purchase cannot be
    computed honestly. The interval figures below do carry the fx split,
    because there both rates are known.
    """
    if holding.avg_price <= 0:
        return None
    return holding.last_price / holding.avg_price - 1


def explained_change_krw(prev: AccountSnapshot, curr: AccountSnapshot) -> float:
    """Won change the two snapshots' prices and rates account for.

    Uses the earlier quantities on purpose — this is the buy-and-hold part of
    the move. Shares acquired during the interval are not explained by it,
    which is what makes the leftover a usable signal.
    """
    latest = {h.symbol: h for h in curr.holdings}
    total = 0.0
    for held in prev.holdings:
        now = latest.get(held.symbol)
        end_price_krw = (
            now.last_price * curr.rate(now.currency)
            if now is not None
            else held.last_price * curr.rate(held.currency)
        )
        start_price_krw = held.last_price * prev.rate(held.currency)
        total += held.qty * (end_price_krw - start_price_krw)
    return total


def _fx_part(prev: AccountSnapshot, curr: AccountSnapshot) -> float:
    """Weighted rate move across the currencies actually held at the start."""
    weights: dict[str, float] = {}
    for held in prev.holdings:
        weights[held.currency] = weights.get(held.currency, 0.0) + prev.holding_value_krw(held)
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return sum(
        (weight / total) * (curr.rate(cur) / prev.rate(cur) - 1)
        for cur, weight in weights.items()
    )


def interval_return(
    prev: AccountSnapshot,
    curr: AccountSnapshot,
    *,
    net_flow_krw: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> IntervalReturn:
    """Return between two snapshots, or a stated reason for having none."""
    if curr.as_of < prev.as_of:
        raise ValueError("interval_return: curr.as_of is earlier than prev.as_of")

    days = (curr.as_of - prev.as_of).days
    start = prev.value_krw()
    end = curr.value_krw()
    blank = dict(
        days=days, start_value_krw=start, end_value_krw=end,
        return_pct=None, price_part_pct=None, fx_part_pct=None,
    )

    if start <= 0:
        return IntervalReturn(measured=False, reason=_NO_START_VALUE, **blank)

    if net_flow_krw is None:
        leftover = (end - start) - explained_change_krw(prev, curr)
        if abs(leftover) / start > tolerance:
            return IntervalReturn(measured=False, reason=_UNEXPLAINED, **blank)
        flow = 0.0
    else:
        flow = float(net_flow_krw)

    # Flows are treated as arriving at the start of the interval. With one
    # snapshot at each end there is nothing finer to go on, and pretending
    # otherwise would dress an assumption up as precision.
    total = (end - flow) / start - 1
    fx = _fx_part(prev, curr)
    return IntervalReturn(
        days=days,
        start_value_krw=start,
        end_value_krw=end,
        measured=True,
        reason=None,
        return_pct=total,
        price_part_pct=total - fx,
        fx_part_pct=fx,
    )
```

- [x] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_account_returns.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

> `test_price_and_fx_are_separated_for_a_usd_holding`이 미묘하다. `price_part = total − fx`는 곱셈 분해의 근사이므로 교차항만큼 어긋난다. 테스트가 요구하는 정밀도(`abs=1e-9`)에서 실패하면 **테스트를 느슨하게 만들지 말고** `_fx_part`와 `price_part` 계산을 곱셈 분해(`(1+total) = (1+price)(1+fx)` → `price = (1+total)/(1+fx) − 1`)로 바꾼다. 사용자에게 "주가 몫 + 환율 몫 = 전체"로 보여줄 것이므로 덧셈 표기를 유지하되, 두 몫의 합이 전체와 0.1%p 이상 어긋나지 않아야 한다.

- [x] **Step 5: 커밋**

```powershell
git add src/tradingbot/account/returns.py tests/test_account_returns.py
git commit -m "BRIEF(part): Measure returns between snapshots, or say why not"
```

---

### Task 4: 브리핑 렌더러

**Files:**
- Create: `src/tradingbot/report/briefing.py`
- Test: `tests/test_report_briefing.py`

**Interfaces:**
- Consumes: `glossary` (Task 1), `AccountSnapshot` (Task 2), `interval_return`/`holding_return` (Task 3), `ParquetCache` (기존)
- Produces:
  - `render_briefing(curr, prev=None, *, price_history=None, now=None, long_gap_days=14) -> str`
  - `SECTIONS: tuple[str, ...]` — 섹션 이름 순서
  - `split_for_telegram(text, limit=4096) -> list[str]`

**설계 근거:** 섹션 목록으로 짜는 이유는 M2(뉴스)와 M3(제안)이 섹션 하나씩 추가되는 형태로 붙기 때문이다. `price_history`는 `{symbol: pd.Series}` — 캐시를 직접 읽지 않고 주입받아 테스트가 네트워크·디스크와 무관하게 돈다.

**기간을 두 번 쓴다.** 로컬 실행이라 간격이 불규칙하고, 같은 "+1.2%"라도 8일치와 23일치는 완전히 다른 말이다.

**레버리지 ETF 주석을 M1부터 넣는다.** 보유 중이면 이미 오해가 시작된다. 심볼 목록은 하드코딩하되, 목록에 없어도 브리핑이 깨지지 않는다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_report_briefing.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.report import glossary
from tradingbot.report.briefing import render_briefing, split_for_telegram

KST = timezone(timedelta(hours=9))


def h(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=77000.0, market="KR"):
    return Holding(
        symbol=symbol, market=market, qty=qty, qty_display=str(qty),
        avg_price=avg, last_price=last, currency=currency,
    )


def snap(day, holdings=None, cash=300_000.0, usd=1350.0, hour=9):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, hour, 0, tzinfo=KST),
        holdings=tuple(holdings if holdings is not None else [h()]),
        cash={"KRW": cash},
        fx_to_krw={"KRW": 1.0, "USD": usd},
        fx_source="broker",
    )


NOW = datetime(2026, 8, 15, 10, 0, tzinfo=KST)


class TestPlainLanguage:
    def test_the_rendered_briefing_contains_no_jargon(self):
        # This is the enforcement point for requirement 3.
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert glossary.find_banned_terms(text) == []

    def test_a_first_run_with_no_history_also_contains_no_jargon(self):
        assert glossary.find_banned_terms(render_briefing(snap(15), None, now=NOW)) == []

    def test_an_unmeasured_interval_also_contains_no_jargon(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(15, [h(last=70000.0)], cash=700_000.0)
        assert glossary.find_banned_terms(render_briefing(curr, prev, now=NOW)) == []


class TestContent:
    def test_the_first_line_says_how_many_days(self):
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert "14일" in text.splitlines()[0] or "14일" in text[:200]

    def test_a_first_run_says_so_instead_of_inventing_a_period(self):
        text = render_briefing(snap(15), None, now=NOW)
        assert "처음" in text

    def test_every_holding_appears(self):
        text = render_briefing(snap(15, [h(), h(symbol="000660")]), snap(1), now=NOW)
        assert "005930" in text and "000660" in text

    def test_the_display_quantity_is_what_gets_printed(self):
        odd = Holding(
            symbol="SOXL", market="US", qty=1.2345678, qty_display="1.2345678",
            avg_price=20.0, last_price=30.0, currency="USD",
        )
        text = render_briefing(snap(15, [odd]), snap(1, [odd]), now=NOW)
        assert "1.2345678" in text

    def test_an_unmeasured_return_shows_the_reason_not_a_zero(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(15, [h(last=70000.0)], cash=700_000.0)
        text = render_briefing(curr, prev, now=NOW)
        assert glossary.label("unmeasured") in text
        assert "입출금" in text

    def test_a_leveraged_etf_gets_its_warning(self):
        soxl = h(symbol="SOXL", currency="USD", market="US", avg=20.0, last=30.0)
        text = render_briefing(snap(15, [soxl]), snap(1, [soxl]), now=NOW)
        assert "3배" in text

    def test_no_leveraged_warning_when_none_is_held(self):
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert "3배" not in text

    def test_a_long_gap_is_called_out(self):
        text = render_briefing(snap(30), snap(1), now=datetime(2026, 8, 30, 10, 0, tzinfo=KST))
        assert "29일" in text
        assert "담지 못한" in text or "놓친" in text

    def test_a_short_gap_gets_no_gap_warning(self):
        text = render_briefing(snap(8), snap(1), now=datetime(2026, 8, 8, 10, 0, tzinfo=KST))
        assert "담지 못한" not in text

    def test_a_stale_broker_timestamp_is_flagged(self):
        # as_of is the broker's clock; if it lags our clock badly the numbers
        # are not current and the reader must be told.
        text = render_briefing(snap(15, hour=1), snap(1), now=datetime(2026, 8, 15, 20, 0, tzinfo=KST))
        assert "기준" in text

    def test_price_history_drives_the_trend_section(self):
        history = {"005930": pd.Series([70000.0, 72000.0, 77000.0])}
        text = render_briefing(snap(15), snap(1), price_history=history, now=NOW)
        assert "005930" in text

    def test_missing_price_history_does_not_break_rendering(self):
        text = render_briefing(snap(15), snap(1), price_history={}, now=NOW)
        assert text.strip()

    def test_an_empty_account_renders_without_crashing(self):
        text = render_briefing(snap(15, [], cash=0.0), None, now=NOW)
        assert text.strip()


class TestSplitForTelegram:
    def test_short_text_stays_in_one_message(self):
        assert len(split_for_telegram("짧은 브리핑")) == 1

    def test_long_text_is_split_under_the_limit(self):
        parts = split_for_telegram("\n\n".join(["가" * 1000] * 10), limit=4096)
        assert len(parts) > 1
        assert all(len(part) <= 4096 for part in parts)

    def test_nothing_is_lost_in_the_split(self):
        original = "\n\n".join([f"섹션{i}\n" + "나" * 900 for i in range(8)])
        assert "".join(split_for_telegram(original, limit=4096)).replace("\n", "") == original.replace("\n", "")

    def test_a_single_oversized_section_is_still_delivered(self):
        parts = split_for_telegram("다" * 9000, limit=4096)
        assert all(len(part) <= 4096 for part in parts)
        assert sum(len(part) for part in parts) >= 9000
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_briefing.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.report.briefing'`

- [x] **Step 3: 구현**

`src/tradingbot/report/briefing.py`를 작성한다. 위 테스트가 요구하는 동작을 전부 만족시키되, 아래 구조를 지킨다.

```python
SECTIONS = ("summary", "totals", "holdings", "trend", "notes")
```

- 섹션별로 `_render_summary(...) -> list[str]` 식의 함수를 하나씩 두고, `render_briefing`이 그것들을 순서대로 이어붙인다. **M2의 뉴스와 M3의 제안이 이 튜플에 이름 하나씩 추가되는 형태로 붙는다.**
- 모든 숫자는 `glossary.format_value(...)`를 거친다. 문자열을 직접 `f"{x:.1%}"`로 만들지 않는다 — 그 순간 사전이 우회된다.
- 수량은 `holding.qty_display`를 출력한다. `holding.qty`를 포맷하지 않는다.
- 레버리지 ETF 주석:
  ```python
  # Daily-reset leveraged ETFs. Held over more than a day, the multiple does
  # not hold — which is exactly the misunderstanding plain wording has to
  # prevent, and it starts the moment one of these is in the account.
  _LEVERAGED = {"SOXL", "SOXS", "TECL", "TECS", "TQQQ", "SQQQ", "FNGU", "LABU", "SPXL"}
  ```
  보유 종목과 교집합이 있을 때만 "3배 ETF는 하루 단위로 3배라서, 여러 날을 합치면 기초지수의 정확히 3배가 아닙니다" 한 줄을 `notes`에 넣는다.
- `now`는 기본값 `None`이면 `datetime.now(curr.as_of.tzinfo)`. 테스트가 시계를 주입할 수 있어야 한다.
- `curr.as_of`가 `now`보다 6시간 이상 이르면 `notes`에 기준 시각 경고를 넣는다.
- `split_for_telegram`은 빈 줄(`\n\n`) 경계에서 나누고, 한 덩어리가 한도를 넘으면 그것만 강제로 잘라 넣는다.

- [x] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_briefing.py tests\test_report_glossary.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

- [x] **Step 5: 커밋**

```powershell
git add src/tradingbot/report/briefing.py tests/test_report_briefing.py
git commit -m "BRIEF(part): Render the account in words a non-expert can read"
```

---

### Task 5: 텔레그램 전달

**Files:**
- Create: `src/tradingbot/notify/__init__.py`, `src/tradingbot/notify/base.py`, `src/tradingbot/notify/telegram.py`
- Test: `tests/test_notify_telegram.py`

**Interfaces:**
- Consumes: `require_env` (기존), `split_for_telegram` (Task 4)
- Produces:
  - `Notifier` Protocol: `send(text: str) -> None`
  - `TelegramNotifier(token, chat_id, transport=..., sleeper=...)`
  - `build_notifier() -> TelegramNotifier` — 환경변수에서 조립
  - `NotifyError` — 3회 재시도 후에도 실패

**설계 근거:** 로컬 실행인데도 텔레그램을 쓰는 이유는 **PC를 끈 뒤에도 폰에 남기 위해서**다. 콘솔이나 HTML로 끝내면 PC와 함께 사라지고, 요구사항 5에서 살아남은 부분이 정확히 이것이다.

`transport`를 주입 가능하게 두는 것은 `DartClient(transport=...)`와 같은 패턴이다 — 테스트가 네트워크 없이 돈다.

M1의 Notifier는 **단방향**이다. M3의 승인 버튼은 로컬 실행 덕에 같은 프로세스 안에서 콜백을 폴링할 수 있으므로(스펙 §4.6), 지금 양방향 추상화를 미리 만들지 않는다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_notify_telegram.py`:

```python
from __future__ import annotations

import pytest

from tradingbot.notify.telegram import NotifyError, TelegramNotifier


class FakeTransport:
    def __init__(self, failures=0, exc=None):
        self.calls: list[dict] = []
        self.failures = failures
        self.exc = exc or RuntimeError("telegram 500")

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append({"url": url, "payload": payload})
        if len(self.calls) <= self.failures:
            raise self.exc
        return {"ok": True}


def notifier(transport, **kwargs):
    return TelegramNotifier(
        token="T", chat_id="C", transport=transport, sleeper=lambda _s: None, **kwargs
    )


class TestSend:
    def test_posts_the_text_to_the_chat(self):
        transport = FakeTransport()
        notifier(transport).send("안녕하세요")
        assert len(transport.calls) == 1
        assert transport.calls[0]["payload"]["chat_id"] == "C"
        assert transport.calls[0]["payload"]["text"] == "안녕하세요"

    def test_the_token_is_in_the_url_not_the_payload(self):
        transport = FakeTransport()
        notifier(transport).send("안녕")
        assert "T" in transport.calls[0]["url"]
        assert "T" not in str(transport.calls[0]["payload"])

    def test_a_long_briefing_is_sent_as_several_messages(self):
        transport = FakeTransport()
        notifier(transport).send("\n\n".join(["가" * 1000] * 10))
        assert len(transport.calls) > 1

    def test_parts_are_sent_in_order(self):
        transport = FakeTransport()
        notifier(transport).send("첫째" + "\n\n" + "둘" * 3000 + "\n\n" + "마지막")
        texts = [call["payload"]["text"] for call in transport.calls]
        assert texts[0].startswith("첫째")
        assert texts[-1].endswith("마지막")


class TestRetry:
    def test_a_transient_failure_is_retried(self):
        transport = FakeTransport(failures=2)
        notifier(transport).send("안녕")
        assert len(transport.calls) == 3

    def test_giving_up_raises_rather_than_returning_quietly(self):
        # A notifier that swallows its own failure is worse than none: the run
        # would report success while the phone stayed silent.
        transport = FakeTransport(failures=99)
        with pytest.raises(NotifyError):
            notifier(transport).send("안녕")

    def test_it_stops_after_three_attempts(self):
        transport = FakeTransport(failures=99)
        with pytest.raises(NotifyError):
            notifier(transport).send("안녕")
        assert len(transport.calls) == 3

    def test_backoff_waits_between_attempts(self):
        waits: list[float] = []
        transport = FakeTransport(failures=2)
        TelegramNotifier(
            token="T", chat_id="C", transport=transport, sleeper=waits.append
        ).send("안녕")
        assert waits == [2, 4]

    def test_a_response_saying_not_ok_is_a_failure(self):
        class NotOk:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, payload):
                self.calls += 1
                return {"ok": False, "description": "chat not found"}

        transport = NotOk()
        with pytest.raises(NotifyError) as excinfo:
            notifier(transport).send("안녕")
        assert "chat not found" in str(excinfo.value)
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_notify_telegram.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.notify'`

- [x] **Step 3: 구현**

`src/tradingbot/notify/base.py`:

```python
"""Where a finished briefing goes.

One method, one direction. M3's approval button will need a reply path, but
running locally it can poll for the callback inside the same process while
the user is still at the keyboard — so there is nothing to generalise yet.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def send(self, text: str) -> None: ...
```

`src/tradingbot/notify/telegram.py`:

```python
"""Telegram Bot API delivery.

Chosen over printing to the console because the console dies with the PC.
The requirement that survived the move to local execution is that the
briefing outlive the session it was made in, and a message on the phone does
exactly that.

Failure is never swallowed. A notifier that reports success while the phone
stays silent is worse than no notifier at all: the run looks fine and the
reader simply never learns anything went wrong.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

from tradingbot.data.credentials import require_env
from tradingbot.report.briefing import split_for_telegram
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

API_BASE = "https://api.telegram.org"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)

Transport = Callable[[str, dict], dict]


class NotifyError(RuntimeError):
    """Delivery failed after every retry."""


def _requests_transport(url: str, payload: dict) -> dict:
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


class TelegramNotifier:
    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        transport: Transport = _requests_transport,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._transport = transport
        self._sleep = sleeper

    @property
    def _url(self) -> str:
        return f"{API_BASE}/bot{self._token}/sendMessage"

    def send(self, text: str) -> None:
        for part in split_for_telegram(text):
            self._send_one(part)

    def _send_one(self, text: str) -> None:
        payload = {"chat_id": self._chat_id, "text": text}
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = self._transport(self._url, payload)
            except Exception as exc:  # transport-level failure
                last = exc
            else:
                if result.get("ok"):
                    return
                last = NotifyError(
                    f"Telegram rejected the message: {result.get('description')}"
                )
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS[attempt])
        raise NotifyError(f"Telegram delivery failed after {MAX_ATTEMPTS} attempts") from last


def build_notifier() -> TelegramNotifier:
    hint = (
        "@BotFather에서 봇을 만들어 토큰을 받고, 그 봇과 대화를 시작한 뒤 "
        "chat id를 확인해 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID 환경변수로 "
        "등록하세요. 저장소에 커밋하지 마세요."
    )
    return TelegramNotifier(
        token=require_env("TELEGRAM_BOT_TOKEN", hint=hint),
        chat_id=require_env("TELEGRAM_CHAT_ID", hint=hint),
    )
```

`src/tradingbot/notify/__init__.py`: 빈 파일.

- [x] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_notify_telegram.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: PASS

> `test_a_response_saying_not_ok_is_a_failure`가 예외 메시지에 `description`을 요구한다. `raise ... from last`만으로는 최종 메시지에 안 들어갈 수 있으니, 마지막 `NotifyError`에 `last`의 내용을 이어 붙인다.

- [x] **Step 5: 커밋**

```powershell
git add src/tradingbot/notify tests/test_notify_telegram.py
git commit -m "BRIEF(part): Deliver the briefing somewhere it outlives the session"
```

---

### Task 6: 토스 어댑터 — **응답을 먼저 확인하고 나서 구현한다**

**Files:**
- Create: `src/tradingbot/account/toss.py`
- Create: `tests/data/toss_balance_sample.json` (Step 1에서 만든다)
- Test: `tests/test_account_toss.py`

**Interfaces:**
- Consumes: `require_env`, `MissingCredentialsError` (기존), `Holding`/`AccountSnapshot` (Task 2)
- Produces:
  - `TossCredentials(client_id, client_secret, account_no)`
  - `TossAccountReader(credentials, transport=..., token_store=...)` — `AccountReader` 구현
  - `to_snapshot(balance_payload: dict, *, fetched_at) -> AccountSnapshot` — **순수 함수**
  - `TossAuthError`, `TossIPNotAllowedError`

**⚠️ 이 태스크는 앞의 다섯 개와 다르게 진행한다.** 이 계획을 쓴 세션에서 토스 개발자 문서에 접근하지 못했다. 필드명·중첩 구조·숫자 타입(문자열인지 숫자인지)을 모르는 상태다. **기억으로 짐작해 쓰면, 통과하는 테스트가 틀린 매핑을 보증하게 된다.**

> **2026-08-19 갱신 — 이 태스크의 진행 방식은 별도 설계서로 대체되었다:**
> **`docs/superpowers/plans/2026-08-19-toss-account-reader-design.md`**
>
> 공식 기계판독 스펙(`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`,
> v1.2.14)을 확보해서 필드명·타입·nullable 여부·오류 코드가 모두 확정되었다. 그래서
> **실계좌 호출 없이 구현할 수 있고**, fixture는 실계좌 응답을 가공하는 대신 스펙의 공식
> 예시를 원문 그대로 쓴다 (근거: 설계서 §3.10). 아래 Step 1~5 대신 설계서 §10의 순서를
> 따른다. 실계좌 1회 대조는 설계서 §12의 후속 확인 항목으로 남는다.

- [ ] **Step 1: 실제 응답을 먼저 확보한다**

1. 토스증권 WTS → 설정 → Open API에서 `client_id` / `client_secret`을 발급하고, **개발 PC의 공인 IP를 허용 목록에 등록**한다.
2. 토큰을 받고 잔고를 한 번 호출하는 **일회용 스크립트**를 `scratch/`(gitignore 대상)에 만들어 응답 원문을 저장한다. 저장소에 커밋하지 않는다.
3. 응답을 `tests/data/toss_balance_sample.json`으로 옮기면서 **가공한다**:
   - 계좌번호·고객식별자는 `"0000000000"` 같은 더미로 교체
   - 금액·수량은 자릿수와 타입(문자열/숫자)을 **유지한 채** 값만 바꾼다
   - **구조·필드명·타입은 절대 바꾸지 않는다** — 그것이 이 fixture의 존재 이유다
4. 아래를 이 문서에 적어 넣는다 (다음 사람이 다시 알아낼 필요 없게):

```text
확인 결과 기록 — 2026-08-19, 공식 스펙 v1.2.14 에서 확인
- 잔고 엔드포인트:            GET /api/v1/holdings — 요약 + items 를 한 응답에 담는다
- 계좌 헤더:                  X-Tossinvest-Account: <accountSeq> (정수)
                              값은 GET /api/v1/accounts 의 result[].accountSeq
                              계좌번호가 아니다 → TOSS_ACCOUNT_NO 는 안전 확인용
- 토큰 엔드포인트 / expires_in: POST /oauth2/token (form-urlencoded), expires_in 은 초
                              (문서 예시 86400). client당 유효 토큰 1개,
                              재발급하면 이전 토큰이 즉시 무효화된다
- 국내·해외가 한 응답인가:     예. result.items 에 KR/US 모두, marketCountry enum = KR|US
- 보유 수량 필드 / 타입:       items[].quantity — 문자열 decimal (숫자 아님)
                              자릿수는 응답이 준 그대로 → qty_display 는 원문 그대로
- 매입단가 필드 / 통화:        items[].averagePurchasePrice — items[].currency 기준
- 현재가 필드:                items[].lastPrice — items[].currency 기준
- 원화 평가금액 필드:          없음(종목별). result.marketValue.amount.{krw,usd} 는
                              통화별 합산이며 원화 환산 합계가 아니다
- 적용 환율 필드:              없음 → GET /api/v1/exchange-rate 별도 호출.
                              rate(매수 환율) 대신 midRate(매매기준율)를 쓴다
- 원화 매입금액 필드:          없음 → 종목별 원화 누적 수익률은 M1에서 내지 않는다
- 예수금 필드 (통화별):        없음 → GET /api/v1/buying-power?currency=KRW|USD 의
                              result.cashBuyingPower. 예수금이 아니라 매수가능금액이다
- 응답 기준 시각 필드:         없음 → 호출 시각(KST)을 as_of 로 쓰고 로그에 남긴다.
                              결과적으로 브리핑의 6시간 지연 경고는 발동하지 않는다
- 입출금 내역 엔드포인트:      없음 (스펙 전체에 입출금 API 부재)
                              → Task 3 net_flow_krw 는 항상 None
- 읽기 전용 스코프:            없음. scopes = {} — 이 토큰으로 주문도 가능하다
                              → 모듈에 주문 경로 문자열이 없음을 테스트로 고정한다
```

- [ ] **Step 2: fixture에 맞춘 테스트 작성**

`tests/test_account_toss.py`. **아래는 뼈대다. 필드 접근은 Step 1의 실제 fixture에 맞춰 쓴다.**

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingbot.account.toss import (
    TossAccountReader,
    TossAuthError,
    TossCredentials,
    TossIPNotAllowedError,
    to_snapshot,
)

KST = timezone(timedelta(hours=9))
SAMPLE = json.loads(
    (Path(__file__).parent / "data" / "toss_balance_sample.json").read_text(encoding="utf-8")
)
FETCHED = datetime(2026, 8, 15, 9, 0, tzinfo=KST)


class TestToSnapshot:
    def test_maps_every_holding_in_the_sample(self):
        snapshot = to_snapshot(SAMPLE, fetched_at=FETCHED)
        assert len(snapshot.holdings) == <fixture의 보유 종목 수>

    def test_quantity_display_is_the_brokers_own_string(self):
        # The number on screen must match the Toss app exactly.
        snapshot = to_snapshot(SAMPLE, fetched_at=FETCHED)
        assert snapshot.holdings[0].qty_display == "<fixture의 원문 그대로>"

    def test_a_us_holding_is_marked_usd(self):
        ...

    def test_the_applied_exchange_rate_comes_from_the_response(self):
        snapshot = to_snapshot(SAMPLE, fetched_at=FETCHED)
        assert snapshot.fx_source == "broker"

    def test_a_missing_rate_falls_back_and_says_so(self):
        payload = json.loads(json.dumps(SAMPLE))
        # remove the rate field, then:
        snapshot = to_snapshot(payload, fetched_at=FETCHED, usdkrw=1350.0)
        assert snapshot.fx_source == "macro"

    def test_a_krw_only_account_needs_no_rate(self):
        ...

    def test_an_empty_account_produces_an_empty_snapshot(self):
        ...

    def test_an_unexpected_shape_raises_instead_of_producing_zeros(self):
        # A silently empty snapshot would read as "you sold everything".
        with pytest.raises((KeyError, ValueError)):
            to_snapshot({"unexpected": True}, fetched_at=FETCHED)


class TestErrors:
    def test_403_becomes_an_ip_error_naming_the_fix(self):
        # The most common failure in this setup: the home IP changed.
        def transport(method, url, **kwargs):
            raise _http_error(403)

        reader = TossAccountReader(
            TossCredentials("id", "secret", "0000-01"), transport=transport
        )
        with pytest.raises(TossIPNotAllowedError) as excinfo:
            reader.snapshot()
        assert "허용" in str(excinfo.value)

    def test_401_is_an_auth_error_not_an_ip_error(self):
        ...

    def test_a_cached_token_is_reused_within_its_lifetime(self):
        # Toss keeps one valid token per client; re-issuing invalidates the
        # previous one, so a second request must not fetch a new token.
        ...

    def test_an_expired_token_is_reissued(self):
        ...

    def test_the_token_cache_survives_a_new_reader(self, tmp_path):
        ...
```

- [ ] **Step 3: 구현**

`src/tradingbot/account/toss.py`. 지켜야 할 것:

- **`to_snapshot`을 순수 함수로 분리한다.** 응답 dict → `AccountSnapshot`. 네트워크 없이 fixture로 검증되는 유일한 방법이다.
- **토큰 캐시.** `state/toss_token.json`에 `{"access_token": ..., "expires_at": <ISO>}`. 만료 60초 전에만 재발급. **유효기간은 응답의 `expires_in`을 쓰고 상수로 박지 않는다.**
- **403 → `TossIPNotAllowedError`.** 메시지에 현재 공인 IP를 함께 넣는다:
  ```python
  # The home IP is not guaranteed stable, and a changed one fails with 403
  # even though the keys are correct. Print the address the caller needs to
  # paste into the Toss allowlist rather than making them go find it.
  ```
  공인 IP 조회가 실패해도 예외 자체는 반드시 뜨게 한다 — 진단 편의가 오류 보고를 삼키면 안 된다.
- **401 → `TossAuthError`**, 1회 재발급 후 재시도.
- 자격증명이 없으면 `MissingCredentialsError` (기존 패턴).
- 환율이 응답에 없으면 `usdkrw` 폴백을 쓰고 `fx_source="macro"`로 기록한다.
- 응답에 기준 시각이 없으면 호출 시각을 `as_of`로 쓰되, **그 사실을 로그로 남긴다.**

- [ ] **Step 4: 통과 확인 + 실계좌 1회**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_account_toss.py -v --basetemp="$env:TEMP\pytest_tmp"
```

그리고 실제 계좌로 한 번 호출해 **토스 앱 화면과 숫자가 일치하는지 눈으로 대조한다.** 수량·평단·평가금액 셋. 여기서 어긋나면 매핑이 틀린 것이고, 브리핑을 아무리 쉽게 써도 소용이 없다.

- [ ] **Step 5: 커밋**

```powershell
git add src/tradingbot/account/toss.py tests/test_account_toss.py tests/data/toss_balance_sample.json
git commit -m "BRIEF(part): Map the Toss balance response onto the read-only port"
```

---

### Task 7: CLI 배선과 실행 경로

**Files:**
- Modify: `src/tradingbot/cli.py`, `.env.template`, `README.md`
- Create: `src/tradingbot/briefing_service.py`, `주간 브리핑.bat`
- Test: `tests/test_briefing_service.py`

**Interfaces:**
- Consumes: Task 1~6 전부, `update_data`/`build_cache` (기존)
- Produces:
  - `run_briefing(config, *, reader, notifier, cache, state_root, skip_update=False, notify=True) -> BriefingResult`
  - `BriefingResult(started_at, finished_at, ok, text, snapshot_path, sent, messages)` — `.to_dict()`
  - CLI: `tradingbot briefing weekly [--dry-run] [--no-notify] [--skip-update]`

**설계 근거:** 조립 로직을 `briefing_service.py`에 두고 CLI는 인자만 넘긴다. `services.py`가 CLI와 GUI 공통 진입점인 것과 같은 이유다. 실행 로그는 `data/pipeline.py:250`의 `PipelineResult.to_dict()` 형태를 따른다.

**전송이 실패해도 브리핑 전문을 콘솔에 낸다.** 로컬 실행에서는 사용자가 화면 앞에 있으므로, 폰으로 못 갔어도 최소한 읽을 수는 있어야 한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_briefing_service.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.briefing_service import run_briefing
from tradingbot.notify.telegram import NotifyError

KST = timezone(timedelta(hours=9))


def snapshot(day=15):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, 9, 0, tzinfo=KST),
        holdings=(Holding("005930", "KR", 10.0, "10", 70000.0, 77000.0, "KRW"),),
        cash={"KRW": 300_000.0},
        fx_to_krw={"KRW": 1.0},
        fx_source="broker",
    )


class FakeReader:
    def __init__(self, snap=None, exc=None):
        self._snap = snap or snapshot()
        self._exc = exc

    def snapshot(self):
        if self._exc:
            raise self._exc
        return self._snap


class FakeNotifier:
    def __init__(self, exc=None):
        self.sent: list[str] = []
        self._exc = exc

    def send(self, text):
        if self._exc:
            raise self._exc
        self.sent.append(text)


def run(tmp_path, reader=None, notifier=None, **kwargs):
    return run_briefing(
        {},
        reader=reader or FakeReader(),
        notifier=notifier or FakeNotifier(),
        cache=None,
        state_root=tmp_path,
        skip_update=True,
        **kwargs,
    )


class TestHappyPath:
    def test_sends_the_briefing(self, tmp_path):
        notifier = FakeNotifier()
        result = run(tmp_path, notifier=notifier)
        assert result.ok and result.sent
        assert len(notifier.sent) == 1

    def test_stores_the_snapshot(self, tmp_path):
        run(tmp_path)
        assert len(list((tmp_path / "account").glob("*.json"))) == 1

    def test_a_second_run_compares_against_the_first(self, tmp_path):
        run(tmp_path, reader=FakeReader(snapshot(1)))
        result = run(tmp_path, reader=FakeReader(snapshot(15)))
        assert "14일" in result.text

    def test_writes_a_run_log(self, tmp_path):
        run(tmp_path)
        logs = list((tmp_path / "briefing_log").glob("*.json"))
        assert len(logs) == 1
        assert json.loads(logs[0].read_text(encoding="utf-8"))["ok"] is True


class TestNoNotify:
    def test_dry_run_renders_but_does_not_send(self, tmp_path):
        notifier = FakeNotifier()
        result = run(tmp_path, notifier=notifier, notify=False)
        assert result.text.strip()
        assert notifier.sent == []
        assert result.sent is False

    def test_dry_run_still_records_the_snapshot(self, tmp_path):
        # The snapshot is the return chain's only source; skipping it would
        # leave a hole that no later run can fill.
        run(tmp_path, notify=False)
        assert len(list((tmp_path / "account").glob("*.json"))) == 1


class TestFailures:
    def test_a_reader_failure_is_reported_not_swallowed(self, tmp_path):
        result = run(tmp_path, reader=FakeReader(exc=RuntimeError("403")))
        assert not result.ok
        assert "403" in " ".join(result.messages)

    def test_a_reader_failure_writes_no_snapshot(self, tmp_path):
        run(tmp_path, reader=FakeReader(exc=RuntimeError("boom")))
        assert list((tmp_path / "account").glob("*.json")) == []

    def test_a_send_failure_still_returns_the_text(self, tmp_path):
        # The user is at the keyboard; the console is the fallback screen.
        result = run(tmp_path, notifier=FakeNotifier(exc=NotifyError("no network")))
        assert not result.ok
        assert result.text.strip()

    def test_a_send_failure_keeps_the_snapshot(self, tmp_path):
        run(tmp_path, notifier=FakeNotifier(exc=NotifyError("no network")))
        assert len(list((tmp_path / "account").glob("*.json"))) == 1
```

- [x] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_briefing_service.py -v --basetemp="$env:TEMP\pytest_tmp"`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingbot.briefing_service'`

- [x] **Step 3: 구현**

`src/tradingbot/briefing_service.py`를 작성한다. 순서:

```
1. skip_update가 아니면 보유 종목의 가격 캐시를 갱신한다 (update_data)
   — 몇 주 만에 켰을 때 옛 가격으로 동향을 그리지 않기 위해서다.
   갱신 실패는 치명적이지 않다: messages에 남기고 계속한다.
2. reader.snapshot()  — 실패하면 여기서 끝. 스냅샷을 쓰지 않는다.
3. 직전 스냅샷 로드 (load_latest) — 반드시 새 스냅샷을 저장하기 **전에**
4. save_snapshot  — 전송 성공 여부와 무관하게 저장한다
5. render_briefing(curr, prev, price_history=...)
6. notify이면 notifier.send(text) — 실패해도 text는 결과에 담는다
7. 실행 로그 JSON 기록
```

3번과 4번의 순서가 중요하다. 먼저 저장하면 방금 저장한 것을 직전 스냅샷으로 읽어 구간이 0일이 된다.

CLI (`cli.py`)에 서브파서를 추가한다:

```python
    briefing_parser = subparsers.add_parser("briefing", help="계좌 현황 브리핑")
    briefing_subparsers = briefing_parser.add_subparsers(dest="briefing_command")
    weekly_parser = briefing_subparsers.add_parser("weekly", help="주간 계좌 브리핑을 만들어 보낸다")
    weekly_parser.add_argument("--dry-run", action="store_true", help="렌더까지만 하고 보내지 않는다")
    weekly_parser.add_argument("--no-notify", action="store_true", help="전송 생략 (--dry-run과 동일)")
    weekly_parser.add_argument("--skip-update", action="store_true", help="가격 캐시 갱신 생략")
    weekly_parser.set_defaults(handler=cmd_briefing_weekly)
```

`cmd_briefing_weekly(args) -> int`는 성공 0, 실패 1을 반환하고 **브리핑 전문을 항상 print한다.**

- [x] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"
```
Expected: 전체 통과 (기존 테스트 포함 — 회귀 없음)

- [x] **Step 5: 실행 경로와 문서**

1. **`주간 브리핑.bat`** — `트레이딩봇 실행.bat`을 본떠 만들되:
   - **ANSI/CP949로 저장한다. 주석은 ASCII만.** UTF-8로 저장하면 파일 전체가 깨진다.
   - `pythonw.exe`가 아니라 `python.exe`로 실행한다 (콘솔 출력이 보여야 한다)
   - 끝에 `pause`를 넣어 성공·실패 모두 읽을 수 있게 한다
2. **`.env.template`**에 항목을 추가한다 (값은 비운 채):
   ```
   # 토스증권 오픈 API — WTS 설정 > Open API에서 발급.
   # 발급 후 이 PC의 공인 IP를 반드시 허용 목록에 등록하세요.
   TOSS_CLIENT_ID=
   TOSS_CLIENT_SECRET=
   TOSS_ACCOUNT_NO=

   # 텔레그램 봇 — @BotFather에서 봇 생성 후 토큰 발급, 봇과 대화 시작 후 chat id 확인.
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=
   ```
3. **`README.md`**에 "주간 브리핑" 절을 추가한다. 반드시 포함할 것:
   - `주간 브리핑.bat` 더블클릭이 기본 사용법
   - **미국 금요일 장은 한국 토요일 새벽에 닫히므로 토요일 아침 이후에 켤 것**
   - **허용 IP 재등록 절차 3줄** — 403이 뜨면 무엇을 어디에 넣는지. 몇 달에 한 번 겪을 일이라 그때는 잊고 있다
   - **`state/account/` 백업 안내** — 수익률 이력의 유일한 원천이고 이 PC에만 있다. 저장소에 커밋하지는 말 것

- [x] **Step 6: 커밋**

```powershell
git add src/tradingbot/briefing_service.py src/tradingbot/cli.py tests/test_briefing_service.py ".env.template" README.md "주간 브리핑.bat"
git commit -m "BRIEF: Wire the weekly briefing to one double-click"
```

---

## 완료 기준 (스펙 §9)

- [ ] `주간 브리핑.bat` 더블클릭 → 계좌를 읽고 브리핑을 렌더해 텔레그램으로 보낸다
- [ ] 폰에서 그 메시지를 받고, **PC를 끈 뒤에도** 읽을 수 있다
- [x] 시점을 달리해 두 번 실행하면 스냅샷 2개가 쌓이고, 두 번째 브리핑이 **구간 일수와 함께** 수익률(또는 "측정 불가" 사유)을 보여준다
- [x] USD 종목 보유 시 주가 몫과 환율 몫이 분리되어 표시된다
- [x] 금칙어 테스트가 존재하고 통과한다
- [ ] 403(허용 IP 불일치)과 401(토큰 무효)이 서로 다른 메시지로 보고되고, 403에는 현재 공인 IP가 함께 나온다
- [x] 텔레그램 전송이 실패해도 브리핑 전문이 콘솔에 남는다
- [ ] **토스 앱 화면의 수량·평단·평가금액과 브리핑의 숫자가 일치한다** (Task 6 Step 4)
- [x] 기존 `pytest`가 전부 통과한다 — 백테스트·모의투자 회귀 없음
- [x] **주문 관련 코드가 M1 diff에 없다**

## 알려진 한계 (범위 밖)

- **매매 제안이 없다.** 요구사항 4는 M3이고, 그 전에 weekly 기준 `research evaluate` 통과가 선행 조건이다 (검토 문서 §4.4)
- **뉴스가 없다.** 요구사항 2는 M2
- **자동 실행이 아니다.** PC를 켜고 더블클릭해야 한다. 요구사항 5는 "PC를 끈 뒤에도 폰에서 읽는다"로 좁혀졌다 (스펙 §2)
- **종목별 누적 수익률은 통화 기준이다.** 매수 시점 환율이 응답에 없으면 원화 환산 누적은 낼 수 없다. 원화 매입금액 필드가 있으면 후속 과제로 추가한다 (Task 6 Step 1에서 확인)
- **입출금과 직접 매매를 구분하지 못한다** (입출금 API가 없을 경우). 둘 다 "측정 불가"로 처리하고 되묻는다
- **입출금 발생 시점을 구간 시작으로 가정한다.** 스냅샷이 양 끝에 하나씩뿐이라 그 이상 알 수 없다
- `report/report.py`의 백테스트 리포트 워딩은 손대지 않는다. 사전이 생겼으니 나중에 같은 곳을 보게 만들 수 있다

---

## 실행 기록 (2026-08-18, 브랜치 `feat/weekly-briefing-m1`)

**Task 1~5, 7 완료. Task 6만 남았고, 그것이 위 완료 기준 4개를 막고 있다.**
테스트 829개 통과 (착수 시점 740개 + 신규 89개), 회귀 없음. `broker/`는 열지 않았다.

| Task | 커밋 |
|---|---|
| 1 용어 사전 | `BRIEF(part): Put the reader-facing wording in one enforceable place` |
| 2 계좌 포트 | `BRIEF(part): Read an account without implementing an order path` |
| 3 구간 수익률 | `BRIEF(part): Measure returns between snapshots, or say why not` |
| 4 렌더러 | `BRIEF(part): Render the account in words a non-expert can read` |
| 5 텔레그램 | `BRIEF(part): Deliver the briefing somewhere it outlives the session` |
| 7 배선·문서 | `BRIEF: Wire the weekly briefing to one double-click` |

### 계획과 다르게 한 세 가지

**1. 환율 분해는 곱셈 분해다. "주가 몫 + 환율 몫 = 전체"라고 쓰지 않는다.**
Task 3 Step 4의 노트가 두 가지를 동시에 요구했는데 그 둘은 양립하지 않는다 —
테스트는 `price_part == 0.10`을 `abs=1e-9`로 요구하고(곱셈 분해만 만족),
같은 노트는 "두 몫의 합이 전체와 0.1%p 이내"를 요구한다. 해당 케이스의 교차항이
0.37%p라 덧셈으로는 절대 들어오지 않는다. 테스트를 계약으로 삼아
`price = (1+total)/(1+fx) − 1`을 쓰고, 브리핑은 두 몫을 나란히 적은 뒤
"두 몫은 곱해져 전체가 되므로, 그냥 더한 값과는 조금 다릅니다"라고 밝힌다.
덧셈으로 맞췄다면 앱 화면과 0.4%p 어긋난 숫자를 내보내게 된다.

**2. 가격 캐시 갱신을 계좌 읽기 *뒤*로 옮겼다** (계획 Task 7 Step 3의 1번 ↔ 2번).
갱신할 종목 목록의 유일한 진실은 계좌 응답이다. 먼저 갱신하면 직전 스냅샷의
종목 목록을 쓰게 되고, 지난 실행 이후 새로 산 종목은 첫 브리핑에서 가격 기록이
아예 없다. 계획이 지키라고 한 순서(직전 스냅샷 로드 → 저장)는 그대로다.

**3. `cash_weight`는 부호를 붙이지 않는다.**
계획의 `TERMS`는 `_pct`(항상 부호)를 썼는데, 렌더 결과를 눈으로 읽어보니
현금 25.5%가 "+25.5%"로 나와 수익처럼 읽혔다. 비중은 방향이 없으므로
`_pct_unsigned`를 따로 두고 테스트로 고정했다. 스펙 §4.4의 예시 문구
("현금 비중은 8%입니다")와도 이쪽이 맞다.

### Task 6을 위해 비워둔 자리

`briefing_service.build_account_reader()`가 그 이음새다. 지금은
`tradingbot.account.toss` import에 실패하면 `MissingCredentialsError`로
**무엇을 해야 하는지 적어서** 던진다. CLI는 그것을 잡아 안내를 출력하고 1로 끝난다.
`account/toss.py`에 `build_reader() -> AccountReader`를 만들면 그대로 연결된다.

이 세션에서도 `developers.tossinvest.com` 응답 스펙을 확인할 수 없었다. 필드명을
기억으로 쓰면 통과하는 테스트가 틀린 매핑을 보증하므로, 어댑터에 손대지 않았다.

**2026-08-19 추가:** 공식 기계판독 스펙(`openapi.json`, v1.2.14)을 확보해서 이 미지 구간이
해소되었다. 설계는 `docs/superpowers/plans/2026-08-19-toss-account-reader-design.md`에
있고, 위 「확인 결과 기록」도 그 스펙으로 채웠다. 구현은 그 설계서 §10의 순서를 따른다.
이음새 시그니처는 `build_account_reader(state_root)`로 바뀐다(설계서 §3.13) — 토큰 캐시가
스냅샷과 같은 state 루트 아래 있어야 하고 `--config`를 따라야 하기 때문이다.

### 검증 방법 (네트워크·계좌 없이)

`run_briefing`은 reader/notifier/cache를 전부 주입받으므로 `tests/test_briefing_service.py`가
스냅샷 저장·구간 비교·전송 실패 경로를 모두 덮는다. CLI 경로(서브파서 → 핸들러 →
종료코드)와 `주간 브리핑.bat`(cmd가 CP949로 파싱하는지, `errorlevel` 분기, `pause`)은
스텁 리더로 실행해 확인했다. `.bat`은 기존 `트레이딩봇 실행.bat`과 동일하게
저장된다 (디스크 CRLF + CP949, blob은 LF — `core.autocrlf=true`).
