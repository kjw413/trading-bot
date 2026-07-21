# 가치투자 프레임워크 봇 적용 설계

- 날짜: 2026-07-21
- 상태: 브레인스토밍 완료 · 사용자 스코프 승인
- 원천 문서: [`docs/valuation-framework.md`](../../valuation-framework.md)
- 관련 문서: [`docs/architecture.md`](../../architecture.md),
  [`docs/quant_research_spec.md`](../../quant_research_spec.md),
  [`docs/superpowers/specs/2026-07-19-kr-theme-multifactor-design.md`](2026-07-19-kr-theme-multifactor-design.md)

## 1. 목적과 배경

`docs/valuation-framework.md`의 기업가치 기반 가치투자 방법론을 현재 봇에
구현 가능한 레이어로 옮긴다. 현재 봇은 **기술적·모멘텀(가격 기반)** 시스템이고,
이 프레임워크는 **펀더멘털 가치평가(기업가치 기반)** 시스템이다. 두 철학은
다르지만, 프레임워크 문서 원칙 #5("가치평가 ↔ 실행 분리")에 따라 **가치평가를
별도 레이어로 얹고 기존 이벤트 엔진을 실행 레이어로 재사용**하면 충돌 없이
공존한다.

세 가지 기존 자산이 그대로 맞물린다.

1. **실행 분리**: 기존 `engine/`·`broker/`가 곧 실행 레이어. 가치평가는 신호만 낸다.
2. **검증 재사용**: 기존 `research/`(IC·분위수·Walk-forward·게이트)가 가치 파생
   신호(역산 성장률·IRR)도 팩터와 동일하게 검증한다.
3. **룩어헤드 규율 정합**: 기존 PIT 스토어와 "종가 신호 → 익일 시가 체결" 흐름이
   프레임워크 백테스트 요건 #1(재무 데이터 공시일 정렬)과 이미 일치한다.

## 2. 브레인스토밍에서 확정된 요구사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 이번 스코프 | (1) 순수 가치평가·의사결정 코어 + (2) DART 재무 수집 레이어 | 사용자 승인 |
| `r_required` 산정 | 종목별 CAPM식 분해: `r_riskfree + equity_risk_premium + firm_specific_premium` | 문서 §2, 위험 차등 반영 |
| 투자기간 `T` | 기본 3년(파라미터). 문서 예시 준수, config로 조정 가능 | 문서 §10 |
| DART 네트워크 | transport 주입형 클라이언트. **테스트는 전부 fixture 목**, 키는 env(`DART_API_KEY`)만 | 저장소 테스트 전략(§11), 키 커밋 금지 |

## 3. 다섯 가지 판단 규칙 (구현 불변식)

프레임워크 문서 §1의 규칙을 코드 계약으로 못박는다. 이 규칙을 어기는 API는
만들지 않는다.

1. **가격 예측 금지.** 가치를 계산하고 현재가와 비교한다.
2. **매수단가는 입력이 아니다.** 의사결정 함수 입력은 현재가뿐. 평단가·수익률·
   본전 개념은 어떤 결정 로직에도 들어가지 않는다.
3. **적정가는 3-튜플이다.** 스칼라를 반환하는 valuation 함수는 만들지 않는다.
   `ScenarioValues(conservative, base, optimistic)`를 반환한다.
4. **신호는 IRR 기준.** 상승률·하락률 트리거를 쓰지 않는다. "현재가에서 남은
   연환산 기대수익률 ≥ 요구수익률"으로 낸다.
5. **수급·차트는 종목 선택이 아니라 집행 타이밍에만.** 가치 레이어와 실행 레이어
   분리. 이번 스코프는 가치 레이어만 다룬다(집행은 기존 엔진).

## 4. 하드 제약 (assertion으로 강제)

문서 §2 "구현 시 반드시 지킬 것"을 실행 가능한 검증으로 옮긴다.

- **말기가치 g 클램프.** `g_terminal`은 장기 명목 경제성장률(기본 상한 3%)로
  하드 클램프. `WACC - g_terminal <= 0`이면 예외.
- **통화 일치.** 현금흐름 통화와 할인율 통화가 다르면 예외. 환율 손익은 가치평가
  레이어 밖(포트폴리오)에서 관리 — 이번 스코프 밖.
- **희석 반영.** 주당가치는 `DilutedShares`로만 나눈다. 기본주식수 입력 경로를
  두지 않는다.
- **성장-재투자 일관성.** `g ≈ reinvestment_rate × ROIC`. 허용 오차를 벗어나면
  경고를 반환(하드 실패가 아니라 리포트 가능한 `ConsistencyCheck`)한다. 이유:
  실무에서 정확히 일치하지 않으므로, 봇은 모순을 **차단**이 아니라 **표시**한다.

## 5. 아키텍처

기존 구조를 유지하고 두 개의 신규 영역을 추가한다.

```text
src/tradingbot/
├─ valuation/                 [신규] 순수 가치평가·의사결정 코어 (네트워크 0)
│  ├─ returns.py    요구수익률(CAPM 분해), IRR, MaxBuyPrice
│  ├─ dcf.py        FCFF DCF 엔진 + 말기가치 + g 클램프 + 일관성 체크
│  ├─ scenario.py   보수/기준/낙관 3-튜플 가치 산출
│  ├─ reverse.py    역산 DCF (이분법 근찾기) → 내포 성장률
│  └─ decision.py   4구간 신호 + 3조건 추가매수 게이트
└─ data/
   └─ fundamentals.py         [신규] DART 재무 수집 + PIT 정규화
```

저장 레이아웃(문서 §9, kr-theme spec §4.1 준수):

```text
data/
├─ raw/fundamentals/KR/{corp_code}/{rcept_no}.json   # DART 원본 (재현용)
└─ processed/fundamentals_pit/KR/{corp_code}.parquet  # 정규화 + available_at
```

### 5.1 핵심 타입

```text
# valuation/returns.py
RequiredReturn(risk_free, equity_risk_premium, firm_specific_premium)
  .rate() -> float                       # 세 항의 합
irr(p0, p_t, dividends, years) -> float  # 연환산
max_buy_price(p_t, dividends, r_required, years) -> float

# valuation/dcf.py
DcfInputs(fcff_0, growth, wacc, g_terminal, years, net_debt,
          minority_interest, non_operating_assets, diluted_shares,
          reinvestment_rate, roic, currency)
DcfResult(enterprise_value, equity_value, value_per_share,
          terminal_value_share, consistency: ConsistencyCheck)
dcf_value(inputs) -> DcfResult

# valuation/scenario.py
ScenarioValues(conservative, base, optimistic)   # V_low <= V_base <= V_high
scenario_values(conservative_inputs, base_inputs, optimistic_inputs)
  -> ScenarioValues                              # 각 시나리오 DCF value_per_share

# valuation/reverse.py
implied_growth(current_price, inputs_without_growth, bracket) -> float

# valuation/decision.py
Signal = ACCUMULATE | PARTIAL | HOLD_OR_TRIM | EXIT
decide(current_price, scenario_max_buy: ScenarioValues) -> Decision
accumulate_gate(price_ok, thesis_intact, survival_ok) -> GateResult  # 3조건 AND
```

### 5.2 기업 유형별 모델 라우팅

문서 §4 표대로 `company_type → primary_model`을 라우팅한다. 이번 스코프에서는
**FCFF DCF만 구현**하고, 라우팅 스켈레톤(enum + 미구현 모델은 명시적
`NotImplementedError`)을 둔다. RIM/NAV/SOTP/rNPV는 후속 스코프.

## 6. DART 재무 수집 (Phase 2)

- **transport 주입.** `DartClient(api_key, transport)`; `transport(url, params)
  -> dict`. 실제 구현은 `requests`, 테스트는 fixture를 반환하는 가짜 transport.
  네트워크 없이 전 로직 검증.
- **PIT 분리.** 각 레코드에 `report_period`(보고서 기준일)와
  `announcement_date`(= DART `rcept_dt`)를 분리 저장. `available_at` =
  공시일 다음 거래일 개장 전. 조회는 `available_at <= as_of` 필터를 강제한다.
- **매핑.** DART 계정(매출·영업이익·자산·부채·감가상각 등) → FCFF 구성요소.
  파싱 불가·결측 계정은 조용히 0으로 채우지 않고 명시적 결측으로 표시.
- **키 관리.** `DART_API_KEY` 환경변수. 없으면 수집 명령은 명확한 오류로
  안내(테스트·코어는 키 불필요).

## 7. 검증·백테스트 정합 (기존 재사용)

- 역산 DCF 내포 성장률·시나리오 IRR은 `factors/` 인터페이스로 감싸 기존
  `research report`(IC·분위수·Walk-forward·게이트)로 유효성을 검증할 수 있다.
  (이번 스코프에서는 코어와 데이터까지. 팩터 래핑은 후속.)
- 백테스트 룩어헤드 요건 #1(공시일 정렬)은 PIT `available_at`으로 충족.
  요건 #2(생존편향)·#3(거래 마찰)은 기존 백테스트/수수료 모델과 델리스팅
  이력 확보에서 다룬다 — 후속 스코프.

## 8. 테스트 전략

| 계층 | 테스트 |
|---|---|
| returns | CAPM 합, IRR 왕복(round-trip), MaxBuyPrice가 정확히 r_required를 만족 |
| dcf | 손계산 대비 EV·주당가치, g 클램프 예외, 통화 불일치 예외, 일관성 체크 |
| scenario | 단조성(V_low ≤ V_base ≤ V_high), 3-튜플 반환 |
| reverse | 알려진 성장률로 만든 가격을 역산해 그 성장률 복원(근찾기 수렴) |
| decision | 4구간 경계값, 3조건 게이트(AND), 매수단가 미사용 계약 |
| fundamentals | 가짜 transport로 파싱·정규화, PIT 위반 시 예외, rcept_dt→available_at |

- 모든 순수 모듈은 네트워크 0. DART는 fixture 목만.
- 기존 테스트 삭제·수정 금지. 파일 쓰기는 `encoding="utf-8"`.

## 9. 범위 제외 (Out of Scope)

- 자동 주문 집행 / KIS 연동 (신호·리포트까지만)
- 뉴스 LLM 3분류 필터 (문서 §7)
- RIM/NAV/SOTP/rNPV 모델 구현 (라우팅 스켈레톤만)
- 대상 유니버스 확정·포지션 사이징 규칙 (문서 §10 미결)
- 미국 EDGAR 수집 (DART=한국 우선, 이후 확장)
- 종목별 상태 스키마(§11) 영속화 전체 — 코어가 소비하는 입력 타입까지만

## 10. 여전히 미결 (후속 세션에서 결정)

문서 §10 중 이번 스코프가 건드리지 않는 항목:

- 대상 유니버스(국내만? 미국 포함? 시총 하한?)
- 재평가 주기(분기 실적 기준? 고정 스케줄?)
- 포지션 사이징(가치 범위 폭 반비례?)
- 실행 방식(신호 알림? 자동 주문?)
- 페이퍼 트레이딩 검증 기간

## 면책

가치평가 개념을 코드로 옮기기 위한 기술 명세이며 투자 자문이 아니다.
백테스트 성과가 실전 성과를 보장하지 않는다.
