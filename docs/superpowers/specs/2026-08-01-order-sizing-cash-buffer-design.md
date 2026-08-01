# 주문 사이징 현금 버퍼 설계

- 날짜: 2026-08-01
- 상태: 설계 완료
- 관련 문서: [`docs/us_etf_rotation_review.md`](../../us_etf_rotation_review.md),
  `config/us_etf_rotation.toml`, `config/us_etf_benchmark.toml`

## 1. 한 문단 요약

미국 ETF 백테스트에서 주문의 17~19%가 `minimum cash buffer breached`로
거부된다. 원인은 매수 주문을 심사할 때 **지금 통장에 있는 현금**을 보기
때문이다. 이 엔진에서 종가에 낸 주문은 다음 날 시가에 체결되고, 그 매수를
먹여살릴 매도 주문은 같은 주문장에 한 칸 앞서 줄 서 있다. 그래서 심사는
"오늘 이 돈이 있느냐"를 묻지만, 정답은 "체결 시점에 이 돈이 있겠느냐"다.
이 작업은 그 질문을 바꾼다.

## 2. 문제 확인 (재현됨)

`src/tradingbot/risk.py:69-72`:

```python
gross = order.qty * estimated_price
min_cash = equity * self.limits.min_cash_buffer_pct
if broker.cash - gross < min_cash - 1e-9:
    return "minimum cash buffer breached"
```

`broker.cash`는 **제출 시점** 잔고다. CLOSE 국면에서는 체결이 일어나지 않으므로
그날 제출된 모든 주문이 똑같은 잔고를 상대로 심사받는다.

재현 (초기자본 10만 달러, `min_cash_buffer_pct = 0.02`):

```
day1 BUY AAA qty=980 status=OPEN
after fill: cash=2000.00 equity=100000.00 cash_pct=0.0200
day2 SELL AAA qty=980 status=OPEN
day2 BUY  BBB qty=1960 status=REJECTED reason=minimum cash buffer breached
```

`apply_constraints`가 목표 비중 합계를 `1 - cash_buffer = 0.98`로 깎으므로,
완전투자 상태의 현금은 **정확히** `equity × 0.02`, 즉 버퍼 그 자체에 붙는다.
따라서 `cash - gross < equity × 0.02`는 `gross > 0`인 모든 매수에 대해 참이다.

여기서 부호가 반대인 두 오류가 같은 누락에서 나온다.

- **너무 엄격하다** — 줄 서 있는 매도 대금이 보이지 않는다. 자금이 조달된
  로테이션이 거부된다. 관측된 17~19%가 이것이다.
- **너무 느슨하다** — 줄 서 있는 매수 약정이 보이지 않는다. N개의 매수가 저마다
  같은 잔고에 "들어맞는다"고 판정받는다. 지금은 체결 시점의
  `insufficient cash`(`broker/backtest.py:182`)가 뒤늦게 잡아낸다.

### 피해는 "주문 하나가 사라짐"보다 크다

거부된 것은 매수뿐이고 **매도는 다음 날 아침 그대로 체결된다.** 포트폴리오는
현금 100%가 되어 다음 리밸런싱까지 한 달을 그대로 앉아 있는다. 초기자본을
10배로 올려도 거부율이 그대로였던 이유도 같다 — 버퍼는 자본의 **비율**이라
자본과 함께 커진다. 거부율이 50%가 아니라 17~19%인 것도 설명된다: 로테이션이
반만 실행되면 계좌가 현금 부자가 되어 다음 달 매수는 통과하고, 이 패턴이
번갈아 나타난다.

## 3. 빠진 개념 하나: 체결 시점의 가용 현금

```
가용현금 = 현재현금
         + Σ (미체결 매도 주문의 예상 대금)
         − Σ (미체결 매수 주문의 예상 비용)
```

## 4. 역할 분담

`config/us_etf_rotation.toml`이 이미 의도를 적어 놓았다 — "엔진 리스크 캡은
전략 상한 위의 백스톱이어야지, 더 타이트한 거부권이면 안 된다." 이 원칙을
현금 버퍼에도 그대로 적용한다.

| 계층 | 책임 | 파일 |
|---|---|---|
| 브로커 | 주문장이 정산되면 현금이 얼마가 되는지 답한다 | `broker/backtest.py` |
| 주문 사이징 | 버퍼를 **미리 떼어두고** 남은 돈으로 수량을 정한다 | `engine/engine.py` |
| 리스크 매니저 | 그래도 넘는 주문만 거부하는 백스톱 | `risk.py` |

핵심은 버퍼가 **거부권이 아니라 사이징 제약**이 되는 것이다. 자금이 조달된
로테이션은 제대로 사이징되고, 정말 못 사는 주문은 사라지는 대신 **작게** 나간다.

### 범위 선

`weight=`로 낸 매수만 깎는다. `qty=`는 호출자가 명시한 지시이므로 지금의
전부-아니면-전무 의미를 유지하고, 버퍼를 넘으면 리스크 매니저가 거부한다.

## 5. 가격 추정과 보수성

시장가 주문은 가격을 들고 있지 않다. `EngineContext`가 리스크 심사용으로 이미
계산해 놓고 버리는 값이 있으므로, 그것을 `Order.estimated_price`에 남긴다.
`PaperBroker`는 주문을 JSON으로 직렬화하므로 재시작에도 살아남게 필드를 추가한다.

추정치는 종가지만 체결은 다음 시가 × (1 ± 슬리피지) + 수수료다. 그래서
**비용은 높게, 대금은 낮게** 추정한다 — `_fill`이 쓰는 것과 **같은**
`apply_slippage` / `round_execution_price` / `FeeModel`을 그대로 쓴다. 새 상수도
새 설정 항목도 만들지 않는다.

밤사이 갭에 대한 추가 할인은 두지 않는다. 전체가 5% 갭다운하면 매도 대금도
5% 줄지만 매수 비용도 5% 줄어 서로 상쇄된다 — 노출은 판 종목과 산 종목의
**상대** 움직임뿐이고 이는 2차 효과다. 그래도 어긋나면 체결 시점의
`insufficient cash`가 지금과 똑같이 최종 백스톱으로 남는다.

## 6. 변경 지점

| 파일 | 변경 |
|---|---|
| `models.py` | `Order.estimated_price: float \| None = None` |
| `broker/backtest.py` | `projected_cash()`, `affordable_qty()`, `_execution_price()` 추출 |
| `broker/paper.py` | `estimated_price` 직렬화 왕복 |
| `engine/engine.py` | `_resolve_qty`가 가용현금−버퍼로 예산을 제한 |
| `risk.py` | `broker.cash` → `broker.projected_cash()` |

`_execution_price()`는 `_fill`에서 뽑아낸다. 예측과 실제 체결이 같은 코드를
쓰게 만들어 둘이 갈라지는 것을 구조적으로 막는다.

`KISBroker`는 v1 전 메서드 미구현 스텁이고 `RiskManager.validate`의 타입도 이미
`BacktestBroker`이므로 이번 범위에서 건드리지 않는다.

## 7. 사이징이 백스톱을 건드리지 않음을 보인다

사이징은 `qty × 체결가 + 수수료 ≤ 가용현금 − 버퍼`를 만족시킨다. 심사는
`가용현금 − qty × 종가 ≥ 버퍼`를 본다. 슬리피지와 호가 올림 때문에
`체결가 ≥ 종가`이므로 `qty × 종가 ≤ qty × 체결가 ≤ 가용현금 − 버퍼`이고,
따라서 심사식은 항상 성립한다. **사이징을 거친 매수는 백스톱에 걸리지 않는다.**
`_resolve_qty`와 `_submit` 사이에 주문장은 변하지 않으므로 두 시점의
`projected_cash()`도 동일하다.

## 8. 관측 가능성

수정 후 클리핑은 조용히 일어난다. 진단 신호는 두 가지로 남는다.

1. `rejected_orders`가 0에 수렴한다 — 이번 작업의 성공 판정 기준 그 자체다.
2. 한 주도 못 사는 매수는 `qty=0`이 되어 브로커의 `quantity must be positive`로
   여전히 `rejected_orders`에 남는다. "전략이 사고 싶었는데 못 샀다"는 사실은
   사라지지 않는다.

## 9. 검증

이 저장소에는 `data/cache`가 없어 백테스트·평가 명령을 돌릴 수 없다. 검증은
fixture 기반 pytest로 한다(현재 623개 통과). 실데이터 재측정은 사용자가
로컬에서 수행한다.

새 테스트가 고정해야 할 것:

- 완전투자 상태의 로테이션(매도 후 매수)이 **거부되지 않는다** — §2의 재현 그대로.
- 같은 날 제출된 매수 여러 건이 같은 현금을 중복해서 쓰지 않는다.
- 매수 예산이 버퍼를 남긴다 — 체결 후 현금 ≥ `equity × min_cash_buffer_pct`.
- `qty=` 명시 주문은 여전히 전부-아니면-전무이고 버퍼를 넘으면 거부된다.
- 미체결 매도 수량이 보유 수량을 넘으면 보유분까지만 대금으로 잡는다.
- `PaperBroker` 재시작 후에도 `estimated_price`가 살아 있다.

## 10. 하지 않는 것

- 슬리피지·체결 모델 현실화(M13)는 별건이다.
- `plan_rebalance`의 매도-우선 순서는 이미 옳다. 건드리지 않는다.
- 매도 측 리스크 심사는 범위 밖이다(`_fillable_qty`가 이미 보유분으로 자른다).
