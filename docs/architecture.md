# 트레이딩 봇 전체 아키텍처

이 문서는 현재 구현을 기준으로 데이터 수집, 백테스트, 모의투자, 주문·체결,
상태 저장 흐름을 설명한다. 핵심 설계는 **동일한 전략 코드와 주문 인터페이스를
백테스트와 모의투자에서 함께 사용**하는 이벤트 기반 구조다.

## 1. 전체 시스템 구성

```mermaid
flowchart TB
    User["사용자 / 작업 스케줄러"]

    subgraph UI["진입 계층"]
        GUI["Tkinter GUI<br/>gui.py"]
        CLI["CLI<br/>cli.py / __main__.py"]
    end

    subgraph APP["애플리케이션 조립 계층"]
        Services["공용 서비스<br/>services.py"]
        Config["TOML 설정<br/>config/default.toml"]
        Symbols["종목 검색<br/>symbols.py"]
    end

    subgraph EXTERNAL["외부 데이터 / 향후 외부 주문"]
        FDR["FinanceDataReader<br/>KR/US 일봉·한국 주식/ETF 목록"]
        YF["yfinance<br/>KR/US 장중 폴링 가격"]
        Nasdaq["NASDAQ Trader<br/>미국 상장 주식·ETF 목록"]
        KISAPI["KIS Developers REST<br/>실전/모의 API"]
    end

    subgraph DATA["데이터 계층"]
        Sources["OHLCV 수집·정규화<br/>data/sources.py"]
        Cache["증분 Parquet 캐시<br/>data/cache.py"]
        History["HistoricalDataFeed<br/>SessionOpen / SessionClose"]
        Polling["PollingDataFeed<br/>PriceTick"]
        Clock["TradingSessionClock<br/>KR/US 장 시간·타임존"]
    end

    subgraph CORE["공용 이벤트·전략 계층"]
        BackEngine["BacktestEngine"]
        PaperEngine["PaperTradingEngine"]
        Context["EngineContext<br/>history / buy / sell / position"]
        Strategy["Strategy Registry<br/>MA Cross / Vol Breakout / RSI"]
        Risk["RiskManager<br/>포지션·현금·일손실·손절 제한"]
    end

    subgraph EXEC["주문·계좌 계층"]
        BrokerAPI["Broker 인터페이스"]
        BackBroker["BacktestBroker<br/>체결 시뮬레이션"]
        PaperBroker["PaperBroker<br/>시뮬레이션 + 자동 저장"]
        KISBroker["KISBroker<br/>현재 스켈레톤·미구현"]
        Portfolio["Portfolio<br/>현금·포지션·평가자산"]
        Fees["수수료·세금·슬리피지<br/>호가단위 반올림"]
    end

    subgraph STORE["로컬 저장소"]
        Parquet[("data/cache/{KR,US}/*.parquet")]
        Listings[("data/cache/_listings/*.csv")]
        State[("state/{name}.json")]
        Reports[("reports/.../report.html<br/>trades.csv")]
    end

    User --> GUI
    User --> CLI
    GUI --> Services
    CLI --> Services
    Config --> Services
    GUI --> Symbols

    FDR --> Sources
    YF --> Sources
    Sources --> Cache
    Cache <--> Parquet
    FDR --> Symbols
    Nasdaq --> Symbols
    Symbols <--> Listings

    Cache --> History
    YF --> Polling
    Clock --> Polling
    Services --> History
    Services --> Polling
    Services --> BackEngine
    Services --> PaperEngine

    History --> BackEngine
    History --> PaperEngine
    Polling --> PaperEngine
    Clock --> PaperEngine
    BackEngine <--> Context
    PaperEngine <--> Context
    Context <--> Strategy
    Context --> Risk
    Context --> BrokerAPI

    BrokerAPI --> BackBroker
    BrokerAPI --> PaperBroker
    BrokerAPI -. "향후 실전 전환" .-> KISBroker
    KISBroker -.-> KISAPI
    BackBroker --> Fees
    PaperBroker --> Fees
    BackBroker <--> Portfolio
    PaperBroker <--> Portfolio
    PaperBroker <--> State

    BackEngine --> Reports
```

현재 실행 경로에서 `KISBroker`는 사용되지 않는다. 모든 메서드가
`NotImplementedError`이며, KIS 인증·주문·체결 조회·잔고 동기화를 구현한 뒤에만
실전 경로로 교체할 수 있다.

## 2. 데이터 수집과 캐시 갱신

```mermaid
flowchart TD
    Start(["data update 또는 GUI 자동 받기"])
    Input["시장·종목·시작일·종료일"]
    Exists{"기존 Parquet가 있는가?"}
    NewStart["요청 시작일 또는 2015-01-01부터 수집"]
    NextDay["캐시의 마지막 날짜 + 1일부터 증분 수집"]
    Older{"요청 시작일이<br/>캐시 최초 날짜보다 이전인가?"}
    OlderStart["요청 시작일부터 앞 구간 보충"]
    Market{"시장"}
    KR["FinanceDataReader.DataReader"]
    US["FinanceDataReader.DataReader<br/>시스템 인증서 + 수정주가 보정"]
    Normalize["열 이름·날짜·타입 정규화<br/>OHLCV만 유지"]
    Merge["기존 + 신규 결합<br/>날짜 정렬·중복 제거"]
    Write["data/cache/MARKET/SYMBOL.parquet 저장"]
    Feed["HistoricalDataFeed가 로컬 캐시만 읽음"]

    Start --> Input --> Exists
    Exists -- "아니오" --> NewStart --> Market
    Exists -- "예" --> Older
    Older -- "예" --> OlderStart --> Market
    Older -- "아니오" --> NextDay --> Market
    Market -- "KR" --> KR --> Normalize
    Market -- "US" --> US --> Normalize
    Normalize --> Merge --> Write --> Feed
```

종목 검색용 목록은 가격 데이터와 별도다. 국내는 KRX 주식과 `ETF/KR`을
FinanceDataReader로 받는다. 미국은 NASDAQ Trader 공식 심볼 디렉터리에서 미국
거래소 상장 주식과 ETF를 받는다. 결과는 `data/cache/_listings/{KR,US}.csv`에
7일 동안 캐시한다. 목록에 없는 종목은 GUI에서 코드를 직접 추가할 수 있으며,
검색 소스 구성이 바뀌면 캐시 버전을 비교해 기간 전에도 목록을 자동 갱신한다.

## 3. 백테스트 동작 순서

백테스트 중에는 네트워크를 사용하지 않고 Parquet 캐시만 읽는다.

```mermaid
sequenceDiagram
    autonumber
    actor User as 사용자
    participant App as CLI/GUI
    participant Svc as services.run_backtest
    participant Feed as HistoricalDataFeed
    participant Engine as BacktestEngine
    participant Strategy as Strategy
    participant Risk as RiskManager
    participant Broker as BacktestBroker
    participant Portfolio as Portfolio
    participant Report as Report

    User->>App: 시장·종목·기간·전략 선택
    App->>Svc: run_backtest(...)
    Svc->>Feed: Parquet 로드, 날짜별 이벤트 구성
    Svc->>Engine: Feed + Strategy + Risk + Broker 조립
    Engine->>Strategy: init(context)

    loop 거래일마다
        Feed-->>Engine: SessionOpen(date, opens)
        Engine->>Broker: 대기 MARKET 주문을 시가에 체결
        Broker->>Portfolio: Fill 반영 및 시가 평가
        Engine->>Risk: start_day(equity)
        Engine->>Strategy: on_open(context, date, opens)

        Feed-->>Engine: SessionClose(date, OHLCV bars)
        Engine->>Broker: LIMIT/STOP을 고가·저가로 판정
        Broker->>Portfolio: 체결·수수료·슬리피지 반영
        Engine->>Broker: 당일 MOC를 종가에 체결
        Engine->>Broker: 남은 DAY 주문 만료
        Engine->>Risk: 일 손실 갱신
        Engine->>Strategy: 종목별 on_bar(context, bar)
        Strategy->>Risk: Context를 통한 신규 주문 검증
        alt 리스크 통과
            Risk-->>Broker: 주문 제출
        else 제한 위반
            Risk-->>Strategy: REJECTED 주문 반환
        end
        Engine->>Risk: 종가 기준 손절 대상 확인
        Engine->>Portfolio: 일말 평가자산 기록
    end

    Engine-->>Svc: BacktestResult
    Svc-->>App: 최종 자산·체결·거부·만료
    App->>Report: 메트릭·차트·거래내역 생성
    Report-->>User: report.html + trades.csv
```

종가(`on_bar`)에서 생성한 일반 `MARKET` 주문은 다음 `SessionOpen`에서 체결된다.
이 순서로 당일 종가 신호를 당일 가격에 체결하는 룩어헤드를 피한다.

## 4. 모의투자 1회 실행 동작

권장 운영 방식은 작업 스케줄러가 5분마다 `paper` 명령을 **한 번씩** 실행하는
형태다. 각 호출은 JSON 상태를 다시 읽으므로 프로세스가 종료되어도 계좌 상태가
이어진다.

```mermaid
sequenceDiagram
    autonumber
    actor Scheduler as 사용자/작업 스케줄러
    participant CLI as CLI/GUI
    participant Svc as build_paper_session
    participant State as state/name.json
    participant Clock as TradingSessionClock
    participant Engine as PaperTradingEngine
    participant Poll as PollingDataFeed
    participant Source as 일봉 수집원
    participant YF as yfinance 폴링
    participant Cache as ParquetCache
    participant Strategy as 공용 Strategy
    participant Broker as PaperBroker

    Scheduler->>CLI: paper --name ...
    CLI->>Svc: 세션 조립
    Svc->>Broker: 생성
    Broker->>State: 기존 현금·포지션·주문·체결 로드
    CLI->>Engine: run_once()
    Engine->>Clock: 현재 시장 시간 확인

    alt 장중
        opt 오늘 첫 장중 실행
            Engine->>Poll: 시가용 현재가 조회
            Poll->>YF: KR .KS→.KQ / US 티커
            Engine->>Broker: 대기 MARKET 체결·시가 평가
            Engine->>Strategy: on_open(...)
            Broker->>State: last_open_date 포함 자동 저장
        end
        Engine->>Clock: 마지막 폴링 후 5분 경과 확인
        Clock-->>Engine: 폴링 가능
        Engine->>Poll: poll()
        Poll->>YF: 지연 현재가 조회
        Engine->>Broker: 단일가 Bar로 LIMIT/STOP 판정
        Broker->>State: 체결·평가 상태 자동 저장
    else 장 마감 후
        Engine->>Cache: 당일 확정 일봉 증분 업데이트
        Cache->>Source: KR/US=FinanceDataReader
        alt 오늘 확정 일봉 있음
            Engine->>Broker: MOC 체결·DAY 만료
            Engine->>Strategy: on_bar(...)
            Broker->>State: last_close_date 포함 자동 저장
        else 수집 오류 + 폴링 가격 있음
            Engine->>Poll: 마지막 가격으로 fallback Bar 생성
            Engine->>Broker: close_fallback 처리
            Engine->>Strategy: on_bar(...)
            Broker->>State: 자동 저장
        else 정상 응답이나 오늘 봉 없음
            Note over Engine: 공휴일/미발행으로 보고 마감 처리 생략
        end
    else 장 시작 전·주말
        Note over Engine: 주문/가격 처리 없이 현재 상태만 반환
    end

    Engine-->>CLI: actions, cash, equity, positions, open_orders
    CLI-->>Scheduler: 실행 결과 출력
```

장 마감 확정 일봉의 수집 과정은 2번 다이어그램과 동일하다.

## 5. 전략 주문부터 체결까지

```mermaid
flowchart TD
    Signal["Strategy<br/>on_open / on_bar / on_fill"]
    Context["EngineContext.buy / sell"]
    Qty["qty 또는 equity × weight로<br/>정수 수량 계산"]
    Order["Order 생성<br/>MARKET / LIMIT / STOP / MOC<br/>DAY / GTC"]
    Risk{"RiskManager.validate"}
    Rejected["REJECTED<br/>사유 기록"]
    Submit["Broker.submit<br/>미체결 큐 등록"]
    Event{"다음 가격 이벤트"}
    Open["SessionOpen<br/>MARKET"]
    Intra["Intraday Bar / PriceTick<br/>LIMIT·STOP 트리거"]
    Close["SessionClose<br/>MOC"]
    Expire["장 마감 후<br/>남은 DAY 주문 만료"]
    Price["기준 체결가 결정"]
    Slip["매수 불리 / 매도 불리<br/>슬리피지 적용"]
    Tick["KR 호가단위 / US $0.01<br/>매수 올림·매도 내림"]
    Fee["수수료·매도세·SEC·TAF 계산"]
    Cash{"매수 현금 충분?"}
    Fill["Fill 생성"]
    Portfolio["Portfolio 반영<br/>현금·평단·포지션·실현손익"]
    Callback["Strategy.on_fill"]
    Persist["PaperBroker면 JSON 자동 저장"]

    Signal --> Context --> Qty --> Order --> Risk
    Risk -- "위반" --> Rejected
    Risk -- "통과" --> Submit --> Event
    Event -- "시가" --> Open --> Price
    Event -- "장중/일봉 고저가" --> Intra --> Price
    Event -- "종가" --> Close --> Price
    Event -- "미체결" --> Expire
    Price --> Slip --> Tick --> Fee --> Cash
    Cash -- "아니오" --> Rejected
    Cash -- "예" --> Fill --> Portfolio --> Callback --> Persist
```

리스크 검사는 매수 주문에 대해 다음을 적용한다.

- 일 손실 한도 도달 시 신규 진입 차단
- 최대 보유 종목 수
- 종목당 최대 평가 비중
- 최소 현금 버퍼
- 장 마감 후 평단 대비 손절 기준 충족 시 매도 주문 생성

매도 주문은 포지션 청산을 막지 않도록 신규 진입 제한 검사를 통과시킨다.

## 6. 실행 모드 비교

| 구분 | 백테스트 | 모의투자 | KIS 실전/모의 API |
|---|---|---|---|
| 엔진 | `BacktestEngine` | `PaperTradingEngine` | 미연결 |
| 가격 | 로컬 확정 일봉 | yfinance 폴링 + 확정 일봉 | 향후 KIS 시세/체결 조회 |
| 브로커 | `BacktestBroker` | `PaperBroker` | `KISBroker` 스켈레톤 |
| 체결 | OHLCV 규칙으로 시뮬레이션 | 폴링 가격으로 시뮬레이션 | 거래소/증권사 실제 체결 |
| 상태 | 실행 중 메모리 | `state/{name}.json` 영속화 | 구현 필요 |
| 결과 | HTML·CSV 리포트 | 콘솔/GUI 상태 및 JSON | 구현 필요 |
| 네트워크 | 실행 시 사용 안 함 | 장중·마감 데이터 조회 시 사용 | 필수 |

## 7. 주요 코드 위치

| 역할 | 파일 |
|---|---|
| CLI / GUI 진입점 | `src/tradingbot/cli.py`, `src/tradingbot/gui.py` |
| 객체 조립과 공용 서비스 | `src/tradingbot/services.py` |
| 외부 OHLCV 수집 / Parquet | `src/tradingbot/data/sources.py`, `data/cache.py` |
| 과거 이벤트 / 장중 폴링 | `src/tradingbot/data/feed.py`, `data/polling.py` |
| 백테스트 / 모의 엔진 | `src/tradingbot/engine/engine.py`, `engine/paper.py` |
| 세션 시간 | `src/tradingbot/engine/clock.py` |
| 거래소 캘린더 (휴장일·조기폐장) | `src/tradingbot/engine/calendar.py` |
| 전략 공통 인터페이스 | `src/tradingbot/strategies/base.py` |
| 전략 상태 영속화 | `src/tradingbot/strategies/state.py` |
| signal_id 멱등성 원장 | `src/tradingbot/strategies/signals.py` |
| Point-in-Time 가격 조회 | `src/tradingbot/data/store.py` |
| 연구·검증 (IC/분위수/Walk-forward) | `src/tradingbot/research/` |
| 가치평가·의사결정 코어 (DCF/IRR/역산/신호) | `src/tradingbot/valuation/` |
| 횡단면 팩터 (모멘텀 등) | `src/tradingbot/factors/` |
| 주문·체결 시뮬레이션 | `src/tradingbot/broker/backtest.py` |
| 모의 계좌 영속화 | `src/tradingbot/broker/paper.py` |
| 향후 KIS 연동 슬롯 | `src/tradingbot/broker/kis.py` |
| 포트폴리오 / 리스크 | `src/tradingbot/portfolio.py`, `src/tradingbot/risk.py` |
| 메트릭 / HTML 리포트 | `src/tradingbot/report/metrics.py`, `report/report.py` |

## 8. VS Code에서 보는 방법

현재 설치된 VS Code 1.128에서는 Mermaid가 기본 기능으로 포함되어 있으므로
확장팩이 필요 없다.

1. 이 파일(`docs/architecture.md`)을 연다.
2. `Ctrl+Shift+V`를 눌러 미리보기를 열거나 `Ctrl+K`, `V`를 순서대로 눌러
   오른쪽에 미리보기를 연다.
3. 큰 다이어그램은 미리보기 위에서 확대·축소하고 이동할 수 있다.

VS Code 1.120 이하를 사용한다면 업데이트를 권장한다. 별도 편집·PNG/SVG 내보내기,
문법 오류 표시가 필요할 때만 공식 **Mermaid Chart** 확장
(`MermaidChart.vscode-mermaid-chart`)을 선택적으로 설치한다.

```powershell
code --install-extension MermaidChart.vscode-mermaid-chart
```

Mermaid 렌더러 확장을 여러 개 동시에 활성화하면 미리보기에서 충돌할 수 있으므로
하나만 사용한다. 과거의 `bierner.markdown-mermaid` 확장은 VS Code 1.121에
기본 기능으로 통합되어 현재는 deprecated 상태다.

## 9. 현재 구현상 주의점

- 모의투자 폴링 가격은 지연될 수 있어 실시간 체결 재현이 아니다.
- 일봉 기반 백테스트는 봉 내부의 실제 가격 경로와 부분 체결·호가 깊이를 모른다.
- 세션 클록은 `exchange_calendars`(XKRX/XNYS) 기반 거래소 캘린더를 사용해
  공휴일·조기폐장·지연개장(예: KRX 신년 첫 거래일 10시 개장)을 반영한다.
  캘린더 데이터 범위를 벗어난 날짜는 평일 규칙으로 폴백하고 경고를 남긴다.
- 전략 내부 상태(예: `rsi_reversion`의 보유일 카운터)는 모의투자에서
  `state/{이름}.strategy.json`에 저장되어 프로세스 재시작 후 복구된다.
  상태 파일이 손상되면 조용히 초기화하지 않고 오류를 발생시킨다.
- 현재는 한 프로세스가 한 시장·한 통화를 담당하며 환율 변환이 없다.
- KIS 브로커는 실제 주문이 불가능한 미구현 슬롯이다.
