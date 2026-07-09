# 주식 트레이딩 봇

국내(KOSPI/KOSDAQ)와 미국 주식을 같은 이벤트 기반 엔진으로 백테스트하고, 모의투자 상태를 파일로 보존하며, 이후 실전 브로커를 붙일 수 있게 만든 프로젝트입니다.

## 현재 범위: M4

- parquet 기반 일봉 캐시와 증분 업데이트
- `HistoricalDataFeed` 이벤트 루프
- MARKET / LIMIT / STOP / MOC 체결
- DAY 만료, 슬리피지, KR/US 수수료 모델
- `RiskManager` 기본 리스크 제한
- `ma_cross`, `vol_breakout`, `rsi_reversion` 전략
- CAGR, MDD, Sharpe, 승률, profit factor, exposure 메트릭
- 자산곡선/드로다운 차트가 포함된 단일 HTML 리포트와 `trades.csv`
- CLI `data update`, `backtest`, `paper`, `strategies`
- M4 모의투자: 세션 클록, 폴링 피드, JSON 상태 영속화

## 실행 환경

Python 3.13 기준입니다. 이 저장소는 `uv.lock`을 포함합니다.

```powershell
py -m uv sync --extra dev
.\.venv\Scripts\python.exe -m tradingbot strategies
```

`uv` 실행 파일이 PATH에 있다면 `uv sync --extra dev`를 써도 됩니다. 가상환경을 활성화한 셸에서는 `python -m tradingbot ...` 형태로 실행하면 됩니다.

## 데이터 업데이트

공개 데이터 소스에서 일봉을 받아 `data/cache/<MARKET>/<SYMBOL>.parquet`에 저장합니다. 국내 데이터는 FinanceDataReader, 미국 데이터는 yfinance를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 --start 2020-01-01
.\.venv\Scripts\python.exe -m tradingbot data update --market US --symbols AAPL --start 2020-01-01
```

## 백테스트와 리포트

백테스트는 네트워크를 사용하지 않고 캐시된 parquet만 읽습니다. 기본적으로 `reports/<timestamp>_<strategy>_<market>/report.html`과 `trades.csv`를 생성합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot backtest --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01
```

리포트를 만들지 않으려면 `--no-report`를 추가합니다. 출력은 최종 자산, 수익률, 체결수, 거부 주문, 만료 주문, 리포트 경로입니다.

## 모의투자

`paper` 명령은 장중 가격을 폴링해 기존 전략을 모의 계좌에 적용하고, 계좌/주문/체결 상태를 `state/<name>.json`에 저장합니다. 기본 실행은 1회 실행이라 Windows 작업 스케줄러나 cron에 걸기 쉽습니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot paper --name kr-demo --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01
```

계속 실행하려면 `--loop`를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot paper --name us-demo --strategy rsi_reversion --market US --symbols AAPL --start 2020-01-01 --loop
```

폴링 가격은 yfinance를 사용합니다. yfinance 가격은 지연될 수 있고, 국내 숫자 종목은 기본적으로 `<code>.KS`로 조회합니다. KOSDAQ 등 다른 접미사가 필요한 경우 `091990.KQ`처럼 접미사를 포함해 넘기세요.

작업 스케줄러로 5분마다 1회 실행하는 예시는 다음과 같습니다.

```powershell
$python = "E:\trading-bot\.venv\Scripts\python.exe"
$repo = "E:\trading-bot"
$action = New-ScheduledTaskAction -Execute $python -Argument "-m tradingbot paper --name kr-demo --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 7)
Register-ScheduledTask -TaskName "TradingBot Paper KR" -Action $action -Trigger $trigger
```

실시간 세션 클록과 폴링 피드는 각각 `TradingSessionClock`과 `PollingDataFeed`로 분리되어 있습니다. 테스트에서는 `now_provider`와 가짜 price fetcher를 주입해 장 시간이나 네트워크에 의존하지 않고 검증합니다.
