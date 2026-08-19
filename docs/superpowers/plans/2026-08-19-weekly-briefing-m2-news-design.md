# 설계서: 주간 브리핑 M2 — 뉴스 섹션 (요구사항 2)

- 작성: 2026-08-19, 설계자
- 상위 스펙: `docs/superpowers/specs/2026-08-17-weekly-briefing-m1-design.md` §2, §8
- 근거 검토: `docs/weekly_briefing_design_review.md` §4.2
- 선행: M1 구현 완료 (`3edd8e2` + `a3258c5`, 테스트 878개 통과)
- **역할 분담: 이 문서는 설계다. 구현·테스트 작성은 codex가 한다.**

---

## 0. 착수 시점에 관한 판단

M1은 **구현이 끝났지만 실계좌로 확인되지 않았다.** 남은 두 가지는 사용자만 할 수 있다
(앱 화면 숫자 대조, 폰 수신). 그래서 이 설계의 태스크를 셋으로 나눠 착수 조건을 다르게 둔다.

| 태스크 | 착수 조건 |
|---|---|
| 1 (모델·DART 매퍼), 4 (섹션·워딩 테스트) | **지금 가능.** M1 결과에 의존하지 않는다 |
| 2 (Yahoo 매퍼) | **실호출 1회 필요** — 사용자가 아니어도 된다, 네트워크만 있으면 된다 |
| 3, 5 (수집기·배선) | **M1 실계좌 대조 이후 권장.** 확인되지 않은 층 위에 층을 얹지 않는다 |

3·5를 미루는 이유는 뉴스 섹션이 `curr.holdings`의 심볼을 그대로 열거해 소식을 찾기
때문이다. 그 심볼 목록이 앱 화면과 일치하는지가 아직 확인되지 않았다.

---

## 1. 범위

**만드는 것:** 보유 종목에 대해 지난 실행 이후 올라온 **공시·뉴스 제목을 그대로**
보여주는 브리핑 섹션 하나.

**만들지 않는 것:**

- **요약·해석.** 기사 본문을 요약하지 않는다. 근거는 §3.1.
- **인과 설명.** "왜 올랐는지"를 말하지 않는다. 근거는 §3.1.
- **감성 점수·뉴스 팩터.** 백테스트에 새는 통로를 만들지 않는다 (§3.5).
- **지수 대비 배수 괴리 계산.** 근거는 §3.4.
- **매매 제안.** M3.
- 백테스트·모의투자·`broker/` 경로 변경.

### 먼저 못 박아 둘 것 — "뉴스는 불가" 판정은 여기 적용되지 않는다

`docs/superpowers/specs/2026-08-12-event-alpha-design.md`는 "Narrative / News /
Novelty — **불가**: 타임스탬프 있는 기사 아카이브 없음"이라고 판정했다.
**그 판정은 백테스트용 Point-in-Time 아카이브에 대한 것이다.** M2는 백테스트가 아니라
지난 한 주를 읽는 용도이므로 과거 아카이브가 필요 없고, 지금 당장 가능하다.

검토 문서 §4.2가 이 구분을 문서에 못 박으라고 한 이유가 이것이다. 여기 다시 적는다:
**이 판정을 근거로 M2를 차단하지 말 것.**

---

## 2. 소스 결정

| 시장 | 소스 | 자격증명 | 응답 스펙 |
|---|---|---|---|
| KR | DART 공시 목록 — `DartClient.disclosure_list` | `DART_API_KEY` (**이미 쓰는 키**) | 문서화됨, 이미 배선됨 |
| US | Yahoo — `yfinance.Ticker.get_news()` | 없음 (**새 키 0개**) | **문서 없음 → fixture 먼저** |
| KR 뉴스 (네이버) | 검색 API | 새 키 2개 | **M2 범위 밖** (§2.3) |

### 2.1 국내는 뉴스가 아니라 공시로 시작한다

검토 문서는 국내 소스로 "네이버 검색 API + DART 공시"를 제시했다. M2는 **공시만** 쓴다.

- 공시는 **사실**이다. 발행 주체가 회사이고, 접수 시각이 있고, 원문 URL이 결정적이다.
- **이미 배선돼 있다.** `DartClient.disclosure_list(corp_code, start, end) -> list[Disclosure]`가
  페이지네이션까지 처리하고, `data/corp_codes.py`의 `CorpCodeStore`가 종목코드→corp_code를
  갖고 있고, `DART_API_KEY`는 사용자가 이미 등록한 키다. **새로 발급받을 것이 없다.**
- 뉴스는 해석이 섞이고, 키가 2개 더 필요하고, 분량이 폭발한다. 브리핑이 "이런 일이
  있었습니다"라고 말할 때 **검증 가능한** 국내 소스는 공시다.

`Disclosure`는 `rcept_no`, `report_name`, `rcept_dt` 세 필드만 갖는다. URL 필드는 없지만
DART 뷰어 주소는 접수번호로 결정된다 — `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`.
이 형식을 상수로 두고, **필드를 지어내지 않는다.**

`data/events.py`의 `disclosures_to_events`는 **쓰지 않는다.** 그것은 실적 공시만
남기도록 좁히는 함수다. 뉴스 섹션은 그 주에 올라온 공시 전부를 대상으로 한다.

### 2.2 미국은 Task 6과 같은 상황이다 — 응답을 먼저 기록한다

설치된 `yfinance 0.2.66`의 `get_news()`를 직접 읽어 확인한 것:

```python
url = f"{_ROOT_URL_}/xhr/ncp?queryRef={query_ref}&serviceKey=ncp_fin"
...
news = data.get("data", {}).get("tickerStream", {}).get("stream", [])
self._news = [article for article in news if not article.get('ad', [])]
```

**항목 하나의 내부 필드명은 문서가 없다.** Yahoo의 비공개 `xhr/ncp` 응답을 그대로
돌려주며, 이 엔드포인트는 과거에 여러 번 바뀌었다. 제목·시각·URL 키 이름을 기억으로
쓰면 **통과하는 테스트가 틀린 매핑을 보증한다** — M1 Task 6에서 이미 겪은 함정이다.

그래서 Task 2는 Task 6과 똑같이 진행한다: **실호출 1회로 응답 원문을 기록하고,
그 fixture에 맞춰 매퍼를 쓴다** (§7).

Yahoo가 불안정하다고 판명되면 대안은 Finnhub 무료 티어다 — 문서화된 REST 스펙이 있지만
새 키가 필요하다. **지금 선택하지 않는다.** 실제로 깨지는 것을 보기 전에 사용자에게
키를 하나 더 발급받게 할 이유가 없다.

### 2.3 네이버 뉴스는 M2에 넣지 않는다

키 2개(`NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`)가 더 필요하고, 종목명 검색은 동명이의와
광고성 기사를 대량으로 물고 온다(검토 문서 §4.2의 "빈 결과나 쓰레기"). 공시만으로 국내
섹션이 성립하는지를 먼저 보고, 부족하면 그때 붙인다. **부족하다는 증거가 나오기 전에
키를 늘리지 않는다.**

---

## 3. 설계 결정과 근거

### 3.1 제목만 보여주고, 해석하지 않는다

뉴스 항목은 **제목 · 날짜 · 출처 · 링크**만 렌더한다. 요약문을 만들지 않고, 주가
움직임과 연결하지 않는다.

근거: 브리핑의 다른 모든 숫자는 **측정된 것**이다. 수익률은 스냅샷 두 개에서 계산했고,
측정할 수 없으면 "측정 불가"라고 적는다. 그 옆에 **측정하지 않은 인과**를 같은 목소리로
놓으면, 읽는 사람은 둘을 구별할 방법이 없고 브리핑 전체의 신뢰가 같이 깎인다.
요약을 만들면 그 요약이 틀렸을 때 우리가 책임질 근거가 없다.

이것은 기능 축소가 아니라 요구사항 3(비전문가 워딩)의 직접적 결과다. 비전문가에게
가장 위험한 문장은 어려운 문장이 아니라 **근거 없이 확신하는 문장**이다.

### 3.2 인과 연결어를 금칙어로 고정한다

`report/glossary.py`의 금칙어 장치와 같은 방식으로 **인과 표현을 테스트로 막는다.**

```python
CAUSAL = ("때문에", "덕분에", "영향으로", "로 인해", "여파로", "탓에", "때문인지")
```

렌더된 브리핑 전문에 이 표현이 있으면 테스트가 실패한다. §3.1은 규칙이고, 이것이
그 규칙의 집행 지점이다. 규칙만 적어두면 섹션이 하나 늘 때마다 무너진다 — M1에서
용어 사전을 테스트로 강제한 것과 같은 이유다.

`glossary.BANNED`에 넣지 않고 별도 상수로 둔다. 금칙어는 "전문용어를 쓰지 마라"이고
이건 "측정하지 않은 것을 주장하지 마라"로, 규칙이 다르다. 한 자루에 담으면 왜 막는지가
흐려진다.

### 3.3 레버리지 ETF는 매핑하고, 매핑했다고 밝힌다

`report/briefing.py`의 `_LEVERAGED` 9종목(`SOXL SOXS TECL TECS TQQQ SQQQ FNGU LABU SPXL`)에는
**회사 공시도 회사 뉴스도 없다.** 종목명으로 검색하는 단순 구현은 빈 결과나 쓰레기를 낸다
(검토 문서 §4.2 함정 1).

대표 구성 종목으로 매핑한다. 표는 **작게** 유지한다 — 9종목, 종목당 2~3개.

```python
# 이 ETF 자체의 소식은 존재하지 않는다. 지수를 구성하는 대표 종목의 소식을
# 대신 보여주되, 렌더에서 반드시 "이 종목 자체 소식이 아니다"라고 밝힌다.
UNDERLYING: dict[str, tuple[str, tuple[str, ...]]] = {
    "SOXL": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
    "SOXS": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
    "TQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
    "SQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
    ...
}
```

`NewsItem.via`에 어디서 가져왔는지 남기고, 렌더가 그것을 문장으로 만든다.

> SOXL 자체 소식은 없습니다. 아래는 SOXL이 따라가는 반도체 지수의 주요 종목 소식이며,
> SOXL의 움직임을 설명하는 것은 아닙니다.

마지막 절이 §3.1의 규칙을 이 자리에서 다시 지킨다. 구성 종목 소식은 **관련이 있을 수
있는 것**이고, 그 이상을 주장하지 않는다.

**매핑에 없는 ETF는 매핑하지 않는다.** 추측으로 구성 종목을 채우지 않고, "이 종목은
개별 회사가 아니라 여러 종목을 묶은 상품이어서 회사 소식이 없습니다"로 끝낸다.

### 3.4 지수 대비 배수 괴리는 M2에서 계산하지 않는다

검토 문서 §4.2 함정 2는 "지수가 1% 올랐을 때 3배 ETF는 3%가 아니라 2.1% 올랐다"를
설명해야 한다고 했다. **M2에서는 하지 않는다.**

근거: 그 문장을 쓰려면 기초지수 시계열이 필요한데 없다.
`data/macro.py`의 `MACRO_SERIES`에 있는 것은 `sp500`(US500), `nasdaq`(IXIC),
`vix`, `kospi`, `kosdaq`뿐이다. SOX(반도체)도 NDX(나스닥 100)도 없고,
**`nasdaq`은 IXIC 종합지수로 TQQQ의 기초지수인 NDX가 아니다.**

틀린 지수로 비교한 숫자는 오해를 고치려다 새 오해를 만든다. M1이 이미 넣은 문구
("3배 ETF는 하루 단위로 3배라서, 여러 날을 합치면 기초지수의 정확히 3배가 아닙니다")를
그대로 두고, 숫자 비교는 **기초지수 시계열을 정식으로 추가하는 별도 작업**으로 남긴다.
그 작업의 조건은 §9에 적었다.

### 3.5 뉴스는 패널에 넣지 않는다 — 테스트로 격리한다

저장 위치는 `state/news/`다. `data/panel.py`의 `PanelStore`를 **쓰지 않는다.**

근거(검토 문서 §4.2): 패널에 들어가는 순간 팩터 신호로 오해되어 백테스트에 샐 수 있다.
`state/`는 `.gitignore` 대상이라 커밋되지도 않는다.

말로만 두지 않고 두 개를 테스트로 고정한다.

- `data/news.py`가 `panel`·`fundamentals_panel`·`features`·`universe` 모듈을 import하지 않는다
- `save_news`가 만든 경로가 패널 루트 아래에 없다

### 3.6 뉴스 실패는 브리핑을 죽이지 않는다

`briefing_service._refresh_prices`와 같은 처리다. 실패는 `messages`에 기록하고 넘어간다.

근거: 뉴스는 브리핑에서 가장 덜 중요한 부분이다. 계좌 숫자와 수익률은 측정된 것이고
뉴스는 참고 자료다. 참고 자료 때문에 측정된 것을 못 보게 만드는 것은 잘못된 교환이다.

**단, 조용히 비우지 않는다.** §3.7이 그 지점이다.

### 3.7 "없음"과 "못 가져옴"은 다른 문장이다

| 상황 | 문장 |
|---|---|
| 소스가 정상이고 항목이 0건 | "이 기간에 새로 올라온 소식이 없습니다." |
| 소스 호출이 실패 | "소식을 가져오지 못했습니다 (사유). 계좌 숫자는 영향받지 않습니다." |
| 키가 없어 소스를 건너뜀 | "국내 공시는 DART_API_KEY가 없어 확인하지 못했습니다." |

빈 섹션을 "새 소식 없음"으로 렌더하면 **사용자는 조용한 실패를 조용한 한 주로 읽는다.**
M1이 수익률에서 "측정 불가"를 0%와 구분한 것과 같은 규칙이다.

### 3.8 자른 것은 말한다

한 주에 10종목이면 공시만으로도 수십 건이 나오고, 텔레그램 상한은 4096자다.
종목당 `PER_SYMBOL = 3`, 전체 `TOTAL_CAP = 12`로 자르고 **자른 사실을 적는다.**

> 이 밖에 삼성전자 4건, AAPL 2건이 더 있습니다.

정렬은 최신순. 조용한 절단은 "전부 다 보여줬다"로 읽히므로, 설계서에 상한을 두는
모든 곳에서 이 규칙을 지킨다.

---

## 4. 인터페이스

### `src/tradingbot/data/news.py` (신설)

```python
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
PER_SYMBOL = 3
TOTAL_CAP = 12
CAUSAL = ("때문에", "덕분에", "영향으로", "로 인해", "여파로", "탓에", "때문인지")
UNDERLYING: dict[str, tuple[str, tuple[str, ...]]]      # §3.3


@dataclass(frozen=True)
class NewsItem:
    symbol: str          # 이 소식을 찾게 만든 보유 종목
    source: str          # "dart" | "yahoo"
    published_at: date
    title: str           # 원문 제목 그대로. 다듬지 않는다
    url: str
    via: str = ""        # 매핑해 온 지수/종목 이름. 직접 소식이면 ""


@dataclass(frozen=True)
class NewsResult:
    items: tuple[NewsItem, ...]
    failures: dict[str, str]      # source -> 실패 사유 (조용히 비우지 않는다)
    dropped: dict[str, int]       # symbol -> 잘라낸 건수
    skipped: dict[str, str]       # source -> 건너뛴 이유 (키 없음 등)

    def for_symbol(self, symbol: str) -> tuple[NewsItem, ...]: ...


# 순수 함수 — 네트워크 없이 fixture 로 검증된다
def dart_items(disclosures: Sequence[Disclosure], symbol: str) -> tuple[NewsItem, ...]
def yahoo_items(payload: dict, symbol: str, *, via: str = "") -> tuple[NewsItem, ...]
def within(items, *, since: date, until: date) -> tuple[NewsItem, ...]
def cap(items, *, per_symbol=PER_SYMBOL, total=TOTAL_CAP) -> tuple[tuple[NewsItem, ...], dict[str, int]]
def find_causal_terms(text: str) -> list[str]        # §3.2 집행용

# 수집 — 소스를 주입받는다
def fetch_news(
    holdings: Sequence[Holding],
    *,
    since: date,
    until: date,
    dart: DartFetcher | None = None,      # (corp_code, start, end) -> list[Disclosure]
    yahoo: YahooFetcher | None = None,    # (ticker) -> dict
    corp_codes: Mapping[str, str] | None = None,
) -> NewsResult

def save_news(result: NewsResult, root: str | Path) -> Path
def load_news(root: str | Path) -> NewsResult | None
def build_fetchers() -> tuple[DartFetcher | None, YahooFetcher | None, dict[str, str]]
```

`build_fetchers`가 자격증명과 실제 클라이언트를 조립한다. `DART_API_KEY`가 없으면 DART
쪽은 `None`이고 그 사실이 `NewsResult.skipped`로 흘러간다 — 예외를 던지지 않는다.
뉴스는 없어도 브리핑이 성립하기 때문이다(§3.6).

### `src/tradingbot/report/briefing.py` (수정)

```python
SECTIONS = ("summary", "totals", "holdings", "trend", "news", "notes")

@dataclass(frozen=True)
class _Context:
    ...                       # 기존 필드 그대로
    news: NewsResult | None   # 추가

def render_briefing(
    curr, prev=None, *,
    price_history=None,
    news: NewsResult | None = None,      # 추가 (키워드 전용, 기본값 None)
    now=None, long_gap_days=14,
) -> str
```

`news`는 키워드 전용 + 기본값이므로 기존 호출부와 20개 테스트는 그대로 통과한다.
`news=None`이면 `_render_news`는 빈 리스트를 돌려주고 섹션이 사라진다 — M1 동작 유지.

`news`를 "notes" **앞**에 둔다. "알아둘 점"은 브리핑의 꼬리 주석이므로 마지막이어야 한다.

### `src/tradingbot/briefing_service.py` (수정)

```python
def run_briefing(config, *, reader, notifier, cache, state_root,
                 skip_update=False, notify=True, news=True) -> BriefingResult
```

순서: 계좌 읽기 → 직전 스냅샷 → 저장 → 가격 갱신 → **뉴스 수집** → 렌더 → 전송 → 로그.

뉴스를 가격 갱신 뒤에 두는 이유는 가격 갱신과 같다 — 어떤 종목의 소식을 찾을지는
계좌 응답만이 안다. `since`는 `prev.as_of.date()`(없으면 `until - 7일`), `until`은
`curr.as_of.date()`. 첫 실행에 7일을 쓰는 이유는 그 이상은 "지난 실행 이후"가 아니기
때문이다.

`NewsResult`는 `state/news/`에도 저장한다. 실행 로그가 "그때 무엇을 보여줬는지"를
재구성할 수 있어야 하고, 재실행이 API를 다시 때리지 않아야 한다.

### `src/tradingbot/cli.py` (수정)

`--no-news` 플래그 하나. `--skip-update`와 같은 모양.

---

## 5. 파일 목록

| 구분 | 경로 |
|---|---|
| 생성 | `src/tradingbot/data/news.py` |
| 생성 | `tests/test_data_news.py` |
| 생성 | `tests/data/dart_disclosure_list_sample.json` (기존 DART fixture 재사용 가능하면 생략) |
| 생성 | `tests/data/yahoo_news_sample.json` — **Task 2에서 실호출로 기록** |
| 수정 | `src/tradingbot/report/briefing.py` — `news` 섹션 + `_Context` 필드 |
| 수정 | `tests/test_report_briefing.py` — 뉴스 섹션 + 인과 금칙어 테스트 |
| 수정 | `src/tradingbot/briefing_service.py` — 수집 단계 |
| 수정 | `tests/test_briefing_service.py` — 뉴스 실패가 실행을 죽이지 않음 |
| 수정 | `src/tradingbot/cli.py` — `--no-news` |
| 수정 | `README.md` — 뉴스 섹션이 무엇을 하지 않는지 |

`broker/`는 열지 않는다. `data/panel.py`·`data/features.py`·`research/`도 열지 않는다.

---

## 6. 테스트 명세

`tests/test_data_news.py` — **네트워크 금지.** fetcher를 전부 주입한다.

### TestDartItems (fixture 기반, 순수)
1. `test_every_disclosure_becomes_one_item`
2. `test_the_title_is_the_filing_name_unchanged`
3. `test_the_url_is_built_from_the_receipt_number`
4. `test_the_date_is_the_receipt_date`
5. `test_an_empty_filing_list_is_an_empty_result_not_a_failure`

### TestYahooItems (Task 2의 fixture 기반, 순수)
6. `test_every_article_in_the_sample_becomes_one_item`
7. `test_the_title_and_url_come_from_the_response`
8. `test_an_advertisement_entry_is_dropped`
9. `test_an_unexpected_shape_raises_instead_of_returning_nothing`
   — 조용한 빈 결과는 "새 소식 없음"으로 렌더된다. 그것이 이 테스트가 막는 것이다.

### TestWindowAndCap
10. `test_items_before_the_window_are_excluded`
11. `test_the_newest_items_survive_the_cap`
12. `test_what_the_cap_dropped_is_counted_per_symbol`
13. `test_nothing_is_dropped_silently` — `dropped` 합계 + 남은 건수 == 원래 건수

### TestUnderlyingMapping
14. `test_a_leveraged_etf_is_mapped_to_its_index_constituents`
15. `test_a_mapped_item_records_where_it_came_from` — `via` 가 비어 있지 않다
16. `test_an_unmapped_etf_is_not_guessed_at` — 매핑 없으면 항목 0건 + 사유

### TestFetchNews (주입된 fetcher)
17. `test_a_missing_dart_key_is_skipped_not_failed` — `skipped`에 기록, 예외 없음
18. `test_a_source_failure_is_recorded_with_its_reason` — `failures`에 사유
19. `test_one_failing_source_does_not_lose_the_other_source_items`
20. `test_a_symbol_with_no_corp_code_is_recorded_not_dropped_silently`

### TestIsolation (§3.5)
21. `test_news_does_not_import_the_panel_modules`
22. `test_saved_news_lives_under_the_state_root_not_the_panel_root`

### TestStore
23. `test_a_saved_result_round_trips`
24. `test_a_corrupt_file_is_reported_not_silently_empty`

`tests/test_report_briefing.py` 추가분:

25. `test_the_news_section_is_absent_when_no_news_is_given` — M1 동작 유지
26. `test_each_item_shows_its_date_title_and_source`
27. `test_a_mapped_item_says_it_is_not_the_etfs_own_news`
28. `test_no_news_and_a_failed_fetch_read_differently` (§3.7)
29. `test_what_the_cap_dropped_is_stated` (§3.8)
30. `test_the_briefing_never_claims_a_cause` — **`find_causal_terms(rendered) == []`**
31. `test_the_news_section_passes_the_jargon_check` — 기존 `find_banned_terms`도 통과
32. `test_a_long_news_list_still_splits_at_section_boundaries` — 4096자 분할

`tests/test_briefing_service.py` 추가분:

33. `test_a_news_failure_is_reported_and_the_briefing_still_renders` (§3.6)
34. `test_no_news_flag_skips_the_fetch_entirely`

합계 **34개**. 878 + 34 = **912개** 목표.

---

## 7. Yahoo 응답 기록 절차 (Task 2 선행)

Task 6과 같은 방식이다. `scratch/`(gitignore 대상)에 일회용 스크립트를 두고 1회 실행한다.

```python
"""Record one Yahoo news response. Run once, then delete. scratch/ is gitignored."""
import json, pathlib, yfinance

out = pathlib.Path("scratch/raw"); out.mkdir(parents=True, exist_ok=True)
for ticker in ("AAPL", "NVDA"):
    raw = yfinance.Ticker(ticker).get_news(count=10)
    (out / f"yahoo_news_{ticker}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(ticker, len(raw), "articles")
    if raw:
        print("  keys:", sorted(raw[0].keys()))
        print("  content keys:", sorted((raw[0].get("content") or {}).keys()))
```

기록한 뒤 `tests/data/yahoo_news_sample.json`으로 옮긴다. **가공하지 않는다** — 계좌
응답과 달리 공개 기사 목록이고 개인정보가 없다. 다만 파일 옆에 **채집 날짜와
yfinance 버전**을 적는다. Yahoo가 형태를 바꾸면 그 날짜가 유일한 단서다.

그리고 아래를 이 문서에 적어 넣는다.

```text
확인 결과 기록 — Task 2에서 채울 것
- 항목 배열 경로:        (data.tickerStream.stream 확인?)
- 항목당 제목 필드:
- 항목당 발행 시각 필드 / 형식:
- 항목당 URL 필드:
- 광고 항목 표시 방법:   (article["ad"] 확인?)
- 출처(발행 매체) 필드:  (있음/없음)
- yfinance 버전 / 채집일:
```

---

## 8. 하지 말 것

- **기사 본문을 요약하지 않는다.** 제목 원문만 쓴다 (§3.1).
- **주가 움직임과 뉴스를 한 문장에 넣지 않는다.** 금칙 연결어 테스트가 막는다 (§3.2).
- **구성 종목을 추측으로 채우지 않는다.** 매핑에 없으면 없다고 말한다 (§3.3).
- **기초지수 숫자를 만들지 않는다.** 없는 시계열을 비슷한 것으로 대체하지 않는다 (§3.4).
- **뉴스를 `PanelStore`에 넣지 않는다.** `state/news/`다 (§3.5).
- **빈 결과와 실패를 같은 문장으로 렌더하지 않는다** (§3.7).
- **조용히 자르지 않는다** (§3.8).
- **Yahoo 응답 필드명을 기억으로 쓰지 않는다** (§7).
- 테스트에서 네트워크를 쓰지 않는다. 기존 CLI·백테스트·모의투자 동작을 바꾸지 않는다.
- 파일 쓰기는 `encoding="utf-8"` 명시.

---

## 9. 실행 순서

- [ ] **Task 1: 모델 + DART 매퍼** (지금 착수 가능)
      실패 테스트(§6 1~5) → `NewsItem`/`NewsResult`/`dart_items`/`within`/`cap` 구현 → 통과
      커밋: `NEWS(part): Turn filings into items without interpreting them`
- [ ] **Task 2: Yahoo 응답 기록 + 매퍼** (네트워크 1회 필요)
      §7의 스크립트 실행 → fixture 저장 → 「확인 결과 기록」 채움 → 테스트(§6 6~9) → 구현
      커밋: `NEWS(part): Map the Yahoo news response from a recorded sample`
- [ ] **Task 3: 수집기 + 저장소 + 레버리지 매핑** (M1 실계좌 대조 이후 권장)
      테스트(§6 14~24) → `fetch_news`/`save_news`/`load_news`/`build_fetchers`/`UNDERLYING`
      커밋: `NEWS(part): Gather what was published, and say what was missed`
- [ ] **Task 4: 브리핑 섹션 + 인과 금칙어** (지금 착수 가능 — Task 1 이후)
      테스트(§6 25~32) → `_render_news` + `SECTIONS`/`_Context`/`render_briefing`
      커밋: `NEWS(part): Show the headlines without explaining the returns`
- [ ] **Task 5: 서비스·CLI 배선 + 문서** (M1 실계좌 대조 이후)
      테스트(§6 33~34) → `run_briefing`/`--no-news`/README
      커밋: `NEWS: Attach the news section to the weekly briefing`
- [ ] **전체 확인**
      ```powershell
      .\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"
      git diff --name-only origin/main...HEAD    # broker/ 와 data/panel.py 가 없어야 한다
      ```
      912개 통과, 회귀 0.

---

## 10. 이 설계가 남기는 알려진 한계

1. **국내는 공시만 본다.** 회사가 공시하지 않은 일(업계 뉴스, 정책, 경쟁사)은 안 보인다.
   네이버를 붙일지는 §2.3의 조건에 달려 있다.
2. **미국 소스가 문서화되지 않은 엔드포인트다.** Yahoo가 형태를 바꾸면 매핑이 깨진다.
   §6의 9번 테스트가 "조용히 빈 결과"가 아니라 예외로 드러나게 만드는 장치다.
3. **관련성을 판정하지 않는다.** 그 종목에 붙은 소식을 시간순으로 보여줄 뿐이고,
   중요도 순이 아니다. 상한(§3.8)에 걸려 잘리는 것이 정작 중요한 소식일 수 있다.
4. **레버리지 ETF 매핑은 손으로 쓴 9줄이다.** 구성 종목이 바뀌면 낡는다.
   자동 갱신하지 않는 이유는, 구성 종목 API를 새로 물리는 비용이 이 표를 손으로
   고치는 비용보다 크기 때문이다.
5. **배수 괴리를 숫자로 설명하지 못한다** (§3.4). 기초지수 시계열을 `MACRO_SERIES`에
   정식으로 추가하는 별도 작업의 조건: SOX·NDX의 FinanceDataReader 심볼을 실제로
   확인하고, 종합지수로 대체하지 않는 것.
