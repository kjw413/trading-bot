# 주식 트레이딩 봇

국내(KOSPI/KOSDAQ)와 미국 주식을 같은 이벤트 기반 엔진으로 백테스트하고, 이후 모의투자와 실전 브로커를 붙일 수 있게 만든 프로젝트입니다.

## 현재 범위: M1

- parquet 기반 일봉 캐시
- `HistoricalDataFeed`
- MARKET 주문만 처리하는 `BacktestBroker`
- 수수료 0.015% 단순 적용
- `ma_cross` 전략
- CLI `data update`, `backtest`, `strategies`

## 실행

의존성 설치 후 로컬에서 바로 실행할 수 있습니다.

```powershell
uv sync --extra dev
python -m tradingbot strategies
```

`uv`가 없다면 Python 3.13 환경에 의존성을 설치한 뒤 같은 명령을 실행하면 됩니다.

## 데이터 업데이트

공개 데이터 소스에서 일봉을 받아 `data/cache/<MARKET>/<SYMBOL>.parquet`에 저장합니다.

```powershell
python -m tradingbot data update --market KR --symbols 005930 --start 2020-01-01
python -m tradingbot data update --market US --symbols AAPL --start 2020-01-01
```

국내 데이터는 FinanceDataReader, 미국 데이터는 yfinance를 사용합니다.

## 백테스트

백테스트는 네트워크를 사용하지 않고 캐시된 parquet만 읽습니다.

```powershell
python -m tradingbot backtest --strategy ma_cross --market KR --symbols 005930 --start 2020-01-01
```

출력은 최종 자산, 수익률, 거래수입니다.
