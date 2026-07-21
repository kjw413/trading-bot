# 가치투자 프레임워크 구현 계획 (코어 + DART 수집)

> **For agentic workers:** superpowers TDD로 태스크 단위 진행. 각 태스크는
> 실패 테스트 작성 → 실패 확인 → 구현 → 통과 확인 → 커밋 순서를 지킨다.

**스펙:** [`docs/superpowers/specs/2026-07-21-value-investing-framework-design.md`](../specs/2026-07-21-value-investing-framework-design.md)

**Goal:** (Phase 1) 순수 가치평가·의사결정 코어 `src/tradingbot/valuation/`를
TDD로 쌓고, (Phase 2) DART 재무 수집·PIT 정규화 레이어 `data/fundamentals.py`를
네트워크 목 기반으로 구현한다.

**Tech Stack:** Python 3.13, 표준 라이브러리 + 기존 의존성(pandas, requests).
신규 외부 의존성 없음.

## Global Constraints

- 신규 의존성 추가 금지.
- 순수 valuation 모듈은 네트워크·파일 접근 0. DART는 transport 주입으로
  테스트에서 네트워크 없이 검증.
- 다섯 판단 규칙(스펙 §3)과 하드 제약(스펙 §4)을 계약으로 지킨다. 특히:
  매수단가를 의사결정 입력에 두지 않는다; valuation은 3-튜플을 반환한다;
  주당가치는 `diluted_shares`로만 나눈다; `g_terminal`은 하드 클램프.
- 기존 테스트 삭제·수정 금지. 파일 쓰기는 `encoding="utf-8"`.
- DART API 키는 `DART_API_KEY` env로만. 절대 커밋 금지.
- 커밋 접두사: 중간 작업 `VF(part):`, 페이즈 마지막 `VF:`.
- 테스트 실행(이 환경, 저장소 루트): `.venv/bin/python -m pytest <경로> -v`

---

## Phase 1 — 가치평가·의사결정 코어

### Task 1: 요구수익률 · IRR · MaxBuyPrice (`valuation/returns.py`)

**Files:** Create `src/tradingbot/valuation/__init__.py`, `valuation/returns.py`;
Test `tests/test_valuation_returns.py`

**Interfaces (Produces):**
- `RequiredReturn(risk_free, equity_risk_premium, firm_specific_premium)` frozen
  dataclass; `.rate() -> float` (세 항의 합, 각 항 음수 방지 검증).
- `irr(p0, p_t, dividends, years) -> float` — `((p_t + dividends)/p0)**(1/years) - 1`.
  `p0 <= 0` 또는 `years <= 0`이면 예외.
- `max_buy_price(p_t, dividends, r_required, years) -> float` —
  `(p_t + dividends) / (1 + r_required)**years`.

**TDD:** IRR/MaxBuyPrice 왕복 검증(어떤 가격을 max_buy로 잡으면 그 가격의
IRR이 정확히 r_required가 된다), 경계·예외.

### Task 2: FCFF DCF 엔진 (`valuation/dcf.py`)

**Files:** Create `valuation/dcf.py`; Test `tests/test_valuation_dcf.py`

**Interfaces (Produces):**
- `ConsistencyCheck(implied_g, expected_g, within_tolerance: bool)`
- `DcfInputs`(frozen): `fcff_0, growth, wacc, g_terminal, years, net_debt,
  minority_interest, non_operating_assets, diluted_shares, reinvestment_rate,
  roic, currency="KRW", g_terminal_cap=0.03, consistency_tol=0.01`
- `DcfResult`(frozen): `enterprise_value, equity_value, value_per_share,
  terminal_value_share, consistency`
- `dcf_value(inputs) -> DcfResult`

**규칙:** 명시적 예측기간 FCFF는 `fcff_0*(1+growth)^t`; 말기가치는
`FCFF_{N+1}/(WACC - g_terminal)`; `g_terminal`은 `min(g_terminal, cap)`으로
클램프; 클램프 후에도 `wacc - g_terminal <= 0`이면 `ValueError`.
`diluted_shares <= 0`이면 예외. `consistency`는 `g ≈ reinvestment_rate*roic`
비교(하드 실패 아님).

**TDD:** 손계산 대비 EV/주당가치, g 클램프 발동, `wacc<=g` 예외,
`terminal_value_share`가 EV의 60~80% 재현되는 케이스, 일관성 플래그.

### Task 3: 시나리오 3-튜플 (`valuation/scenario.py`)

**Files:** Create `valuation/scenario.py`; Test `tests/test_valuation_scenario.py`

**Interfaces (Produces):**
- `ScenarioValues(conservative, base, optimistic)` frozen; 생성 시
  `conservative <= base <= optimistic` 검증(위반 시 예외 — 시나리오 가정이
  뒤집힌 것).
- `scenario_values(conservative_inputs, base_inputs, optimistic_inputs)
  -> ScenarioValues` — 각 `DcfInputs`에 `dcf_value`를 돌려 `value_per_share`로
  3-튜플 구성.

**TDD:** 단조성, 뒤집힌 입력 예외, 각 원소가 해당 시나리오 DCF와 일치.

### Task 4: 역산 DCF (`valuation/reverse.py`)

**Files:** Create `valuation/reverse.py`; Test `tests/test_valuation_reverse.py`

**Interfaces (Produces):**
- `implied_growth(current_price, base_inputs, bracket=(-0.5, 0.5), tol=1e-6,
  max_iter=200) -> float` — `base_inputs.growth`만 바꿔가며
  `dcf_value(...).value_per_share - current_price = 0`의 근을 이분법으로 찾음.
  브래킷 양 끝 부호가 같으면 `ValueError`(현재가가 탐색 범위 밖).

**TDD:** 알려진 성장률로 만든 주당가치를 현재가로 넣으면 그 성장률을 복원
(수렴), 단조성 가정 검증, 범위 밖 예외.

### Task 5: 의사결정 함수 (`valuation/decision.py`)

**Files:** Create `valuation/decision.py`; Test `tests/test_valuation_decision.py`

**Interfaces (Produces):**
- `Signal`(str Enum): `ACCUMULATE, PARTIAL, HOLD_OR_TRIM, EXIT`
- `Decision(signal, current_price, max_buy: ScenarioValues, reason)` frozen
- `decide(current_price, max_buy: ScenarioValues) -> Decision` — 문서 §5 4구간:
  `<= max_buy.conservative` → ACCUMULATE; `~ base` → PARTIAL;
  `~ optimistic` → HOLD_OR_TRIM; `> optimistic` → EXIT. **매수단가 입력 없음.**
- `CompanyType`(Enum) + `primary_model(company_type) -> str` 라우팅 스켈레톤
  (DCF 외에는 `NotImplementedError`).
- `GateResult(passed, reasons)` + `accumulate_gate(price_ok, thesis_intact,
  survival_ok) -> GateResult` — 3조건 AND(문서 §5 추가매수 게이트).

**TDD:** 경계값 4구간, 게이트 AND(하나만 False여도 실패·사유 기록),
`decide` 시그니처에 매수단가·수익률 인자가 없음을 계약으로 확인.

### Task 6: Phase 1 회귀 + 문서 반영

전체 `pytest -q` 통과 확인. `docs/architecture.md` §7 코드 위치 표에
`valuation/` 행 추가, `README.md` 확장 목록에 한 줄. 커밋 `VF: valuation core`.

---

## Phase 2 — DART 재무 수집 · PIT 정규화

### Task 7: DART 클라이언트 + 재무 모델 (`data/fundamentals.py`)

**Files:** Create `data/fundamentals.py`; Test `tests/test_fundamentals_client.py`;
fixtures `tests/data/dart/*.json`

**Interfaces (Produces):**
- `Transport = Callable[[str, dict], dict]` (URL, params → JSON dict)
- `DartClient(api_key, transport)`; `.financial_statements(corp_code, year,
  report_code) -> list[RawAccount]`; `.disclosure_list(corp_code, start, end)
  -> list[Disclosure]`. 실제 네트워크 transport는 별도 팩토리
  `requests_transport()`로 분리(테스트는 가짜 transport 주입).
- `RawAccount(account_name, amount, report_period, currency)`,
  `Disclosure(rcept_no, report_name, rcept_dt)` frozen dataclasses.
- API 오류코드(비‑`000`) 시 `DartApiError`.

**TDD:** fixture 목으로 파싱, 오류코드 예외, 키 누락 안내.

### Task 8: PIT 정규화 + 매핑 (`data/fundamentals.py` 확장)

**Files:** 위 파일 확장; Test `tests/test_fundamentals_pit.py`

**Interfaces (Produces):**
- `available_at(rcept_dt, market) -> date` — 공시일 다음 거래일(기존
  `engine/calendar.py` 재사용).
- `to_fundamental_record(accounts, disclosure, market) -> FundamentalRecord` —
  `report_period`·`announcement_date`·`available_at` 분리 저장 + FCFF 구성요소
  매핑(매출·영업이익·감가상각·자본적지출·운전자본·순차입금·희석주식수).
- `FundamentalStore.as_of(corp_code, as_of: date) -> FundamentalRecord | None` —
  `available_at <= as_of` 필터 강제(위반 접근은 미래 데이터 반환 안 함).
- 결측 계정은 명시적 `None`(0으로 채우지 않음).

**TDD:** rcept_dt → available_at 다음 거래일, PIT 컷오프(공시 전 조회는
None), 매핑 정확성, 결측 표시.

### Task 9: Phase 2 회귀 + 문서 + (선택) CLI 배선

전체 회귀 통과. `docs/architecture.md`에 fundamentals 행 추가. 시간이 되면
`tradingbot fundamentals update` CLI를 `research` 서브커맨드 패턴으로 배선하되,
키 없으면 안내 메시지. 커밋 `VF: DART fundamentals ingestion`.
