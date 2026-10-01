# M3 주간 리밸런싱 제안 설계

## 0. 이 문서가 답하는 질문

목표는 "검증된 퀀트 알고리즘을 바탕으로 매주 1회 매수/매도 제안". M1·M2로 보고 계층은 완성됐다. M3는 그 위에 제안을 얹는다.

이 문서는 "어떻게 좋은 전략을 만드는가"에 답하지 않는다. 그것은 연구의 몫이다. 이 문서는 **"검증되지 않은 상태에서 이 봇은 무엇을 말해야 하는가"** 에 답한다.

---

## 1. 착수 시점 판단 — 통과한 전략이 없고, 있었어도 쓸 수 없었다

| 전략 | 판정 |
|---|---|
| `theme_multifactor` (KR) | 승격 불가 (2026-07-26) — 기준 2개 미달, 1개 측정 불가 |
| `us_asset_rotation` (US) | 승격 불가 (2026-07-29) — 두 벤치마크 모두에 패배 |

`us_asset_rotation`을 신호원으로 재활용할 수 없는 이유가 **넷**이고 각각 독립적으로 치명적이다.

1. **초과수익 −2.93%p.** 기준은 ≥ 0.0.
2. **방어 다리가 실행 불가.** 검증된 −18.46% MDD는 `abs_momentum_ma_days = 200`, `bear_exposure = 0.5` 아래에서 TLT·IEF·LQD·GLD·DBC로 회피해 얻은 값이다 (`us_etf_rotation.toml:75,87`). 계좌는 그중 아무것도 보유하지 않는다. **성과가 실행할 수 없는 구간에 의존한다.**
3. **주기가 다르다.** 검증은 월간(`us_etf_rotation.toml:71`), 목표는 주간.
4. **체결 무결성이 깨져 있다.** `us_etf_rotation.toml:17-20`의 자체 주석이 주문 거부율 17~19%를 기록한다. 원인은 주문 사이징이 `[risk_limits] min_cash_weight`를 미리 확보하지 않는 것이고 별도 과제로 미뤄졌다. 즉 4.91% CAGR은 **기각된 전략조차 깨끗하게 측정한 값이 아니다.**

문구로 고칠 수 있는 문제가 아니다. 그래서 M3의 1단계는 전략 실행이 아니라 **거부**다.

---

## 2. 전제가 바뀌었다 — "레버리지는 측정 불가"는 절반이 수집 아티팩트였다

당초 판단은 "레버리지 상품은 `max_mdd = 0.25`를 통과할 수 없으니 비레버리지에서 검증하고 신호만 옮긴다"였다. 조사 결과 그 전제가 **5종 중 2종에는 거짓**이다.

`data/cache.py:56`은 신규 심볼의 시작일을 `start or "2015-01-01"`로 잡는다. SOXL과 TECL이 2014-12-31부터 2927행으로 캐시된 것은 **상장일이 아니라 수집 창**이다 (AAPL·MSFT·NVDA도 같은 날짜로 시작한다 — AAPL이 2014년에 상장했을 리 없다). 실제 이력은 SOXL 2010-03, TECL 2008-12부터이고 무료로 백필된다(`cache.py:51-52`의 백필 분기). 그러면 2011·2015-16·2018Q4·2020·2022 낙폭이 모두 들어오고, **거래하는 종목 자체에서 MDD·Sharpe·실현 드래그를 직접 측정할 수 있다.**

따라서 책을 **측정 가능성으로 가른다.**

| 보유 | 상태 | 결론 |
|---|---|---|
| `SOXL` `TECL` | 백필 후 2010/2008~ | 거래 종목에서 직접 측정 가능. 이전 불필요 |
| `FNGU` 378행 · `GGLL` 993행 · `SPCX` 49행 | 구조적 부족. `config/themes.toml`에 레버리지 프록시 전무. FNGU는 ETN(발행사 채무) | **영구 재량 보유.** 임시 상태가 아니다 |

일괄 이전보다도, 일괄 거부보다도 정직하고 유용하다. 신호 이전 기계는 여전히 만들되 §3.8의 측정 통과를 조건으로만 작동한다.

---

## 2.1 이중 트랙 검증 — 신호는 1배에서, 위험은 실물에서

`max_mdd = 0.25`는 3배 상품에서 통과 불가능하다. 그렇다고 그 기준을 레버리지에
맞춰 늘리면 기준이 아니라 자기기만이 된다. 두 질문이 섞여 있기 때문에 생기는
교착이다. 분리하면 둘 다 정직하게 답할 수 있다.

| | Track A — 신호 검증 | Track B — 실행 위험 측정 |
|---|---|---|
| 무엇을 묻나 | 이 전략에 예측력이 있는가 | 이걸 3배로 타면 실제로 어떻게 되나 |
| 유니버스 | 1배: `SOXX` `XLK` `SMH` `IGV` `XLF` `XLE` | 실물: `SOXL` `TECL` |
| 기준 | 기존 `[promotion]` 그대로 | 레버리지 프로파일 |
| 벤치마크 | 1배 유니버스 동일비중 | **같은 3배 상품 매수보유** |
| 역할 | **승격을 가른다** | **공시한다. 승격을 가르지 않는다** |

**승격은 Track A만 가른다.** Track B가 나쁘다고 신호가 틀린 것은 아니고, Track B가
좋다고 신호가 검증된 것도 아니다. 3배 상품은 상승장에서 아무 신호 없이도 좋아
보인다. 실행 위험을 승격 기준에 넣으면 시장 방향을 예측력으로 착각하게 된다.

**Track B는 반드시 보여준다.** Track A만 보여주면 독자는 18% 낙폭으로 검증된
전략을 보고 자기 계좌도 그럴 것이라 믿는다. 실제로는 그 몇 배다. 그 격차가 이
설계가 존재하는 이유의 절반이다.

**두 트랙 모두 §3.2를 지킨다.** Track A의 숫자는 SOXX·XLK 옆에, Track B의 숫자는
SOXL·TECL 옆에 놓인다. 어떤 숫자도 측정되지 않은 종목 옆에 가지 않고, 환산해서
옮기지도 않는다. 이것이 (다)와 (가)를 병행하는 진짜 이유다 — 하나만 골랐다면
반드시 한쪽 숫자를 남의 자리에 놓아야 했다.

**Track B의 벤치마크가 SPY가 아니라 같은 3배 상품인 이유.** 3배 전략을 SPY와
비교하면 레버리지 자체가 초과수익으로 계상된다. 그건 전략의 공이 아니라 배수의
공이다. SOXL 전략은 SOXL 매수보유를 이겨야 의미가 있다.

**레버리지 프로파일의 임계값은 통과할 때까지 낮추는 것이 아니다.** 완화가 아니라
**다른 벤치마크에 대한 다른 질문**이다. 프로파일을 새로 쓸 때는 근거를 이 문서에
남기고, 벤치마크를 함께 바꾼다. 그 둘을 같이 하지 않으면 기준이 아니라 변명이다.

**Track B가 없는 보유는 어떻게 되나.** `FNGU` `GGLL` `SPCX`는 이력이 모자라
Track B를 돌릴 수 없다. §2대로 영구 재량 보유로 남는다. Track A가 통과해도
이들에게는 지시가 나가지 않는다 — 신호가 유효해도 그 상품에서 무슨 일이 벌어질지
잴 수 없기 때문이다.

---

## 3. 설계 규칙

각 규칙은 테스트로 집행된다. §3.1~§3.10은 협상 불가, §3.11은 2차이되 먼저 만든다.

### 3.1 승격 연동 — 캐비앳이 아니라 차단
`Verdict.promoted`가 `True`가 아니면 브리핑은 **매수/매도 지시를 한 줄도 렌더하지 않는다.** 연구 노트로 라벨링해 보여줄 뿐이다. 캐비앳을 달아도 Sharpe 0.63이 화면에서 가장 큰 글자로 남는다.

### 3.2 측정되지 않은 종목 옆에 지표를 놓지 않는다
모든 지표는 `(값, 측정된_종목, 구간)`을 함께 지닌다. `측정된_종목 != 표시_종목`이면 숫자 대신 **"측정 불가"**. 환산한 수를 지어내지 않는다. 기존 `passed=None`(`evaluation.py:210`)과 미측정이 승격을 막는 규칙(`evaluation.py:259,269-275`)을 그대로 재사용한다.

### 3.3 실행 불가능한 구간은 버리지 않고 보고한다
`ThemeUniverse.members(dt)`와 계좌의 거래 가능 집합을 교집합하고, 대응이 없는 구성원은 **"이 구간은 실행 불가"** 로 렌더한다. §1의 2번을 잡는 규칙이다.

### 3.4 국면은 랭킹보다 먼저 나오는 1급 출력
`국면: 리스크오프, 목표 현금 X%`. 리스크오프 주간에 레버리지 신규 매수를 내보내지 않는다.

### 3.5 주기가 검증과 다르면 거부한다
`Verdict`가 리밸런싱 주기를 지닌다. 월간으로 검증된 판정으로 주간 지시를 내지 않는다. 주기를 바꾸면 `run_walk_forward`를 다시 돌린 뒤에야 숫자를 표시할 수 있다.

### 3.6 레버리지 등록부는 하나뿐이다
`briefing.py:40`의 `_LEVERAGED`를 삭제한다. **GGLL과 SPCX가 빠져 있어** 이 계좌는 등록부에 걸리지 않는 상품을 둘 보유한다. 게다가 `briefing.py:262`는 `notes.append(...)` — 게이트가 아니라 메모다. `news.py`의 `UNDERLYING` ∪ `_UNMAPPED_BASKETS`가 이미 관련 티커를 전부 덮으므로, 이를 `leverage_multiple`, `instrument_type`(etf/etn/single_stock_levered), 보수, 기초지수를 담은 **하나의 상품 표**로 승격시킨다.

### 3.7 레버리지 지시 줄마다 의무 공시
배수 L; 연 캐리 = 보수 + (L−1)·조달비용; 드래그 = (L²−L)·σ̂²/2 + 캐리 (기존 `volatility_days = 60` 창 사용); 그 종목 자신의 캐시 시작일과 **검증 구간과의 중첩 비율**; 자격을 갖춘 프록시의 존재 여부.

### 3.8 프록시 쌍은 주장하지 않고 측정한다
거래 종목 일간수익률을 `L × 프록시 일간수익률`에 회귀해 **베타 ∈ [0.9L, 1.1L]이고 R² > 0.95** (직전 1년)일 때만 쌍을 공표한다. 통과하는 프록시가 없으면 **"이 보유에 대해 검증된 신호가 없음 — 재량 보유"**.

이 규칙이 §2의 신호 이전을 안전하게 만드는 유일한 장치다. 지수 관계에 대한 주장이 아니라 측정이 근거이므로 어떤 상품이든 같은 잣대로 판정된다.

### 3.9 체결 무결성을 공시한다
백테스트 결과가 `거부수/전체`를 지니고 브리핑이 이를 표시한다. 거부율이 임계값을 넘으면 승격성 표시를 아예 막는다. §1의 4번이 다시 숨지 못하게 하는 규칙이다.

### 3.10 판정을 낳은 실행이 만들지 않은 숫자는 그 판정 옆에 못 온다
서로 다른 실행의 수치를 한 화면에서 섞지 않는다.

### 3.11 실현 대 기대 대조표 (2차 중 최우선)
매주 각 보유의 실현 수익률과 `L × 프록시` 수익률, 그리고 추적 시작 이후 누적 괴리(%p)를 `state/` 아래 남긴다. 목록에 없는 실패 모드까지 잡는 가장 싼 장치이고, 이것이 없으면 설계가 틀린 채로 무한히 유지될 수 있다.

### 이후 순위
`scale_costs`에 캐리를 일간 적립으로 넣어 2배 비용 검사가 실제로 무언가를 검사하게 할 것(현재는 수수료+5bp 슬리피지만 2배라 레버리지에서는 공허하다) · 위험한도를 비중이 아니라 **익스포저(비중×L)** 공간에서 · 점추정 대신 경로 재표집 5/50/95 밴드 · 세후 오버레이 · 종목별 슬리피지 · ETN 구조적 위험 상시 표기.

---

## 4. 거부는 한 종류가 아니다

M2 §3.7의 규율을 확장한다. 다음은 **절대 같은 문장으로 렌더되지 않는다.**

| 상태 | 독자가 알아야 할 것 |
|---|---|
| 평가된 적 없음 | 아직 아무도 재보지 않았다 |
| 평가했고 미달 | 재봤고 못 미쳤다 — 어느 기준인지까지 |
| 측정 불가 | 데이터가 모자랐다. 통과가 아니다 |
| 기록이 낡음 | 통과했지만 그 뒤 코드가 바뀌었다 |
| 구조적 재량 보유 | 이 상품은 앞으로도 측정되지 않는다 (§2) |
| 통과, 변경 불필요 | 정상 작동 중이며 이번 주는 그대로 두면 된다 |

**"제안 없음"과 "그대로 두라"는 완전히 다른 말이다.** 전자는 봇이 말할 수 없다는 뜻이고 후자는 봇이 판단한 결과다.

---

## 5. 인터페이스

### `src/tradingbot/research/promotion_ledger.py` (신설)

```python
class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True)
class CriterionResult:
    name: str
    threshold: float
    measured: float | None      # None 이면 측정 불가
    passed: bool | None


@dataclass(frozen=True)
class PromotionRecord:
    strategy: str
    market: str
    universe: tuple[str, ...]
    verdict: Verdict
    criteria: tuple[CriterionResult, ...]
    cadence: str                              # §3.5
    rejected_orders: int
    total_orders: int                         # §3.9
    evaluated_at: datetime
    commit: str
    report_path: str


def record_promotion(record, root) -> Path
def latest_promotion(strategy, market, root) -> PromotionRecord | None
def passing_strategies(root, *, current_commit) -> tuple[PromotionRecord, ...]
```

승격은 평가 당시 커밋에 묶인다. 전략 코드가 바뀌면 옛 판정은 근거가 아니다.

### `src/tradingbot/instruments.py` (신설, §3.6)

```python
@dataclass(frozen=True)
class Instrument:
    symbol: str
    leverage: float
    kind: str                    # "etf" | "etn" | "single_stock_levered"
    expense_ratio: float | None
    benchmark_index: str | None


INSTRUMENTS: dict[str, Instrument]     # 유일한 등록부


def leverage_of(symbol: str) -> float
```

### `src/tradingbot/proposal.py` (신설)

```python
class NoProposalReason(Enum):
    NEVER_EVALUATED = "never_evaluated"
    DID_NOT_PASS = "did_not_pass"
    UNMEASURABLE = "unmeasurable"
    STALE_RECORD = "stale_record"
    CADENCE_MISMATCH = "cadence_mismatch"
    DISCRETIONARY_HOLDING = "discretionary_holding"


# 거부가 아니다. 통과했고 이번 주 바꿀 것이 없다는 봇의 답이므로
# 거부 목록에 넣지 않는다 (§4 마지막 줄).
@dataclass(frozen=True)
class PassedNoChange:
    basis: PromotionRecord


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    quantity: int
    target_weight: float
    current_weight: float
    estimated_cost: float
    leverage: float                  # §3.7
    annual_carry: float
    expected_drag: float
    measured_on: str                 # §3.2
    overlap_fraction: float


@dataclass(frozen=True)
class Proposal:
    orders: tuple[Order, ...]
    regime: str | None                                   # §3.4
    rebalance_needed: bool
    basis: PromotionRecord | None
    refusals: tuple[tuple[str, NoProposalReason], ...]   # 종목별
    detail: str


def propose_rebalance(snapshot, *, ledger_root, current_commit,
                      price_history=None, now=None) -> Proposal
```

`refusals`가 종목별인 것이 §2의 결론을 담는다. 한 계좌 안에서 어떤 보유는 측정 가능하고 어떤 보유는 영구 재량이다.

§3.3의 **실행 불가능한 구간**은 이 목록에 없다. 그것은 보유 하나에 대한 판정이
아니라 전략이 요구하는 다리를 계좌가 실행할 수 없다는 사실이므로, 목표 비중이
생기는 Task 8 단계에서 다뤄진다. 초안에 있던 `INSUFFICIENT_DATA`도 뺐다 —
`UNMEASURABLE`(재봤으나 데이터 부족)과 `DISCRETIONARY_HOLDING`(앞으로도 불가)이
이미 그 공간을 정확히 나눠 갖고 있어서, 셋째 값은 경계를 흐릴 뿐이다.

### `src/tradingbot/report/briefing.py` (수정)

`SECTIONS`에 `"proposal"`, `_render_proposal`, `_Context.proposal`, `render_briefing(..., proposal=None)`. `None`이면 섹션이 없어 기존 출력은 바이트 동일 (M2 Task 4와 동일 방식).

---

## 6. 테스트 명세

### TestPromotionLedger
1. `test_a_recorded_verdict_round_trips`
2. `test_an_unmeasurable_criterion_is_not_a_pass`
3. `test_the_latest_record_wins_when_a_strategy_is_reevaluated`

### TestInterlock (§3.1)
4. `test_a_failed_verdict_renders_no_buy_or_sell_line`
5. `test_a_stale_commit_is_not_a_basis`

### TestMeasurementProvenance (§3.2)
6. `test_a_metric_measured_on_another_symbol_renders_unmeasurable`
7. `test_no_scaled_number_is_invented_for_a_leveraged_holding`

### TestExecutability (§3.3)
8. `test_a_leg_the_account_cannot_trade_is_reported_not_dropped`

### TestCadence (§3.5)
9. `test_a_monthly_verdict_refuses_a_weekly_instruction`

### TestLeverageRegistry (§3.6)
10. `test_only_one_module_defines_leverage`
11. `test_every_held_symbol_resolves_in_the_registry`

### TestDisclosure (§3.7)
12. `test_each_leveraged_order_states_multiple_carry_and_drag`
13. `test_the_overlap_fraction_with_the_validation_window_is_stated`

### TestProxy (§3.8)
14. `test_a_proxy_failing_beta_or_r2_is_not_published`
15. `test_no_qualifying_proxy_reads_as_discretionary_holding`

### TestExecutionIntegrity (§3.9)
16. `test_a_high_rejection_rate_blocks_a_promotion_flavoured_display`

### TestRefusalVocabulary (§4)
17. `test_the_six_refusals_render_as_six_different_sentences`
18. `test_no_proposal_and_hold_as_is_are_not_the_same_sentence`

### TestNoExecution
19. `test_the_proposal_module_has_no_order_placement_path`

### TestBriefing
20. `test_without_a_proposal_the_briefing_is_unchanged`

---

## 7. 하지 말 것

- **주문을 실행하지 않는다.** 토스 어댑터는 읽기 전용이고 테스트가 주문 경로 부재를 못 박고 있다. 그 못을 뽑지 않는다.
- **거부를 침묵으로 렌더하지 않는다.** 빈 섹션은 "제안 없음"이 아니라 "고장"으로 읽힌다.
- **`UNMEASURABLE`을 `FAIL`로 접지 않는다.**
- **마크다운 판정문을 파싱하지 않는다.** 표가 바뀌면 조용히 깨진다.
- **통과할 때까지 임계값을 낮추지 않는다.**
- **익스포저 환산으로 검증된 위험을 복원했다고 주장하지 않는다.** 40% SOXX를 13.3% SOXL + 현금으로 바꾸는 것은 **최초 명목만** 일치시킨다. 일간 리셋이 즉시 재레버리지하고, 놀리는 현금 때문에 "완전 투자" 상태로 검증된 수익률도 더는 적용되지 않는다. 규칙으로 둘 수는 있으나 위험 프로파일을 되돌린다고 말하면 거짓이다.

---

## 8. 실행 순서

- [ ] **Task 0: 데이터 백필** (코드 없음, 무료, 네트워크 필요)
  - `data update --market US --symbols SOXL TECL --start 2008-01-01` — 수집 창 아티팩트 해소 (§2)
  - `data update --market US --symbols SOXX XLK SMH IGV XLF XLE --start 2007-01-01` — 1배 섹터 층. **현재 하나도 없다.**
  - `data update --market US --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ` — 광의 11종이 2026-07-28에 멈춰 레버리지 파일(2026-08-21)과 24거래일 어긋난다
  - `data update --market US --symbols FNGU --start 2018-01-01` — 1회 시도. ETN 이력이 오면 사용 가능, 안 오면 영구 재량 확정
- [ ] **Task 1: 승격 원장** — `promotion_ledger.py` + 테스트 1~5 · `PROPOSE(part): Record a verdict a machine can read`
- [ ] **Task 2: 상품 등록부** — `instruments.py`, `_LEVERAGED` 삭제 + 테스트 10~11 · `PROPOSE(part): Keep one answer to what a symbol actually is`
- [ ] **Task 3: 평가기 연결** — `research evaluate`가 원장에 기록, 주기·거부율 포함 · `PROPOSE(part): Write the verdict where the bot can find it`
- [ ] **Task 4: 거부 엔진** — `proposal.py`, 종목별 사유. 주문 생성 없음 · `PROPOSE(part): Say why there is nothing to propose`
- [ ] **Task 5: 브리핑 섹션** — 사유가 매주 보인다 · `PROPOSE(part): Show the reader why the bot is silent`
- [ ] **Task 6: 프록시 측정 + 공시** — §3.8, §3.7 · `PROPOSE(part): Measure the proxy instead of asserting it`
- [ ] **Task 7: 실현 대 기대 대조표** — §3.11 · `PROPOSE(part): Watch the gap between what was promised and what happened`
- [ ] **Task 8: 주문 생성** — 근거가 생겼을 때만 · `PROPOSE: Turn a validated target into orders you can place`

Task 1~7은 통과한 전략이 없어도 지금 만들 수 있고, 만들면 **공백이 매주 눈에 보인다.**

---

## 9. 알려진 한계

1. **엔진이 완성돼도 제안은 나오지 않는다.** 통과한 전략이 없기 때문이다. 이 설계는 배관이고, 목표 자체는 연구가 푼다.
2. **FNGU·GGLL·SPCX는 영구 재량이다.** 임시 상태가 아니다. 계좌의 5분의 3이 봇의 판단 밖에 있다는 뜻이고, 이는 숨기지 않고 매주 표시된다.
3. **전략 인터페이스가 비중을 내지 않는다.** 기존 `Strategy`는 `on_bar`에서 `buy`/`sell`을 부르는 이벤트 구동형이다. 주간 목표 비중을 얻으려면 어댑터가 필요하고, 어댑터가 백테스트와 다르게 동작하면 검증이 무의미해진다. Task 8의 가장 큰 위험.
4. **정수 주수 반올림이 비중을 흔든다.** 소액 계좌에서 고가 ETF 한 주는 큰 비중이다. 반올림 후 익스포저 한도를 넘지 않는지 검사한다.
5. **선언된 연구 프로토콜이 지켜지지 않았다.** `research.toml:25-30`의 `[periods]`는 in-sample 2010-2018 / OOS 2022+ 를 선언하고 튜닝을 금지하지만, 실제 팩터 교체는 in-sample 2007-2016 · 확인 2017-2026으로 이뤄졌다(`us_etf_rotation.toml:79-83`). 그 표를 고쳐도 아무것도 바뀌지 않고 아무 오류도 나지 않는다. 연구 무결성 문제이며 M3 범위 밖이지만 기록해 둔다.
