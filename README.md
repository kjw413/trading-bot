# 주식 트레이딩 봇

국내(KOSPI/KOSDAQ)와 미국 주식을 같은 이벤트 기반 엔진으로 백테스트하고, 모의투자 상태를 파일로 보존하며, 이후 실전 브로커를 붙일 수 있게 만든 프로젝트입니다.

## 현재 범위: M5 (v1 완료) + 퀀트 시스템 확장 착수

v1 이후 [`trading_bot_agentic_ai_execution_plan_260714.md`](trading_bot_agentic_ai_execution_plan_260714.md)에
따라 퀀트 투자 시스템으로 확장 중입니다. 현재까지 반영된 확장:

- 퀀트 연구 기준 문서(`docs/quant_research_spec.md`)와 설정(`config/research.toml`) — M6
- KR/US 거래소 캘린더(`exchange_calendars` XKRX/XNYS): 공휴일·조기폐장·지연개장을
  세션 클록에 반영 — M7 일부
- 전략 상태 영속화(`strategies/state.py`): `rsi_reversion` 보유일 카운터가
  재시작 후 복구됨 — M14 일부
- `signal_id` 멱등성 원장(`strategies/signals.py`): 동일 신호 재실행 시 중복 주문 방지 — M14 일부
- 횡단면 Factor 인터페이스·레지스트리와 3·6·12개월(및 12-1) 모멘텀 팩터
  (`factors/`, `data/store.py` Point-in-Time 조회) — M9 일부
- 연구·검증 프레임워크(`research/`): forward return 라벨, Spearman IC,
  분위수 분석, Walk-forward, 실험 기록, 팩터 채택 게이트와
  `research report` CLI — M10

### v1 완료 항목

- parquet 기반 일봉 캐시와 증분 업데이트
- `HistoricalDataFeed` 이벤트 루프
- MARKET / LIMIT / STOP / MOC 체결
- DAY 만료, 슬리피지, KR/US 수수료 모델
- `RiskManager` 기본 리스크 제한
- `ma_cross`, `vol_breakout`, `rsi_reversion` 전략
- CAGR, MDD, Sharpe, 승률, profit factor, exposure 메트릭
- 자산곡선/드로다운 차트가 포함된 단일 HTML 리포트와 `trades.csv`
- CLI `data update`, `backtest`, `paper`, `strategies`
- 모의투자: 세션 클록, 폴링 피드, 마감 확정 일봉 갱신, JSON 상태 영속화
- M5: KIS 브로커 스켈레톤(`broker/kis.py`, 미구현 시그니처), 체크인 fixture 기반
  고정값 회귀 테스트(`tests/test_smoke_backtest.py`), 공휴일(확정 일봉 없음) 마감 스킵

## 실행 환경

Python 3.13 기준입니다. 이 저장소는 `uv.lock`을 포함합니다.

```powershell
py -m uv sync --extra dev
.\.venv\Scripts\python.exe -m tradingbot strategies
```

`uv` 실행 파일이 PATH에 있다면 `uv sync --extra dev`를 써도 됩니다. 가상환경을 활성화한 셸에서는 `python -m tradingbot ...` 형태로 실행하면 됩니다.

## GUI

CLI 대신 데스크톱 GUI(Tkinter, 추가 의존성 없음)로 같은 기능을 쓸 수 있습니다. 주식 초보자도 쓸 수 있도록 만들어져 있습니다.

- 종목을 **이름으로 검색**해서 선택 (예: "삼성전자" → 005930 자동 변환). 한국은 KRX 주식과 국내 상장 ETF, 미국은 NASDAQ Trader에 등록된 미국 거래소 상장 주식과 ETF가 검색됩니다. 그 밖의 티커는 "코드 직접 추가"로 넣을 수 있습니다. 종목 목록은 `data/cache/_listings/`에 7일간 캐시되며, 검색 범위가 바뀌면 자동 갱신됩니다.
- 백테스트/모의투자 실행 전 **시세 데이터 자동 받기**(기본 켜짐) — `data update`를 따로 몰라도 됩니다.
- 백테스트 완료 시 HTML 리포트가 브라우저에 자동으로 열립니다.
- 전략 선택 시 한 줄 설명 표시, 진행 상황 로그 창, 도움말 메뉴/버튼으로 여는 [사용 설명서](docs/manual.html) 제공.

```powershell
.\.venv\Scripts\python.exe -m tradingbot gui
```

가장 쉬운 실행법은 저장소 루트의 **`트레이딩봇 실행.bat` 더블클릭**입니다. `.venv`가 없으면 `uv sync`까지 자동으로 수행한 뒤 콘솔 창 없이 GUI를 띄웁니다. 설치된 스크립트 `tradingbot-gui`(창만 뜨고 콘솔이 없는 GUI 엔트리)를 사용해도 됩니다. `--config` 없이 실행하면 `config/default.toml`을 사용하고, 상단 "설정 파일" 입력으로 다른 TOML을 지정할 수 있습니다. CLI와 GUI는 `src/tradingbot/services.py`의 동일한 실행 로직을 공유하며, 종목명 검색은 `src/tradingbot/symbols.py`가 담당합니다.

주식이 처음인 사용자를 위한 안내는 [docs/manual.html](docs/manual.html)에 있습니다 (GUI의 "사용 설명서" 버튼으로도 열림).

## 데이터 업데이트

공개 데이터 소스에서 일봉을 받아 `data/cache/<MARKET>/<SYMBOL>.parquet`에 저장합니다. 국내·미국 일봉은 FinanceDataReader를 사용하고, 미국은 Windows 시스템 인증서 저장소를 통해 SSL을 검증합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 --start 2020-01-01
.\.venv\Scripts\python.exe -m tradingbot data update --market US --symbols AAPL --start 2020-01-01
```

국내 종목은 KOSPI/KOSDAQ 모두 사용자 입력을 6자리 코드로 유지하세요. 모의투자 장중 폴링만 내부에서 yfinance용 `.KS`를 먼저 시도하고 실패하면 `.KQ`로 폴백합니다.

## 백테스트와 리포트

백테스트는 네트워크를 사용하지 않고 캐시된 parquet만 읽습니다. 기본적으로 `reports/<timestamp>_<strategy>_<market>/report.html`과 `trades.csv`를 생성합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot backtest --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01
```

리포트를 만들지 않으려면 `--no-report`를 추가합니다. 출력은 최종 자산, 수익률, 체결수, 거부 주문, 만료 주문, 리포트 경로입니다.

## 모의투자

`paper` 명령은 장중 가격을 폴링해 기존 전략을 모의 계좌에 적용하고, 계좌/주문/체결 상태를 `state/<name>.json`에 저장합니다. 기본 실행은 1회 실행이라 Windows 작업 스케줄러나 cron에 걸기 쉽고, 운영 방식으로도 이 형태를 권장합니다.

처음 실행하기 전에는 전략 워밍업에 필요한 일봉 캐시를 한 번 준비해야 합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market KR --symbols 005930 --start 2020-01-01
.\.venv\Scripts\python.exe -m tradingbot paper --name kr-demo --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01
```

장 마감 후 실행되면 `paper`가 당일 확정 일봉을 자동으로 `cache.update()`하고, 그 OHLCV 봉으로 MOC 체결과 `strategy.on_bar()`를 처리합니다. 데이터 소스 장애로 확정 일봉을 가져오지 못하면 마지막 폴링 시세로 만든 fallback 봉을 사용하고 출력 action이 `close_fallback`으로 표시됩니다.

계속 실행하려면 `--loop`를 사용할 수 있습니다. 루프는 일시 예외를 로깅하고 계속 돌도록 되어 있지만, 네트워크/TLS 장애에서 다음 스케줄 실행으로 자연 복구되는 1회 실행 방식이 더 단순합니다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot paper --name us-demo --strategy rsi_reversion --market US --symbols AAPL --start 2020-01-01 --loop
```

폴링 가격은 yfinance를 사용합니다. yfinance 가격은 지연될 수 있으며, 국내 종목은 6자리 코드 입력을 기준으로 내부에서 `.KS` 다음 `.KQ`를 시도합니다.

작업 스케줄러로 5분마다 1회 실행하는 예시는 다음과 같습니다.

```powershell
$python = "E:\trading-bot\.venv\Scripts\python.exe"
$repo = "E:\trading-bot"
$action = New-ScheduledTaskAction -Execute $python -Argument "-m tradingbot paper --name kr-demo --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 7)
Register-ScheduledTask -TaskName "TradingBot Paper KR" -Action $action -Trigger $trigger
```

실시간 세션 클록과 폴링 피드는 각각 `TradingSessionClock`과 `PollingDataFeed`로 분리되어 있습니다. 테스트에서는 `now_provider`와 가짜 price fetcher를 주입해 장 시간이나 네트워크에 의존하지 않고 검증합니다.

## 운영 PC 셋업 체크리스트

개발 머신이 아닌 운영용 PC로 옮길 때 순서입니다.

1. 저장소 클론 후 의존성 설치: `git clone <repo>` → `py -m uv sync --extra dev`
2. 설치 검증(네트워크 불필요): `.\.venv\Scripts\python.exe -m pytest -q` — 전부 통과해야 합니다.
3. 전략 워밍업 캐시 준비: `data update`를 종목별로 1회 실행 (위 데이터 업데이트 섹션 참고)
4. 모의투자 1회 실행으로 상태 파일 생성 확인: `paper --name <운영이름> ...` → `state/<이름>.json` 생성 확인
5. 작업 스케줄러 등록 (아래 예시): 장중 5분 간격 `paper` 1회 실행 + 매일 장 시작 전 `data update`
6. 며칠 관찰: 출력의 `actions`에 `close`가 정상 기록되는지, `close_fallback`이 반복되지 않는지 확인

장 시작 전 `data update`용 스케줄 예시(평일 08:40):

```powershell
$python = "E:\trading-bot\.venv\Scripts\python.exe"
$repo = "E:\trading-bot"
$action = New-ScheduledTaskAction -Execute $python -Argument "-m tradingbot data update --market KR --symbols 005930 --start 2020-01-01" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 08:40
Register-ScheduledTask -TaskName "TradingBot Data Update KR" -Action $action -Trigger $trigger
```

## 운영 노트

- `rsi_reversion`의 `holding_days`는 현재 전략 인스턴스 메모리에만 있습니다. 모의투자 프로세스를 재시작하면 최대 보유일 카운터가 리셋될 수 있습니다.
- exposure 메트릭은 일말 기준 포지션 보유일 비율입니다. 당일 진입 후 당일 청산하는 데이트레이딩 전략은 exposure가 낮거나 0%로 표시될 수 있습니다.
- `close_fallback`이 반복되면 확정 일봉 캐시가 최신화되지 않은 상태라는 뜻입니다. 네트워크 또는 데이터 소스 상태를 확인하고 다음 정상 마감 실행에서 캐시가 갱신되는지 확인하세요.
- 세션 클록은 요일만 확인하고 공휴일 캘린더는 모릅니다. 평일 공휴일에는 장중 폴링이 정체된 지연 시세를 볼 수 있으나, 마감 처리는 확정 일봉이 하나도 없으면 비거래일로 간주하고 건너뜁니다. 다만 공휴일 장중에 제출된 DAY 주문은 다음 거래일 마감까지 살아 있을 수 있으니, 공휴일 전후 체결 내역은 한 번 확인하세요.
- 실전 전환은 `broker/kis.py` 스켈레톤 구현이 선행되어야 합니다. 모든 메서드가 `NotImplementedError`이며, TR ID와 엔드포인트는 KIS Developers 최신 문서로 검증한 뒤 구현하세요. `app_key`/`app_secret`은 절대 저장소에 커밋하지 마세요.
