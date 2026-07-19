# KR 테마 멀티팩터 전략 + 자동 데이터 파이프라인 설계

- 날짜: 2026-07-19
- 상태: 사용자 승인 완료 (섹션별 승인)
- 관련 문서: [`trading_bot_agentic_ai_execution_plan_260714.md`](../../../trading_bot_agentic_ai_execution_plan_260714.md),
  [`docs/quant_research_spec.md`](../../quant_research_spec.md),
  [`docs/architecture.md`](../../architecture.md)

## 1. 목적

특정 테마에 속한 특정 종목들을 대상으로, 데이터 기반 매수·매도로 안정적 수익을
목표로 하는 룰 기반 멀티팩터 전략과, 이를 뒷받침하는 자동 데이터 수집·전처리
시스템을 구축한다. 기존 실행 계획(M6~M19)의 2단계(개별 종목 멀티팩터)를 테마
중심으로 구체화하는 작업이며, 기존 로드맵·승격 기준·검증 체계에 통합된다.

## 2. 확정 요구사항

| 항목 | 결정 |
|---|---|
| 로드맵 관계 | 기존 실행 계획에 통합 (2단계 구체화, ETF 모멘텀 트랙 유지) |
| 대상 시장 | 한국(KOSPI/KOSDAQ) 우선 |
| 테마 정의 | config 수동 정의 + 날짜 기반 버전 관리 |
| 매매 주기 | 주간~월간 리밸런싱, 종가 신호 → 익일 시가 체결 |
| 데이터 범위 | 수급(외국인·기관·개인) + 재무·공시(DART) + 시장·거시. 뉴스·검색 트렌드 제외 |
| 알고리즘 | 룰 기반 멀티팩터 우선. 데이터셋은 ML 학습 가능 형태로 설계 |
| 자동화 | Windows 작업 스케줄러 일일 배치 |
| 접근 순서 | 검증 인프라 우선 (접근법 A) |

접근법 A 선택 근거: pykrx 수급·DART 공시는 과거분 소급 수집이 가능하므로
데이터를 미리 쌓아둘 필요가 없고, 검증 프레임워크 없이는 새 팩터의 유효성을
판단할 수 없다. 기존 계획의 원칙(데이터 신뢰성·검증 우선)과도 일치한다.

## 3. 전체 아키텍처: 4개 Phase

기존 이벤트 엔진·백테스트·모의투자 구조는 유지하고 그 위에 확장한다.

```text
Phase 1  연구·검증 프레임워크          (M10)
         src/tradingbot/research/
         forward return 라벨, IC 분석, 분위수 분석, Walk-forward, 실험 기록
         → 완료 기준: 기존 모멘텀 팩터에 대해 IC/분위수/Walk-forward
           리포트가 생성된다

Phase 2  데이터 파이프라인 확장        (M7 잔여 + 신규)
         data/flows.py, macro.py, fundamentals.py, quality.py
         CLI `data pipeline` + 작업 스케줄러 일일 배치
         → 완료 기준: 매일 자동으로 수집→검사→전처리→로그가 수행된다

Phase 3  테마 유니버스 + 팩터 확장     (M8 일부 + M9 확장)
         config/themes.toml, data/universe.py, factors/flow.py, value.py
         → 완료 기준: 신규 팩터가 Phase 1 게이트(IC·분위수)를 통과해
           전략 후보로 선별된다

Phase 4  테마 멀티팩터 전략 + 리밸런싱 (M11 변형 + M12)
         portfolio/, engine/rebalance.py, strategies/theme_multifactor.py
         → 완료 기준: In-sample/Validation/OOS 분리 백테스트와
           Walk-forward 결과가 승격 기준으로 평가된다
```

설계 원칙:

1. **기존 재사용**: 승격 기준, 실험 기간 구분, 거래비용 모델, 룩어헤드 방지
   흐름(종가 신호 → 익일 시가 체결)은 `quant_research_spec.md`를 그대로 따른다.
   ETF 모멘텀(M11)도 같은 인프라 위에서 구현 가능하다.
2. **모든 신규 데이터는 Point-in-Time**: 레코드마다 `available_at`을 부여하고,
   백테스트는 그 시점 이전 데이터를 볼 수 없다. 재무는 보고서 기준일이 아닌
   공시일 기준으로만 사용한다.
3. **ML 준비형 데이터셋**: Phase 3 산출물은 `date × symbol × 팩터 행렬 +
   forward return 라벨` 테이블로 저장해 3단계(ML 랭킹)의 학습 데이터가 된다.

## 4. 데이터 파이프라인 (Phase 2)

### 4.1 저장 구조

```text
data/
├─ raw/                          # 소스 원본 그대로 (재현용)
│  ├─ flows/KR/{symbol}.parquet
│  ├─ macro/{series}.parquet
│  └─ fundamentals/KR/{corp}/{rcept}.json
├─ processed/                    # 정규화 + PIT 필드 부여
│  ├─ flows/KR/*.parquet
│  ├─ macro/*.parquet
│  └─ fundamentals_pit/KR/*.parquet
├─ features/daily/*.parquet      # date × symbol 팩터 행렬 + 라벨
├─ experiments/                  # 실험 기록 JSON
└─ cache/                        # 기존 가격 캐시 (변경 없음)
```

### 4.2 공통 스키마

모든 processed 레코드는 `date, symbol, source, available_at, ingested_at,
data_version` 필드를 갖는다. `DataStore` 조회는 항상
`available_at <= 조회시점` 필터를 강제한다.

### 4.3 소스별 수집 규칙

| 소스 | 모듈 | 내용 | available_at |
|---|---|---|---|
| 수급 | `data/flows.py` | pykrx 일별 투자자별 순매수. 소급 + 증분 수집 | T일 데이터는 T+1 개장 전 |
| 거시 | `data/macro.py` | KOSPI·KOSDAQ 지수, USD/KRW, 국고채 금리, VIX (FinanceDataReader/yfinance) | T일 데이터는 T+1 개장 전 |
| 재무 | `data/fundamentals.py` | DART OpenAPI 분기 재무제표. `report_period`(보고서 기준일)와 `announcement_date`(공시 접수일) 분리 저장. PER/PBR 등 파생 지표는 processed 단계에서 가격·주식수와 결합해 계산 | 공시일 다음 거래일 개장 전 |

- DART API 키는 환경변수로 주입하고 저장소에 커밋하지 않는다.
- 증분 수집은 기존 가격 캐시와 동일한 패턴(마지막 날짜 + 1일부터)을 따른다.

### 4.4 품질 검사 (`data/quality.py`)

검사 항목: 날짜·종목 중복, OHLC 논리 오류, 음수 거래량, 비정상 가격 점프,
누락 거래일(거래소 캘린더 대조), 수급 합계 이상치.

결과는 통과/경고/실패 3단계 리포트로 남긴다. 실패 데이터는 조용히 버리지 않고
격리 디렉터리로 옮긴 뒤 로그에 명시한다.

### 4.5 일일 배치 (`tradingbot data pipeline --market KR`)

```text
가격 증분 → 수급 증분 → 거시 증분 → [주 1회, 월요일 실행분] 재무 갱신
→ 품질 검사 → features 재계산 → 실행 결과 JSON 로그 (state/pipeline_log/)
```

- 작업 스케줄러가 장 마감 후 평일 19:00(기본값, 설정으로 변경 가능)에 1회
  실행한다.
- 소스 하나가 실패해도 나머지는 계속 진행하되 실패를 결과 로그와 콘솔에
  명시한다. 다음 실행에서 증분 구조가 자동 보충한다.
- 네트워크 오류는 지수 백오프로 소스당 최대 3회 재시도한다.
- DuckDB 조회 계층은 이번 범위에서 제외한다. 현재 데이터 규모는
  pandas + Parquet로 충분하며, 필요 시 저장 포맷 변경 없이 추가할 수 있다.

## 5. 검증 프레임워크 (Phase 1)

`src/tradingbot/research/` 모듈:

| 모듈 | 역할 |
|---|---|
| `labels.py` | forward return 5d/20d/60d, 벤치마크 대비 초과수익 라벨 |
| `ic.py` | Spearman IC, IC 평균·표준편차·IR, 시계열 추이 |
| `quantiles.py` | 분위수별 수익률, 상위-하위 스프레드, 팩터 회전율 |
| `walk_forward.py` | 학습 3년 → 검증 1년, 1년 전진 (spec 규칙 그대로) |
| `experiment.py` | git commit, data_version, 파라미터, 지표를 `data/experiments/*.json`에 기록 |

**팩터 채택 게이트**: 새 팩터는 IC IR과 분위수 단조성 기준을 통과해야 전략에
편입된다. 임계값은 `config/research.toml`에 정의하고 In-sample 구간에서만
조정한다.

## 6. 테마 유니버스 (Phase 3)

`config/themes.toml` 스키마:

```toml
[themes.ai_semiconductor]
name = "AI 반도체"
members = [
  { symbol = "000660", from = "2023-01-01" },
  { symbol = "042700", from = "2023-06-01", to = "2025-03-01" },  # 편출 이력 보존
]
```

- `data/universe.py`의 `members(theme, date)`가 해당 날짜에 유효한 멤버만
  반환한다. 편입 전·편출 후 종목은 제외된다.
- **정직성 규칙**: 과거 편입일을 현재 지식으로 소급 작성하면 생존자 편향이
  생긴다. 이 한계는 수동 정의 방식에서 제거할 수 없으므로 문서화하고,
  편입·편출 변경은 반드시 날짜와 사유를 함께 커밋한다.

## 7. 팩터 확장 (Phase 3)

- `factors/flow.py`: 외국인 순매수 강도(5/20/60일 누적 순매수 ÷ 거래대금),
  기관 순매수 강도, 수급 지속일수
- `factors/value.py`: PER·PBR 역수 (`available_at` 준수)
- 기존 `momentum.py` 유지 + 변동성·유동성 팩터 보강
- `factors/transform.py`: 결측 처리 → 극단값 절삭(winsorize) → Z-score →
  방향 통일 → 가중 결합. 테마 유니버스는 종목 수가 적으므로(테마당 10~30개)
  섹터 중립화는 생략한다.
- **거시 데이터는 종목 팩터가 아니라 국면(regime) 필터**로 사용한다:
  KOSPI 200일 이동평균 상/하 등으로 상승장/하락장을 구분해 주식 노출 비중을
  조절하고, 국면별 성과를 분리해 리포트한다.

## 8. 벤치마크 (테마 전략)

| 용도 | 벤치마크 | 판단 기준 |
|---|---|---|
| 기본 | KOSPI 매수 후 보유 | 시장을 이기는가 |
| 보조 (더 중요) | 테마 유니버스 동일비중 매수 후 보유 | 테마 안에서 종목 선별·타이밍이 실제로 가치를 더하는가 |

보조 벤치마크를 이기지 못하면 테마 전체를 사는 것과 다를 게 없으므로,
보조 벤치마크 초과가 전략 존재 이유를 검증하는 핵심 기준이다.

## 9. 전략과 포트폴리오 (Phase 4)

### 9.1 포트폴리오 계층 (`src/tradingbot/portfolio/`)

```text
종합점수 (팩터 결합)
→ ranking.py    : 테마 유니버스 내 상위 N종목 선정
→ weights.py    : 동일비중 / 변동성 역가중 (research.toml 선택)
→ constraints.py: 종목 최대 비중, 최소 현금, 회전율 제한
→ rebalance.py  : 현재 비중 비교 → 최소 주문금액 필터
                  → 매도 주문 먼저 → 현금 확인 → 매수 주문
```

### 9.2 전략 (`strategies/theme_multifactor.py`)

- `generate_targets(date, universe, data_store, portfolio) → dict[symbol, weight]`
  인터페이스를 구현하고, 기존 Strategy 어댑터로 감싸 백테스트·모의투자 엔진을
  그대로 사용한다.
- 리밸런싱 주기(주간/월간)는 `research.toml` 파라미터로 정의한다.
- 신호는 리밸런싱일 종가 기준으로 계산하고 다음 거래일 시가에 체결한다.
- 국면 필터가 하락장으로 판단하면 주식 노출을 축소하고 부족분은 현금으로
  보유한다.
- 기존 `signal_id` 멱등성 원장과 전략 상태 영속화를 그대로 사용한다.

## 10. 오류 처리

- **데이터 신선도 게이트**: 전략 실행 시 features 최신 날짜가 직전 거래일보다
  오래되면 리밸런싱을 건너뛰고 경고한다. 오래된 데이터로 주문하지 않는다.
- 파이프라인은 소스별 독립 실패 + 격리 + 재시도 (4.5절).
- PIT 위반 접근(available_at 이후 데이터 요청)은 예외를 발생시킨다.
- 승격·강등 기준은 `quant_research_spec.md` 6장을 재사용하되, 테마 전략의
  보조 벤치마크(테마 동일비중)를 추가한다.

## 11. 테스트 전략

| 계층 | 테스트 |
|---|---|
| 데이터 | 수집기 네트워크 mock, 증분 병합, 품질 검사 규칙별, PIT 위반 시 예외 |
| 팩터·검증 | 고정 fixture 대비 팩터 값, IC/분위수 계산, 라벨-팩터 날짜 정렬(데이터 누수) 검증 |
| 유니버스 | 날짜별 멤버 조회, 편입 전·편출 후 제외 |
| 포트폴리오 | 목표 비중→주문 변환, 제약 위반 거부, 중복 실행 멱등성 |
| 통합 | 소형 fixture 엔드투엔드 백테스트 고정값 회귀 (기존 회귀 테스트 유지) |

## 12. 운영

- 1차 인터페이스는 CLI 중심: `data pipeline`, `backtest`, `research ic` 등.
- 작업 스케줄러 등록용 bat 파일을 제공한다
  (bat 파일 규칙: REM 뒤 한글 금지, 한글 echo는 chcp 65001 이후에만).
- GUI 통합은 전략이 승격 기준을 통과한 뒤로 미룬다.

## 13. 범위 제외 (Out of Scope)

- 뉴스·검색 트렌드 데이터
- DuckDB 조회 계층
- 미국 개별 종목 (한국 완성 후 확장)
- ML 랭킹 구현 (데이터셋 준비까지만)
- GUI 통합
- 상주 데몬/서비스형 수집기
