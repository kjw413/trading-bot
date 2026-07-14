---
title: Trading Bot Quant System Development Plan
repository: kjw413/trading-bot
document_type: agentic_ai_execution_spec
language: ko
version: 1.0
status: ready_for_execution
---

# Trading Bot 퀀트 투자 시스템 개발 실행 계획

## 0. 문서 목적

이 문서는 `kjw413/trading-bot` 저장소를 단순 기술적 분석 기반 트레이딩 봇에서, 데이터·수학적 알고리즘·팩터 검증·포트폴리오 최적화·모의투자·실전투자까지 연결되는 퀀트 투자 시스템으로 발전시키기 위한 단일 실행 명세서다.

Agentic AI는 이 문서를 기준으로 다음을 수행한다.

1. 현재 저장소 구조와 구현 상태를 파악한다.
2. 기존 기능을 최대한 유지하면서 필요한 계층을 확장한다.
3. 한 번에 대규모 변경을 하지 않고 Milestone 단위로 구현한다.
4. 각 단계마다 테스트, 문서, 완료 기준을 함께 충족한다.
5. 데이터 누수, 룩어헤드 바이어스, 생존자 편향, 과최적화를 최우선으로 방지한다.
6. 실전 주문 연동보다 데이터 신뢰성과 전략 검증을 먼저 완성한다.

---

# 1. 프로젝트 최종 목표

## 1.1 핵심 목표

다음 흐름을 하나의 시스템 안에서 지원한다.

```text
데이터 수집
→ 데이터 정제 및 Point-in-Time 저장
→ 팩터 생성
→ 전략 연구
→ 백테스트
→ Walk-forward 검증
→ 포트폴리오 구성
→ 모의투자
→ 실전 주문
→ 운영 모니터링
```

## 1.2 투자 대상

초기 목표는 아래 순서로 확장한다.

### 1단계
한국·미국 ETF 기반 자산배분 및 모멘텀 전략

### 2단계
한국·미국 개별 종목 기반 멀티팩터 전략

### 3단계
가격·재무·거시·수급 데이터를 활용한 머신러닝 종목 랭킹

## 1.3 기본 원칙

- 예측 정확도보다 재현성과 검증 가능성을 우선한다.
- 실전 주문 기능보다 데이터와 백테스트 신뢰성을 우선한다.
- 모든 전략 결과는 벤치마크와 비교한다.
- 모든 백테스트는 거래비용과 슬리피지를 반영한다.
- 모든 실험은 Git commit, 데이터 버전, 파라미터를 기록한다.
- 테스트 기간 데이터는 전략 선택과 파라미터 조정에 사용하지 않는다.
- 비밀키와 계좌정보는 저장소에 커밋하지 않는다.
- 실전 주문은 기본 비활성화한다.

---

# 2. 현재 저장소 상태

## 2.1 현재 구현된 기능

현재 저장소에는 다음 기능이 구현되어 있다.

### 데이터
- 한국 일봉 데이터: FinanceDataReader
- 미국 일봉 데이터: yfinance
- 한국 주식 및 ETF 종목 검색
- 미국 거래소 상장 주식 및 ETF 종목 검색
- Parquet 기반 가격 데이터 캐시
- 증분 데이터 업데이트
- OHLCV 정규화

### 백테스트
- 이벤트 기반 BacktestEngine
- HistoricalDataFeed
- MARKET 주문
- LIMIT 주문
- STOP 주문
- MOC 주문
- DAY 주문 만료
- 한국·미국 거래비용
- 슬리피지
- 호가단위 반올림
- 포트폴리오 현금 및 포지션 관리

### 전략
- 이동평균 교차
- RSI 평균회귀
- 변동성 돌파

### 리스크
- 종목별 최대 비중
- 최대 보유 종목 수
- 일일 최대 손실
- 손절 기준
- 최소 현금 비중

### 리포트
- 총수익률
- CAGR
- MDD
- Sharpe Ratio
- 승률
- Profit Factor
- Exposure
- 자산곡선
- Drawdown
- 거래내역 CSV

### 모의투자
- TradingSessionClock
- PollingDataFeed
- PaperBroker
- JSON 상태 저장
- 작업 스케줄러 기반 반복 실행
- 장 마감 확정 일봉 처리

### 인터페이스
- CLI
- Tkinter GUI
- 종목명 검색
- 자동 데이터 수집
- 백테스트 실행
- 모의투자 실행
- HTML 리포트 열기

### 실전 브로커
- KISBroker 인터페이스 스켈레톤 존재
- 실제 인증·주문·체결·잔고 기능은 미구현

---

## 2.2 현재 프로젝트의 성격

현재 시스템은 다음에 가깝다.

```text
가격 OHLCV 기반 기술적 분석 전략 실행기
+ 백테스트 엔진
+ 모의투자 엔진
```

아직 다음 기능은 부족하다.

```text
기업 재무 데이터
거시경제 데이터
수급 데이터
산업·테마 데이터
날짜별 유니버스
Point-in-Time 데이터
횡단면 팩터
팩터 검증
종목 순위
포트폴리오 최적화
Walk-forward 검증
실험 추적
실전 브로커 연동
```

---

# 3. 현재 구조의 강점

## 3.1 이벤트 기반 엔진

백테스트와 모의투자가 동일한 전략 인터페이스를 사용한다.

```text
Strategy
→ StrategyContext
→ Broker Interface
→ BacktestBroker / PaperBroker / KISBroker
```

이 구조는 향후 팩터 전략과 포트폴리오 전략을 추가할 때 재사용 가능하다.

## 3.2 룩어헤드 방지 구조

종가 신호는 다음 거래일 시가 주문으로 처리된다.

```text
당일 종가 확인
→ 전략 신호 생성
→ 다음 거래일 시가 체결
```

이 흐름을 유지해야 한다.

## 3.3 가격 데이터 캐시

백테스트 중 네트워크 요청을 하지 않고 로컬 Parquet만 사용한다.

## 3.4 회귀 테스트

고정 fixture를 사용해 엔진 수정 전후 결과를 비교한다.

---

# 4. 핵심 문제와 기술 부채

## 4.1 OHLCV 데이터만 존재

현재 데이터는 아래 컬럼에 집중되어 있다.

```text
open
high
low
close
volume
```

따라서 가치·퀄리티·성장성·거시·수급 기반 전략을 구현할 수 없다.

## 4.2 횡단면 분석 구조 부재

현재 전략은 개별 종목별 `on_bar()` 중심이다.

필요한 구조는 다음과 같다.

```text
전체 유니버스
→ 종목별 팩터 계산
→ 팩터 표준화
→ 종합점수 계산
→ 상위 N종목 선택
→ 목표 비중 생성
→ 리밸런싱 주문
```

## 4.3 전략 검증 부족

현재 회귀 테스트는 엔진의 일관성을 검증할 뿐 전략의 경제적 유효성을 검증하지 않는다.

필요한 검증:

- 벤치마크 대비 초과수익
- In-sample / Validation / Out-of-sample
- Walk-forward
- 파라미터 민감도
- 거래비용 민감도
- 시장 국면별 성과
- 팩터 IC
- 분위수 수익률
- 회전율
- 과최적화 검정

## 4.4 전략 상태 영속화 부족

예: RSI 전략의 `holding_days`가 메모리에만 존재한다.

재시작 후에도 다음 상태가 유지되어야 한다.

- 보유기간
- 마지막 리밸런싱 날짜
- 최근 팩터 점수
- 목표 비중
- 처리 완료 신호 ID
- 전략별 내부 상태

## 4.5 거래소 휴장일 처리 부족

요일 기반 장 시간 판단만 존재한다.

반드시 실제 거래소 캘린더를 사용해야 한다.

## 4.6 RiskManager 결합도

RiskManager가 특정 BacktestBroker 내부 구조에 의존하지 않도록 추상 Broker 인터페이스 중심으로 변경해야 한다.

## 4.7 실전 브로커 미구현

KISBroker는 인증·잔고·주문·체결 조회가 구현되지 않았다.

---

# 5. 목표 아키텍처

```text
1. 원천 데이터 계층
   ├─ 가격·거래량
   ├─ 기업 재무
   ├─ 기업 이벤트
   ├─ 시장·거시경제
   ├─ 산업·테마
   └─ 수급

2. Point-in-Time 데이터 계층
   ├─ 발표일
   ├─ 데이터 사용가능일
   ├─ 수정주가
   ├─ 상장·폐지 이력
   ├─ 지수 편입 이력
   └─ 데이터 버전

3. 팩터 계층
   ├─ 모멘텀
   ├─ 가치
   ├─ 퀄리티
   ├─ 성장성
   ├─ 변동성
   ├─ 유동성
   └─ 수급

4. 연구·검증 계층
   ├─ Label 생성
   ├─ IC 분석
   ├─ 분위수 분석
   ├─ Walk-forward
   ├─ 비용 민감도
   └─ 시장 국면 분석

5. 포트폴리오 계층
   ├─ 유니버스 필터
   ├─ 종목 랭킹
   ├─ 비중 계산
   ├─ 리밸런싱
   └─ 제약조건

6. 실행 계층
   ├─ 백테스트
   ├─ 모의투자
   └─ 실전투자

7. 운영 계층
   ├─ 데이터 수집 상태
   ├─ 신호 변경
   ├─ 주문·체결
   ├─ 포트폴리오 위험
   └─ 전략 성과
```

---

# 6. 권장 코드 구조

```text
src/tradingbot/
├─ data/
│  ├─ cache.py
│  ├─ feed.py
│  ├─ polling.py
│  ├─ sources.py
│  ├─ prices.py
│  ├─ fundamentals.py
│  ├─ macro.py
│  ├─ flows.py
│  ├─ universe.py
│  ├─ corporate_actions.py
│  ├─ quality.py
│  └─ store.py
├─ factors/
│  ├─ base.py
│  ├─ registry.py
│  ├─ transform.py
│  ├─ momentum.py
│  ├─ reversal.py
│  ├─ volatility.py
│  ├─ liquidity.py
│  ├─ value.py
│  ├─ quality.py
│  ├─ growth.py
│  └─ flow.py
├─ research/
│  ├─ dataset.py
│  ├─ labels.py
│  ├─ ic.py
│  ├─ quantiles.py
│  ├─ walk_forward.py
│  ├─ sensitivity.py
│  ├─ regime.py
│  └─ experiment.py
├─ portfolio/
│  ├─ universe.py
│  ├─ ranking.py
│  ├─ weights.py
│  ├─ rebalance.py
│  ├─ constraints.py
│  └─ optimizer.py
├─ strategies/
│  ├─ base.py
│  ├─ ma_cross.py
│  ├─ rsi_reversion.py
│  ├─ vol_breakout.py
│  ├─ etf_momentum.py
│  └─ multifactor_rank.py
├─ broker/
│  ├─ base.py
│  ├─ backtest.py
│  ├─ paper.py
│  ├─ fees.py
│  └─ kis.py
├─ engine/
│  ├─ engine.py
│  ├─ paper.py
│  ├─ clock.py
│  └─ rebalance.py
├─ report/
│  ├─ metrics.py
│  ├─ report.py
│  ├─ factor_report.py
│  └─ portfolio_report.py
└─ monitoring/
   ├─ health.py
   ├─ reconciliation.py
   ├─ alerts.py
   └─ audit.py
```

---

# 7. 데이터 저장 구조

```text
data/
├─ raw/
│  ├─ prices/
│  ├─ fundamentals/
│  ├─ macro/
│  ├─ flows/
│  ├─ universe/
│  └─ corporate_actions/
├─ processed/
│  ├─ prices/
│  ├─ fundamentals_pit/
│  ├─ universe/
│  ├─ macro/
│  └─ flows/
├─ features/
│  ├─ daily/
│  └─ monthly/
├─ experiments/
└─ cache/
```

모든 데이터셋은 가능하면 다음 필드를 포함한다.

```text
symbol
market
date
source
as_of_date
available_at
ingested_at
data_version
```

재무 데이터는 반드시 다음 날짜를 구분한다.

```text
report_period
announcement_date
available_at
```

백테스트는 `available_at` 이전에 해당 데이터를 사용해서는 안 된다.

---

# 8. Milestone 실행 계획

# M6. 퀀트 연구 기준 정의

## 목표

연구 대상, 벤치마크, 검증 방식, 전략 승격 기준을 문서화한다.

## 작업

1. `docs/quant_research_spec.md` 생성
2. `config/research.toml` 생성
3. 첫 전략은 ETF 모멘텀 로테이션으로 정의
4. 기본 투자 주기는 월 1회
5. 기본 벤치마크 정의
6. 투자 가능 자산 목록 정의
7. 실험 기간 구분 정의

## 권장 초기 전략

```text
대상:
한국·미국 주식 ETF
채권 ETF
원자재 ETF
섹터 ETF

신호:
3개월 모멘텀
6개월 모멘텀
12개월 모멘텀
200일 이동평균

선정:
상대 모멘텀 상위 3개

비중:
동일비중 또는 변동성 역가중

주기:
월 1회
```

## 완료 기준

- 투자 유니버스가 문서화됨
- 벤치마크가 정의됨
- 리밸런싱 주기가 정의됨
- 목표 MDD와 최대 회전율이 정의됨
- Out-of-sample 기간이 정의됨
- 전략 승격 기준이 정의됨

---

# M7. 거래소 캘린더 및 데이터 계층 확장

## 목표

거래일 처리와 데이터 저장 구조를 신뢰 가능한 수준으로 개선한다.

## 작업

1. 한국·미국 거래소 캘린더 도입
2. 공휴일·조기폐장 처리
3. 데이터 디렉터리 구조 변경
4. DataStore 인터페이스 생성
5. Parquet + DuckDB 조회 계층 추가
6. 데이터 버전 필드 추가
7. 데이터 품질 검사 추가

## 데이터 품질 검사

- 날짜 중복
- 종목 중복
- OHLC 논리 오류
- 음수 거래량
- 비정상 가격 점프
- 장기 가격 정체
- 누락 거래일
- 수정주가 불일치
- 상장폐지 누락
- 미래 데이터 사용

## 완료 기준

특정 날짜를 입력하면 해당 날짜에 실제로 이용 가능했던 데이터만 조회할 수 있다.

---

# M8. 유니버스 및 기업행동 데이터

## 목표

생존자 편향과 수정주가 오류를 방지한다.

## 작업

1. 상장일 저장
2. 상장폐지일 저장
3. 종목코드 변경 이력 저장
4. 지수 편입·편출 이력 저장
5. 액면분할 저장
6. 배당 저장
7. 합병·분할 이벤트 저장
8. 날짜별 유니버스 생성

## 완료 기준

과거 날짜 기준으로 당시 실제 존재했던 종목만 조회된다.

---

# M9. 팩터 엔진

## 목표

여러 종목을 동시에 평가하는 횡단면 팩터 계산 계층을 만든다.

## Factor 인터페이스

```python
class Factor:
    name: str

    def compute(
        self,
        date,
        universe,
        data_store,
    ) -> pd.Series:
        ...
```

## 1차 구현 팩터

### 가격 기반
- 12개월 모멘텀
- 12개월 모멘텀에서 최근 1개월 제외
- 6개월 모멘텀
- 3개월 모멘텀
- 1개월 단기 반전
- 20일 변동성
- 60일 변동성
- 평균 거래대금
- 52주 고점 대비 현재가
- 이동평균 괴리율

### 향후 확장
- PER
- PBR
- EV/EBITDA
- ROE
- ROA
- 영업이익률
- 부채비율
- 매출 성장률
- 영업이익 성장률
- 외국인 순매수
- 기관 순매수

## 팩터 변환

```text
원점수
→ 결측치 처리
→ 극단값 절삭
→ 섹터 중립화
→ Z-score
→ 방향 통일
→ 종합점수
```

## 완료 기준

```text
date
symbol
momentum_3m
momentum_6m
momentum_12m
volatility_20d
liquidity
total_score
```

형태의 데이터가 생성된다.

---

# M10. 연구 및 검증 프레임워크

## 목표

팩터와 전략이 실제로 유효한지 검증하는 체계를 만든다.

## 수익률 Label

- forward_return_5d
- forward_return_20d
- forward_return_60d
- excess_return_vs_benchmark

## 팩터 검증

- Spearman IC
- IC 평균
- IC 표준편차
- IC IR
- 팩터 분위수별 수익률
- 상위 분위-하위 분위 스프레드
- 팩터 회전율
- 시장 국면별 성과
- 시가총액 구간별 성과
- 섹터별 성과

## Walk-forward

```text
학습 구간
→ 검증 구간
→ 테스트 구간
→ 앞으로 이동
→ 반복
```

## 추가 성과지표

- 벤치마크 수익률
- 초과수익
- Alpha
- Beta
- Information Ratio
- Sortino Ratio
- Calmar Ratio
- 변동성
- 월간 승률
- Turnover
- 평균 보유기간
- 거래비용 비중
- 최대 연속 손실
- VaR
- CVaR

## 완료 기준

전략 결과가 다음 구간으로 분리되어 표시된다.

```text
In-sample
Validation
Out-of-sample
Walk-forward aggregate
```

---

# M11. ETF 모멘텀 전략

## 목표

첫 번째 실제 퀀트 전략을 구현한다.

## 전략 흐름

```text
월말 신호 계산
→ ETF별 3·6·12개월 모멘텀 계산
→ 절대 모멘텀 필터
→ 상대 모멘텀 상위 3개
→ 변동성 역가중
→ 월초 리밸런싱
```

## 예시 점수

```text
score =
0.2 × 3개월 수익률
+ 0.3 × 6개월 수익률
+ 0.5 × 12개월 수익률
```

## 필터

- 가격 > 200일 이동평균
- 평균 거래대금 기준 통과
- 최근 변동성 기준 통과
- 최소 상장기간 충족

## 필요한 신규 인터페이스

```python
def generate_targets(
    date,
    universe,
    data_store,
    portfolio,
) -> dict[str, float]:
    ...
```

## 완료 기준

- 벤치마크 비교 가능
- 거래비용 반영
- Walk-forward 검증
- 파라미터 주변값에서도 성과 유지
- 거래비용 2배에서도 전략 논리 유지
- 특정 ETF 하나에 성과가 과도하게 의존하지 않음

---

# M12. 포트폴리오 리밸런싱 엔진

## 목표

단일 종목 매수 주문이 아니라 목표 비중 기반 포트폴리오를 구현한다.

## 처리 흐름

```text
현재 비중 계산
→ 목표 비중 비교
→ 주문 필요 금액 계산
→ 최소 주문금액 필터
→ 회전율 제한
→ 매도 주문
→ 현금 확인
→ 매수 주문
```

## 제약조건

- 종목 최대 비중
- 섹터 최대 비중
- ETF 발행사 최대 비중
- 최소 현금 비중
- 최대 회전율
- 일일 거래대금 대비 주문 한도
- 목표 포트폴리오 변동성
- 유사 ETF 중복 제한

## 비중 방식

1. 동일비중
2. 변동성 역가중
3. Risk Parity
4. 최소분산
5. 최대 Sharpe
6. 목표 변동성

## 완료 기준

목표 비중과 실제 주문 결과를 재현할 수 있고, 주문 전후 포트폴리오 비중 차이가 기록된다.

---

# M13. 체결 모델 현실화

## 목표

백테스트 체결 가정을 실제 시장에 가깝게 만든다.

## 작업

- 거래량 참여율
- 부분 체결
- 지정가 미체결
- 갭 상승·하락
- 상한가·하한가
- 거래정지
- 시장 충격
- 유동성 기반 슬리피지
- 가격 변동성 기반 슬리피지

## 권장 슬리피지 모델

```text
slippage =
base_spread
+ order_value / average_daily_value
+ volatility_component
```

## 완료 기준

고정 bp 슬리피지와 유동성 기반 슬리피지 결과를 비교할 수 있다.

---

# M14. 전략 상태 영속화

## 목표

프로세스 재시작 후에도 전략 상태가 정확히 복구되게 한다.

## 저장 대상

- 보유기간
- 마지막 리밸런싱 날짜
- 마지막 처리 거래일
- 최근 팩터 점수
- 목표 비중
- 주문 생성 사유
- 처리 완료 signal_id
- 전략별 내부 변수
- 데이터 버전
- 전략 버전

## 멱등성 키

```text
signal_id =
strategy_name
+ rebalance_date
+ symbol
+ side
+ target_weight
```

이미 처리된 `signal_id`는 다시 주문하지 않는다.

## 완료 기준

동일 실행을 두 번 호출해도 중복 주문이 발생하지 않는다.

---

# M15. 모의투자 안정화

## 목표

실전 전환 전에 운영 신뢰성을 확보한다.

## 작업

- 거래소 캘린더 반영
- 상태 복구
- 주문 중복 방지
- 포지션 대조
- 체결 대조
- 네트워크 장애 재시도
- 데이터 지연 탐지
- 가격 이상치 탐지
- 로그 구조화
- 오류 알림

## 실전 전환 전 검증 기준

- 20거래일 이상 상태 손상 없음
- 중복 주문 0건
- 포지션 불일치 0건
- 휴장일 주문 0건
- 재시작 후 상태 복구 성공
- 데이터 장애 후 정상 복구
- 백테스트와 모의투자 신호 일치
- 목표 비중 대비 실제 비중 오차 허용범위 이내

---

# M16. RiskManager 리팩터링

## 목표

BacktestBroker, PaperBroker, KISBroker에 공통 적용 가능한 리스크 계층을 만든다.

## 작업

1. RiskManager의 BacktestBroker 직접 의존 제거
2. Broker 인터페이스에 필요한 조회 메서드 정의
3. 포트폴리오 스냅샷 인터페이스 도입
4. 주문 전 리스크 검사
5. 주문 후 포지션 검사
6. 계좌 전체 위험 검사
7. Kill Switch 추가

## 리스크 항목

- 최대 종목 비중
- 최대 포트폴리오 투자비중
- 최대 종목 수
- 최대 일 손실
- 최대 누적 손실
- 최대 주문금액
- 최대 일일 주문횟수
- 최대 회전율
- 최소 현금
- 최대 예상 변동성

## 완료 기준

동일 RiskManager가 모든 Broker 구현에서 동작한다.

---

# M17. KIS 모의투자 브로커 구현

## 목표

한국투자증권 모의투자 API를 연결한다.

## 구현 순서

1. 인증 토큰 발급
2. 토큰 파일 캐시
3. 계좌 잔고 조회
4. 주문 가능 금액 조회
5. 미체결 주문 조회
6. 체결 내역 조회
7. 국내 주식 주문
8. 국내 주식 취소
9. 미국 주식 주문
10. 미국 주식 취소
11. 주문·체결 동기화
12. 로컬 상태와 계좌 상태 대조

## 필수 안전장치

- 기본값은 모의투자
- 실전 전환은 명시적 옵션 필요
- 환경변수 기반 비밀키
- API 응답 원본 로그
- 계좌번호 마스킹
- 중복 주문 차단
- 주문 금액 상한
- 주문 수량 상한
- 긴급 중단
- 주문 전 예상 체결금액 출력

## 완료 기준

KIS 모의투자 계좌와 로컬 상태가 자동으로 일치한다.

---

# M18. 소액 실전 전환

## 목표

검증된 전략만 소액으로 실전 전환한다.

## 조건

다음 조건을 모두 만족해야 한다.

- Out-of-sample 성과 기준 충족
- Walk-forward 결과 기준 충족
- 모의투자 20거래일 이상 정상 운영
- 포지션 대조 오류 0건
- 중복 주문 0건
- 휴장일 주문 0건
- 긴급 중단 기능 검증
- 최대 주문 한도 검증
- API 오류 대응 검증

## 실전 단계

```text
Dry-run
→ 1주 또는 최소 수량
→ 소액 포트폴리오
→ 제한된 ETF 유니버스
→ 검증 후 단계적 확대
```

---

# M19. 실험 추적 및 운영 모니터링

## 목표

모든 전략 결과와 운영 상태를 추적 가능하게 만든다.

## 실험 메타데이터

```text
experiment_id
git_commit
data_version
strategy_name
strategy_version
parameters
universe
benchmark
backtest_period
validation_period
test_period
cost_assumption
metrics
created_at
```

## 운영 화면

### 시장 상태
- 주요 지수
- 금리
- 환율
- VIX
- 시장 변동성

### 전략 상태
- 전략명
- 최근 신호
- 다음 리밸런싱
- 현재 팩터 점수
- 전략 중단 조건

### 포트폴리오
- 현재 비중
- 목표 비중
- 주문 예정
- 현금 비중
- 위험 기여도

### 운영 상태
- 마지막 데이터 수집
- 마지막 백테스트
- 마지막 주문
- 체결 상태
- API 상태
- 오류 상태

---

# 9. Agentic AI 구현 규칙

## 9.1 작업 단위

각 변경은 하나의 명확한 목적을 가져야 한다.

예:

```text
좋음:
Add exchange calendar abstraction and KR/US holiday tests

나쁨:
Refactor data, strategy, broker, GUI, and reports
```

## 9.2 작업 순서

각 Milestone은 다음 순서로 수행한다.

```text
1. 현재 코드 확인
2. 설계 문서 작성
3. 테스트 작성
4. 최소 구현
5. 전체 테스트 실행
6. 회귀 확인
7. 문서 갱신
8. 커밋
```

## 9.3 변경 제한

- 기존 CLI 명령을 불필요하게 변경하지 않는다.
- 기존 GUI 기능을 유지한다.
- 기존 테스트를 삭제하지 않는다.
- 회귀 테스트의 고정값은 이유 없이 변경하지 않는다.
- 비밀정보를 코드에 넣지 않는다.
- 실전 주문 기능을 기본 활성화하지 않는다.
- 데이터 수집과 전략 계산을 한 함수에 섞지 않는다.
- 네트워크 요청을 백테스트 루프 안에서 실행하지 않는다.
- 데이터 수집 실패를 조용히 무시하지 않는다.

## 9.4 테스트 규칙

각 신규 기능은 최소 다음 테스트를 포함한다.

- 정상 흐름
- 결측 데이터
- 빈 데이터
- 잘못된 시장
- 잘못된 종목
- 중복 실행
- 날짜 경계
- 휴장일
- 재시작
- 데이터 누수
- 네트워크 실패
- 비정상 API 응답

## 9.5 백테스트 금지사항

- 미래 데이터 사용 금지
- 전체 기간 평균값 사용 금지
- 상장폐지 종목 제외 금지
- 수정주가와 원주가 혼용 금지
- 종가 신호를 동일 종가 체결로 처리 금지
- 테스트 기간을 이용한 파라미터 선택 금지
- 거래비용 미반영 금지

---

# 10. 우선순위

## Priority 0: 즉시 해결

1. 거래소 캘린더
2. 전략 상태 영속화
3. 중복 주문 방지
4. 데이터 품질 검사
5. RiskManager 추상화

## Priority 1: 퀀트 연구 핵심

1. Point-in-Time 데이터 구조
2. Factor 인터페이스
3. 모멘텀 팩터
4. 변동성 팩터
5. 유동성 팩터
6. IC 분석
7. 분위수 분석
8. Walk-forward
9. 벤치마크 비교

## Priority 2: 첫 실제 전략

1. ETF 유니버스
2. ETF 모멘텀
3. 목표 비중
4. 월간 리밸런싱
5. 변동성 역가중
6. 회전율 제한

## Priority 3: 운영 안정화

1. 모의투자 대조
2. 오류 알림
3. 운영 로그
4. 실험 추적
5. KIS 모의투자

## Priority 4: 확장

1. 재무 데이터
2. 가치 팩터
3. 퀄리티 팩터
4. 성장 팩터
5. 수급 팩터
6. 멀티팩터 전략
7. 머신러닝 랭킹

---

# 11. 권장 GitHub Issue 목록

1. Define quant research objectives and benchmark
2. Add KR and US exchange calendars
3. Design point-in-time data schema
4. Add DataStore abstraction
5. Add data quality validation suite
6. Add corporate-action model
7. Add historical universe membership
8. Create Factor interface and registry
9. Implement price momentum factors
10. Implement volatility and liquidity factors
11. Add forward-return labels
12. Add IC analysis
13. Add quantile-return analysis
14. Add walk-forward validation
15. Add benchmark-relative metrics
16. Implement ETF momentum strategy
17. Add target-weight portfolio interface
18. Implement rebalancing engine
19. Add turnover constraints
20. Add strategy-state persistence
21. Add idempotent signal execution
22. Refactor RiskManager to Broker interface
23. Add paper-account reconciliation
24. Add structured operations logging
25. Implement KIS authentication
26. Implement KIS balance inquiry
27. Implement KIS paper orders
28. Add KIS fill reconciliation
29. Add experiment tracking
30. Add quant dashboard

---

# 12. Definition of Done

각 Milestone은 다음 조건을 모두 충족해야 완료로 간주한다.

- 코드 구현 완료
- 단위 테스트 추가
- 통합 테스트 추가
- 전체 테스트 통과
- README 또는 관련 문서 갱신
- 설정 예시 추가
- 오류 처리 추가
- 로그 추가
- 데이터 누수 검토
- 회귀 테스트 확인
- 재현 가능한 실행 명령 제공
- 실전 주문 기본 비활성화 확인

---

# 13. 첫 실행 작업

Agentic AI는 다음 순서로 작업을 시작한다.

## Step 1
현재 저장소 전체 구조와 테스트를 확인한다.

## Step 2
`docs/quant_research_spec.md`를 생성한다.

## Step 3
한국·미국 거래소 캘린더 추상화를 구현한다.

## Step 4
기존 TradingSessionClock을 거래소 캘린더와 연결한다.

## Step 5
휴장일 및 조기폐장 테스트를 추가한다.

## Step 6
전략 상태 영속화 인터페이스를 설계한다.

## Step 7
RSI 전략의 `holding_days`를 상태 저장 대상으로 전환한다.

## Step 8
중복 실행 방지를 위한 `signal_id`를 추가한다.

## Step 9
Factor 인터페이스와 registry를 추가한다.

## Step 10
3개월·6개월·12개월 모멘텀 팩터를 구현한다.

---

# 14. 최종 개발 방향

이 프로젝트는 기존 엔진을 폐기하고 다시 만드는 것이 아니라 다음 방향으로 확장한다.

```text
기존:
가격 데이터
+ 기술적 전략
+ 백테스트
+ 모의투자

확장:
Point-in-Time 데이터
+ 팩터 엔진
+ 연구 검증
+ 포트폴리오 구성
+ 실험 추적
+ 브로커 연동
```

가장 중요한 우선순위는 다음과 같다.

```text
실전 주문
보다
데이터 신뢰성
→ 팩터 유효성
→ 포트폴리오 검증
→ 모의투자 안정성
```

Agentic AI는 이 원칙을 위반하는 대규모 기능 확장이나 성급한 실전 주문 연동을 피해야 한다.
