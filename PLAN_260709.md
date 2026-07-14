# 주식 트레이딩 봇 v1 구현 계획 (국내 + 미국, 백테스트 → 모의 → 실전)

## Context

사용자는 `e:\trading-bot`(현재 빈 디렉터리)에 주식 트레이딩 봇을 만들고자 함. 확정된 요구사항:

- **대상 시장**: 국내(KOSPI/KOSDAQ) + 미국 주식 둘 다
- **계좌/API 아직 없음** → 무료 공개 데이터로 백테스트·로컬 모의투자가 지금 바로 동작해야 하고, 증권사(한국투자증권 KIS Developers) 연동은 나중에 끼울 수 있게 Broker 추상화
- **단계**: 백테스트 → 모의투자 → 실전 순서
- **전략**: 검증된 고전 전략 2~3개를 플러그인 형태로 내장 (변동성 돌파, 이동평균 교차, RSI 평균회귀)
- 환경: Windows 11, Python 3.13 설치됨

**핵심 설계 결정 — 자체 이벤트 기반 엔진**: backtesting.py/vectorbt는 백테스트 전용이라 모의/실전 모드에서 전략을 다시 짜야 하고, backtrader는 사실상 유지보수 중단 + KIS 연동 없음. 일봉 기반 이벤트 루프(~400줄)를 직접 만들면 **동일한 Strategy 코드가 백테스트·모의·실전에서 그대로 실행**되고, KISBroker는 세 번째 Broker 구현체로 슬롯인됨.

## 스택

| 항목 | 선택 |
|---|---|
| Python | 3.13 (설치본), `uv` + `pyproject.toml` (+ `uv.lock`) |
| 국내 일봉 | FinanceDataReader (수정주가 OHLCV + KRX 종목 리스팅) |
| 미국 일봉 | yfinance (`auto_adjust=True`) |
| 모의투자 시세 | yfinance (KR은 `005930.KS/.KQ` 매핑, ~20분 지연 — 모의 모드 한계로 문서화) |
| 캐시 | Parquet (pyarrow), 종목당 1파일, 증분 업데이트 |
| 설정 | TOML (stdlib `tomllib`) / CLI는 stdlib `argparse` |
| 리포트 | matplotlib PNG(base64) 내장 단일 HTML + trades.csv |
| 테스트 | pytest (네트워크 없이 fixture로만) |

런타임 의존성: `pandas`, `numpy`, `pyarrow`, `matplotlib`, `finance-datareader`, `yfinance`. 개발: `pytest`.

## 디렉터리 구조

```
e:\trading-bot\
├─ pyproject.toml / README.md(한국어) / .gitignore
├─ config\default.toml            # 수수료, 리스크, 전략 파라미터, 유니버스
├─ data\cache\{KR,US}\*.parquet   # gitignore
├─ state\                         # PaperBroker JSON 상태, gitignore
├─ reports\                       # 백테스트 결과물, gitignore
├─ src\tradingbot\
│  ├─ __main__.py, cli.py         # python -m tradingbot {data,backtest,paper,strategies}
│  ├─ config.py, models.py        # Bar/Order/Fill/Position + enum
│  ├─ data\{sources,cache,feed}.py
│  ├─ broker\{base,fees,backtest,paper,kis}.py
│  ├─ portfolio.py, risk.py
│  ├─ engine\{engine,clock}.py
│  ├─ strategies\{base,registry,vol_breakout,ma_cross,rsi_reversion}.py
│  ├─ report\{metrics,report}.py
│  └─ utils\log.py
└─ tests\ (+ tests\data\ fixture parquet)
```

## 핵심 인터페이스

**이벤트 모델** — DataFeed가 하루를 3가지 이벤트로 방출, 엔진 루프 하나가 백테스트/모의 공용:
- `SessionOpen(dt, opens)` → 대기 MARKET 주문 시가 체결, `strategy.on_open()`
- `PriceTick(dt, prices)` → 모의 모드 전용 5분 폴링, STOP/LIMIT 트리거
- `SessionClose(dt, bars)` → 백테스트에서 STOP/LIMIT을 고가/저가로 판정, MOC 체결, `strategy.on_bar()`, 리스크 점검, 평가손익 갱신

```python
class Strategy(ABC):
    name: ClassVar[str]; default_params: ClassVar[dict] = {}
    def init(self, ctx): ...                # 선택
    def on_open(self, ctx, dt, opens): ...  # 선택
    @abstractmethod
    def on_bar(self, ctx, bar): ...         # 종가 후, 종목별
    def on_fill(self, ctx, fill): ...       # 선택

class Context:  # 모든 모드에서 동일
    def history(self, symbol, n) -> pd.DataFrame: ...
    def position(self, symbol) / cash() / equity(): ...
    def buy/sell(symbol, qty|weight, order_type, limit_price, stop_price, tif): ...
    # 모든 주문은 RiskManager.validate() 통과 후 Broker.submit()

class Broker(ABC):  # submit / cancel / open_orders / positions / cash
    ...
# SimulatedExecutionMixin: BacktestBroker·PaperBroker 공용 체결 로직
# PaperBroker: state\<name>.json 저장/복원 (재시작 안전)
# KISBroker: v1은 스켈레톤만 (OAuth·주문·잔고 엔드포인트 TODO, 모의투자 도메인 명시)
```

**체결 규칙 (시뮬레이션, D일)**: MARKET→D시가+슬리피지 / BUY STOP→`high(D)>=stop`이면 `max(stop, open(D))`에 체결(갭상승 시 시가 체결 — 룩어헤드 방지) / BUY LIMIT→`low(D)<=limit`이면 `min(limit, open(D))` / MOC→종가. 체결가는 KRX 호가단위 표(2023 개편)/`$0.01`로 라운딩(매수 올림, 매도 내림), 정수 주식만, 예상 수수료 포함 현금 부족 시 거부.

주문 유형은 `MARKET / LIMIT / STOP / MOC`, TIF는 `DAY / GTC` — 내장 전략 3개에 필요한 전부이며 일봉으로 시뮬레이션 가능한 것만.

## 내장 전략 (전부 롱 온리, 종목별 독립 실행)

1. **`vol_breakout` 변동성 돌파** (k=0.5): 장 시작 시 `target = open + k*(전일 고가-저가)`에 BUY STOP(DAY) 제출, 체결되면 당일 MOC 청산 (`exit="next_open"` 옵션).
2. **`ma_cross` 이동평균 교차** (fast=20, slow=60): 골든크로스→MARKET 매수(익일 시가 체결), 데드크로스→전량 매도.
3. **`rsi_reversion` RSI 평균회귀** (period=14 Wilder, buy<30, exit>55, 최대 보유 10일).

새 전략 = `Strategy` 상속 파일 1개 + registry 등록.

## 수수료 / 슬리피지 (config/default.toml, 하드코딩 금지)

- **KR**: 수수료 0.015% 양방향 + 매도 시 거래세 0.15% (2025년 기준)
- **US**: 수수료 0 + 매도 시 SEC fee ($27.80/1M) + FINRA TAF ($0.000166/주, 주문당 $8.30 상한)
- 슬리피지: 모든 체결에 5bps 불리하게 적용

## 리스크 관리 기본값

`max_position_pct=20%`, `max_positions=5`, `max_daily_loss_pct=3%`(도달 시 신규 진입 차단), `stop_loss_pct=5%`(평단 대비, 자동 청산 주문), `min_cash_buffer_pct=2%`. **1 프로세스 = 1 시장 = 1 통화**(KRW 또는 USD) — v1은 환율 변환 없음.

## 마일스톤 (각각 독립 검증 가능)

- **M1 — 워킹 스켈레톤**: uv 셋업(+`git init`), models, 데이터 수집·parquet 캐시, HistoricalDataFeed, BacktestBroker(MARKET만, 수수료 0.015%), Portfolio, ma_cross, 엔진 루프, CLI `data update`/`backtest`.
  ✔ `python -m tradingbot backtest --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01` 이 오프라인으로 돌고 최종 자산·수익률·거래수 출력
- **M2 — 체결 완성 + 수수료 + 리스크 + 나머지 전략**: STOP/LIMIT/MOC/DAY 만료, fees.py(호가단위 포함), 슬리피지, RiskManager, vol_breakout·rsi_reversion, 단위 테스트.
  ✔ `pytest` 그린; 005930 vol_breakout 거래내역에 거래세 정확히 반영
- **M3 — 리포트**: CAGR/MDD/Sharpe(일간, √252)/승률/profit factor, 자산곡선+드로다운 차트, 단일 HTML + trades.csv → `reports\<timestamp>_<strategy>_<market>\`.
  ✔ 백테스트가 브라우저에서 열리는 HTML 리포트 생성
- **M4 — 모의투자**: clock.py 세션(KR 09:00–15:30 Asia/Seoul, US 09:30–16:00 America/New_York — DST는 zoneinfo가 처리), PollingDataFeed(5분 폴링), PaperBroker JSON 영속화·재시작 복원, CLI `paper --name <run>`, Windows 작업 스케줄러 등록법 문서화.
  ✔ 장중 실행 → 강제 종료 → 재시작 시 포지션/현금 복원 확인
- **M5 — KIS 스켈레톤 + 스모크 테스트 + 문서**: kis.py 스텁, tests\data fixture, 최종 자산 원/센트 단위 고정값 회귀 테스트, 한국어 README, .gitignore.
  ✔ 클린 클론 → `uv sync` → `pytest` 전부 그린 (네트워크 0)

## 검증 계획

- 단위: `test_fees.py`(매도세는 매도만, 라운딩 방향, TAF 상한), `test_backtest_broker.py`(갭상승 STOP은 시가 체결, DAY 만료, LIMIT 경계, 현금 부족 거부), `test_portfolio.py`(물타기 평단, 부분 청산 실현손익), `test_metrics.py`(수계산 대조), `test_strategies.py`(합성 봉으로 정확히 1회 신호 유도 → 주문 검증)
- E2E: `test_smoke_backtest.py` — 체크인된 fixture(2종목 2년)로 3전략 실행, **최종 자산 고정값 일치** 회귀 (슬리피지 결정론적이라 가능)
- 수동: 005930+AAPL `data update` → 전략×시장별 백테스트 → HTML 확인 → 모의 세션 시작/강제종료/재개

## v1 비목표 (명시적 제외)

분봉/웹소켓 실시간(PriceTick 이벤트로 문은 열어둠), KIS 실주문(스켈레톤만), 알림(텔레그램 등), 공매도/신용/파생/소수점 주식, 시장 통합 포트폴리오·환율, 배당·기업행위(수정주가로 갈음), 부분 체결/호가 심도, 파라미터 최적화/ML, GUI/대시보드.
