# P0 계측기 수리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리밸런싱 매수 주문의 17~19%가 거부되는 원인을 제거해, 승격 기준 1번(초과수익)과 6번(비용 2배)의 실측값이 알파와 사이징 결함의 혼합물이 되지 않게 한다. 그리고 탐색이 쓸 In-sample 구간을 설정 파일에 확정한다.

**Architecture:** 새 개념을 만들지 않는다. 제출 시점의 현금 투영이 **같은 계획 안에서 먼저 제출된 매도의 대금을 빠뜨리고 있는 것**이 결함이므로, 그 대금을 투영에 더한다. 계산은 한 곳(`EngineContext`)에서 하고 사이징과 검증이 **같은 숫자**를 쓴다 — 둘이 어긋나면 결함이 형태만 바꿔 되살아난다.

**Tech Stack:** Python 3.13, pandas, pytest. **신규 의존성 없음.**

**스펙:** `docs/superpowers/specs/2026-08-30-promotion-candidate-exploration-design.md` §3.1, §4.1, §5(P0), §12

---

## 착수 전에 읽을 것: 설계의 P0 공식은 불충분했다

설계 §5 P0은 이렇게 적었다:

```
budget = min(equity * weight,  cash - equity * min_cash_buffer_pct)
```

**이 공식만으로는 문제가 고쳐지지 않고, 오히려 나빠진다.** 구현 착수 전 주문 수명주기를
읽어 확인한 사실이다.

### 실제 순서

1. `_handle_close`가 CLOSE 페이즈에서 `strategy.on_bar`를 부르고, 전략이 **하루치 리밸런싱
   계획 전체(매도 먼저, 매수 나중)를 D일 종가에 제출**한다.
2. `EngineContext._submit`이 주문마다 **제출 즉시** `risk_manager.validate`를 부른다
   (`engine.py:215`).
3. 실제 체결은 **D+1일 시가**다 (`_handle_open` → `broker.on_session_open`).

즉 **매수는 매도가 체결되기 전날 밤의 현금으로 검증된다.** 매도 주문은 제출만 됐을 뿐
현금을 움직이지 않았다 — 현금은 체결에서만 움직인다.

### 그래서 결함의 정확한 위치

`risk.py:69-72`의 검사는 다음 아침의 조건을 **오늘 밤의 현금으로 앞당겨 판정**하고 있다.

```python
gross = order.qty * estimated_price
min_cash = equity * self.limits.min_cash_buffer_pct
if broker.cash - gross < min_cash - 1e-9:
    return "minimum cash buffer breached"
```

`broker.cash`에는 같은 계획의 매도 대금이 아직 없다. **판정 자체가 시점을 잘못 잡았다.**

### 체결 시점은 이미 옳게 동작하고 있다

- `_process_orders`는 `self._open_orders`를 **제출 순서대로** 훑고, `plan_rebalance`가
  매도를 먼저 넣는다 → D+1 시가에 **매도가 먼저 체결되어 현금이 들어온 뒤 매수가 체결된다.**
- 그리고 체결 시점에는 **수수료까지 반영한 진짜 바닥**이 이미 있다
  (`backtest.py:182`): `if gross + fee > self.cash: reject "insufficient cash"`.

**따라서 지금 거부되는 주문의 상당수는 다음 날이면 아무 문제 없이 체결됐을 주문이다.**
버퍼 검사는 하드 불변식이 아니라 사전 가드이며(체결 시점에는 버퍼를 아예 검사하지 않는다),
지금은 그 가드가 과도하게 발동한다.

### 그래서 설계 공식이 왜 나쁜가

설계 공식은 사이징을 `cash`(매도 전 현금)에 묶는다. 포지션 A를 전량 팔고 B를 사는 회전에서
D일 밤 현금은 거의 0이므로 **수량이 0이 되어 주문이 아예 나가지 않는다.** 거부는 집계되고
로그에 남지만 미주문은 조용하다 — **회전이 소리 없이 실종된다.** 지금보다 나쁘다.

### 수정된 접근

**대기 중인 매도 주문의 예상 대금을 현금 투영에 더한다.** 사이징과 검증이 같은 값을 쓴다.

```
available = cash + 대기매도대금 − equity × min_cash_buffer_pct
budget    = min(equity × weight, available)
```

- 이것은 **체결 시점에 실제로 벌어질 일을 앞당겨 맞게 계산한 것**이지 가드를 푼 것이 아니다.
- 하드 바닥은 그대로 `backtest.py:182`가 지킨다.
- 남는 위험: 매도가 체결되지 못하면(그 종목의 바가 없는 경우) 매수만 체결돼 버퍼를 침범할
  수 있다. 그 경우에도 현금이 음수가 되지는 않는다 — 위 하드 바닥이 막는다. 버퍼는 원래
  체결 시점에 강제되지 않으므로 **불변식이 약해지는 것이 아니라 사전 가드의 시점이
  교정되는 것**이다.

`config/us_etf_rotation.toml`의 기존 주석이 이 방향을 이미 못박아 뒀다: *"엔진 리스크 캡은
전략 상한 위의 백스톱이어야지, 더 타이트한 거부권이면 안 된다."*

> 설계 문서 §5 P0의 공식은 이 계획서에 맞춰 정정한다 (같은 커밋).

---

## Global Constraints

- **신규 의존성 추가 금지.**
- **기본 동작을 바꾸지 않는다.** `validate`의 새 인자는 기본값 `0.0`이라, 넘기지 않는 모든
  기존 호출자·테스트는 지금과 **완전히 동일하게** 동작한다.
- **사이징과 검증은 같은 숫자를 쓴다.** 두 곳에서 따로 계산하면 어긋나는 순간 결함이 형태만
  바꿔 되살아난다. 계산은 `EngineContext`에 한 번만 둔다.
- **가격 추정 경로를 통일한다.** 대기 매도 대금은 매수 사이징이 쓰는 것과 **같은**
  `_estimate_price`로 구한다. 다른 경로를 쓰면 둘이 미세하게 어긋난다.
- **매도 주문은 사이징 대상이 아니다.** `sell(qty=...)`은 수량이 명시되며 이 변경과 무관하다.
- **`risk_manager`가 `None`일 수 있다.** 엔진은 리스크 매니저 없이도 돌아간다 — 그 경우
  버퍼는 0으로 취급한다.
- 테스트에서 네트워크 접근 금지 (합성 fixture만).
- 파일 쓰기는 `encoding="utf-8"` 명시.
- 커밋 접두사: 중간 `FIX(part):`, 마지막 `FIX:`.
- **테스트 실행** (PowerShell, 저장소 루트) — 이 PC는 `--basetemp` 필수:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest <경로> -v --basetemp="$env:TEMP\pytest_tmp"
  ```

## 기존 인터페이스 (구현자가 알아야 할 정확한 시그니처)

- `EngineContext` (`engine/engine.py:47`): 필드 `feed`, `broker`, `risk_manager: RiskManager | None`, `_phase: OrderPhase`
  - `_resolve_qty(self, estimated_price: float, qty: int | None, weight: float | None) -> int` (`engine.py:156`)
  - `_estimate_price(self, symbol, order_type, limit_price, stop_price) -> float` (`engine.py:164`)
  - `_submit(...)` — `risk_manager.validate(order, self.broker, estimated_price)` 호출 (`engine.py:215`)
  - `equity() -> float`, `position(symbol)`, `has_open_order(symbol, side=None) -> bool`
- `RiskManager.validate(self, order: Order, broker: BacktestBroker, estimated_price: float) -> str | None` (`risk.py:57`) — 거부 사유 문자열 또는 `None`
- `RiskLimits` (`risk.py`, frozen): `max_position_pct`, `max_positions`, `max_daily_loss_pct`, `stop_loss_pct`, `min_cash_buffer_pct`
- `BacktestBroker` (`broker/backtest.py`): `open_orders() -> list[Order]` (OPEN 상태만), `cash -> float` (property), `equity -> float`, `position(symbol)`
- `Order` (`models.py`): `id, symbol, side: OrderSide, qty: int, order_type: OrderType, limit_price, stop_price, status: OrderStatus, created_phase`
- `OrderSide`: `str` Enum — `BUY = "BUY"`, `SELL = "SELL"`
- `config/research.toml` `[periods]`: `in_sample_start`, `in_sample_end`, `validation_start`, `validation_end`, `out_of_sample_start`
- 관련 기존 테스트: `tests/test_order_phase_and_rsi.py`, `tests/test_backtest_broker.py`, `tests/test_smoke_backtest.py` (고정값 회귀)

---

### Task 1: 대기 매도 대금을 반영하는 현금 투영

**Files:**
- Modify: `src/tradingbot/risk.py`
- Test: `tests/test_risk_cash_projection.py` (신규)

**Interfaces:**
- Produces:
  - `RiskManager.validate(order, broker, estimated_price, *, pending_sell_proceeds: float = 0.0) -> str | None`
    — 버퍼 검사를 `cash + pending_sell_proceeds - gross`로 수행. **기본값 0.0이므로 기존 호출자는 동작 불변.**

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_risk_cash_projection.py`:

```python
from __future__ import annotations

from tradingbot.models import Order, OrderSide, OrderType, TimeInForce
from tradingbot.risk import RiskLimits, RiskManager


class FakePosition:
    def __init__(self, qty: int = 0) -> None:
        self.qty = qty


class FakePortfolio:
    def __init__(self) -> None:
        self.positions: dict[str, FakePosition] = {}


class FakeBroker:
    """Only what `validate` reads: cash, equity, position, portfolio."""

    def __init__(self, cash: float, equity: float) -> None:
        self.cash = cash
        self.equity = equity
        self.portfolio = FakePortfolio()

    def position(self, symbol: str) -> FakePosition:
        return self.portfolio.positions.get(symbol.upper(), FakePosition())


def buy_order(symbol: str = "SPY", qty: int = 10) -> Order:
    return Order(
        id="O1",
        symbol=symbol,
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
        tif=TimeInForce.DAY,
    )


LIMITS = RiskLimits(
    max_position_pct=1.0,
    max_positions=99,
    max_daily_loss_pct=1.0,
    stop_loss_pct=0.0,
    min_cash_buffer_pct=0.02,
)


class TestCashBufferProjection:
    def test_rejects_when_cash_alone_cannot_cover_the_buy(self):
        # 100 equity, 2% buffer -> 98 spendable, but only 50 in cash.
        manager = RiskManager(LIMITS)
        broker = FakeBroker(cash=50.0, equity=100.0)
        assert (
            manager.validate(buy_order(qty=10), broker, 10.0)
            == "minimum cash buffer breached"
        )

    def test_pending_sell_proceeds_make_the_same_buy_affordable(self):
        # The rebalance sold something first; that cash arrives at the same
        # open, before this buy fills. Judging the buy on pre-sell cash is
        # judging tomorrow's condition with tonight's balance.
        manager = RiskManager(LIMITS)
        broker = FakeBroker(cash=50.0, equity=100.0)
        assert (
            manager.validate(
                buy_order(qty=10), broker, 10.0, pending_sell_proceeds=60.0
            )
            is None
        )

    def test_buffer_still_binds_once_proceeds_are_counted(self):
        # 50 + 40 = 90 projected; buying 89 would leave 1 < the 2 buffer.
        manager = RiskManager(LIMITS)
        broker = FakeBroker(cash=50.0, equity=100.0)
        assert (
            manager.validate(
                buy_order(qty=89), broker, 1.0, pending_sell_proceeds=40.0
            )
            == "minimum cash buffer breached"
        )

    def test_default_keeps_existing_behaviour(self):
        # Every existing caller omits the argument and must be unaffected.
        manager = RiskManager(LIMITS)
        broker = FakeBroker(cash=50.0, equity=100.0)
        assert manager.validate(buy_order(qty=10), broker, 10.0) == manager.validate(
            buy_order(qty=10), broker, 10.0, pending_sell_proceeds=0.0
        )

    def test_sells_are_never_gated_by_the_buffer(self):
        manager = RiskManager(LIMITS)
        broker = FakeBroker(cash=0.0, equity=100.0)
        order = buy_order(qty=10)
        order.side = OrderSide.SELL
        assert manager.validate(order, broker, 10.0) is None
```

- [ ] **Step 2: 구현**

`src/tradingbot/risk.py` — `validate`에 키워드 인자를 더하고 버퍼 검사만 바꾼다.
**나머지 검사(일일 손실, 최대 포지션 수, 포지션 상한)는 손대지 않는다.**

```python
    def validate(
        self,
        order: Order,
        broker: BacktestBroker,
        estimated_price: float,
        *,
        pending_sell_proceeds: float = 0.0,
    ) -> str | None:
        ...
        gross = order.qty * estimated_price
        min_cash = equity * self.limits.min_cash_buffer_pct
        # Orders are submitted at the close but fill at the next open, and a
        # rebalance plan's sells are submitted — and fill — before its buys.
        # Judging a buy on pre-sell cash rejects orders that would have
        # settled fine the next morning; that is what removed 17-19% of both
        # sides' orders and contaminated the excess-return comparison.
        # The hard floor stays where it belongs: the broker refuses a fill
        # that cannot be paid for, fees included.
        projected_cash = broker.cash + pending_sell_proceeds - gross
        if projected_cash < min_cash - 1e-9:
            return "minimum cash buffer breached"
        return None
```

- [ ] **Step 3: 테스트 통과 확인 + 기존 리스크 테스트 회귀**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_risk_cash_projection.py tests/test_order_phase_and_rsi.py -v --basetemp="$env:TEMP\pytest_tmp"
```

---

### Task 2: 컨텍스트가 투영을 계산하고 사이징에 반영한다

**Files:**
- Modify: `src/tradingbot/engine/engine.py`
- Test: `tests/test_engine_cash_sizing.py` (신규)

**Interfaces:**
- Produces:
  - `EngineContext.pending_sell_proceeds() -> float` — 열린 매도 주문의 예상 대금 합계
  - `_resolve_qty`가 버퍼를 미리 떼어낸 예산으로 수량을 정한다
  - `_submit`이 `pending_sell_proceeds`를 `validate`에 넘긴다

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_engine_cash_sizing.py`. 합성 피드·브로커로 하루짜리 리밸런싱을 재현한다.
**핵심 시나리오는 "A를 전량 팔고 B를 산다"이며, 지금은 그 매수가 거부된다.**

```python
class TestRotationIsNotRejected:
    def test_full_rotation_submits_the_buy(self):
        # Sell all of A, buy B with the proceeds. Before this fix the buy was
        # validated against pre-sell cash and rejected outright.
        ...
        assert not result.rejected_orders
        assert any(f.symbol == "B" and f.side is OrderSide.BUY for f in result.fills)

    def test_buy_is_sized_to_leave_the_buffer(self):
        # No pending sells: the buy must stop short of the buffer rather than
        # ask for equity * weight and be refused.
        ...
        assert broker.cash >= broker.equity * 0.02 - 1e-9
        assert not result.rejected_orders

    def test_unaffordable_buy_places_no_order_at_all(self):
        # Budget below one share -> qty 0. Assert it is neither filled nor
        # recorded as a rejection: nothing was ever asked for.
        ...
        assert not result.fills
        assert not result.rejected_orders

    def test_weight_still_caps_the_buy_when_cash_is_ample(self):
        # Plenty of cash: sizing must still honour equity * weight, not spend
        # everything available.
        ...
```

`tests/test_smoke_backtest.py`(고정값 회귀)를 **수정하지 않고** 통과시키는 것이 이 태스크의
암묵적 요구다. 그 값이 바뀌면 이 변경이 현금이 넉넉한 경로까지 건드린 것이다.

- [ ] **Step 2: 구현**

```python
    def pending_sell_proceeds(self) -> float:
        """Cash the open sell orders are expected to bring in.

        A rebalance submits its sells and buys together at the close, but
        nothing moves until the next open — where the sells fill first,
        because the broker works its open orders in submission order and
        `plan_rebalance` puts sells first. Sizing and validating a buy on
        pre-sell cash therefore judges tomorrow's condition with tonight's
        balance.

        Priced through `_estimate_price`, the same path the buy itself uses:
        two different estimates here would let sizing and validation disagree
        and reintroduce the rejection from the other side.
        """
        total = 0.0
        for order in self.broker.open_orders():
            if order.side is not OrderSide.SELL:
                continue
            try:
                price = self._estimate_price(
                    order.symbol, order.order_type, order.limit_price, order.stop_price
                )
            except Exception:  # a symbol without a usable bar contributes nothing
                continue
            total += order.qty * price
        return total

    def _spendable_cash(self) -> float:
        """Cash available to buys once the required buffer is set aside."""
        buffer_pct = (
            self.risk_manager.limits.min_cash_buffer_pct
            if self.risk_manager is not None
            else 0.0
        )
        return (
            self.broker.cash
            + self.pending_sell_proceeds()
            - self.equity() * buffer_pct
        )

    def _resolve_qty(self, estimated_price: float, qty: int | None, weight: float | None) -> int:
        if qty is not None:
            return int(qty)
        if weight is None:
            raise ValueError("qty or weight is required")
        # Set the buffer aside before sizing rather than sizing past it and
        # letting the risk check refuse the whole order. A budget short of one
        # share yields no order, which is quieter than a rejection but honest:
        # nothing was affordable, so nothing was asked for.
        budget = min(self.equity() * float(weight), self._spendable_cash())
        if budget <= 0:
            return 0
        return int(budget // estimated_price)
```

`buy()`는 `_resolve_qty`가 0을 돌려주면 주문을 만들지 않고 반환해야 한다. **기존
`_submit`은 `qty <= 0`을 브로커까지 보내 `"quantity must be positive"` 거부로 집계하므로,
그 경로를 타지 않게 한다** — 미주문을 거부로 세면 이번 수정의 측정 목표가 무너진다.

`_submit`은 검증에 같은 값을 넘긴다:

```python
            reason = self.risk_manager.validate(
                order,
                self.broker,
                estimated_price,
                pending_sell_proceeds=self.pending_sell_proceeds(),
            )
```

- [ ] **Step 3: 전체 테스트**

```powershell
.\.venv\Scripts\python.exe -m pytest -v --basetemp="$env:TEMP\pytest_tmp"
```

`tests/test_smoke_backtest.py`가 **수정 없이** 통과해야 한다.

---

### Task 3: 구간 정의를 설정에 확정한다

**Files:**
- Modify: `config/research.toml`
- Test: 없음 (설정값이며, 소비하는 코드는 이미 테스트돼 있다)

- [ ] **Step 1: `[periods]` 수정**

```toml
[periods]
# 2007-01-03이 캐시의 실제 최초 거래일이고, 2008 금융위기는 방어 메커니즘을 검증할 수
# 있는 이 표본 최대의 스트레스 구간이다. 미국 판정문이 유일하게 "설계 의도대로
# 작동했다"고 확인한 것(전략 -9.90% vs SPY -47.12%)이 그 구간에서 나왔는데, 2010년
# 시작은 그것을 표본 밖에 두고 있었다. 뒤쪽 경계는 건드리지 않으므로 누수가 아니다.
in_sample_start = "2007-01-01"
in_sample_end = "2018-12-31"
validation_start = "2019-01-01"
validation_end = "2021-12-31"
out_of_sample_start = "2022-01-01"  # 파라미터 조정 사용 금지
```

- [ ] **Step 2: 2026-08-01 연구가 이 경계를 벗어났다는 사실을 주석으로 남긴다**

`out_of_sample_start` 아래에 덧붙인다:

```toml
# 2026-08-01 팩터 교체 연구는 held-out을 2017~2026 한 덩어리로 잡아 이 경계를 넘었고,
# 그래서 현행 us_asset_rotation 설정은 OOS 예산을 이미 한 번 썼다. 설계
# 2026-08-30 §4.2 참조 — 그 설정을 더 손보며 2022+ 결과를 다시 보는 것은 허용되지 않는다.
```

---

### Task 4: 재기준선 측정 (**사용자 실행** — 데이터와 Windows 환경 필요)

Task 1~3이 끝난 뒤, 데이터 캐시가 있는 PC에서 실행한다. 에이전트 환경에는 `data/cache/`가
없고(`.gitignore`), `uv.lock`이 `sys_platform == 'win32'` 고정이라 여기서는 돌릴 수 없다.

- [ ] **Step 1: 수정 전 수치를 먼저 기록** (비교 대상이 사라지기 전에)

현재 판정문의 값: 초과수익 −2.31%p, Sharpe 0.5197, MDD 0.1886, 회전율 2.7379,
WF 승률 0.2941, 비용 2배 −2.66%p, 거부 129건.

- [ ] **Step 2: 재측정**

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml research evaluate `
  --strategy theme_multifactor --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ `
  --start 2007-01-01 --benchmark-config config\us_etf_benchmark.toml
```

- [ ] **Step 3: 완료 기준 확인**

- 거부 주문이 **전략·벤치마크 양쪽에서 1% 미만**으로 떨어졌는가
- 남은 거부가 있다면 그 사유가 `minimum cash buffer breached`가 **아닌지**

- [ ] **Step 4: `docs/us_etf_rotation_review.md`에 절 추가**

새 문서를 만들지 않는다 — 한 전략의 이력은 한 곳에 모은다. 수정 **전/후 6개 기준을
나란히** 싣고, 거부율 변화를 함께 적는다.

> **판정이 뒤집힐 수 있다.** 초과수익이 양수가 되어도 P1~P3을 건너뛰지 않는다. 승률
> 0.29를 포함한 나머지 기준이 여전히 미달이고, 거부 17%가 사라진 것만으로 승률이 0.6을
> 넘을 근거는 없다 (설계 §5 P0의 경고).

---

## 완료 기준

- [ ] 매도 대금이 반영된 현금 투영으로 버퍼를 판정한다.
- [ ] 사이징과 검증이 **같은** 투영값을 쓴다.
- [ ] 전량 매도 후 매수하는 회전이 거부되지 않고 체결된다.
- [ ] 살 수 없을 때는 주문을 내지 않으며, 그것이 **거부로 집계되지 않는다.**
- [ ] `validate`를 기존처럼 호출하면 동작이 달라지지 않는다.
- [ ] `tests/test_smoke_backtest.py`가 **수정 없이** 통과한다.
- [ ] `[periods]`가 In-sample 2007-01-01 ~ 2018-12-31로 확정되고, OOS 예산 소진 사실이 주석으로 남는다.
- [ ] 재기준선 측정 결과가 판정문에 수정 전/후로 기록된다. (Task 4, 사용자)
- [ ] 전체 테스트 통과.

## 알려진 한계 (범위 밖)

- **체결 모델은 건드리지 않는다.** 슬리피지·부분체결·시가 갭은 M13의 몫이다. 이번 수정은
  제출 시점 투영만 교정한다.
- **매도가 체결되지 못하면 버퍼가 침범될 수 있다.** 그 종목의 바가 없는 경우이며, 현금이
  음수가 되지는 않는다(`backtest.py:182`가 하드 바닥). 버퍼는 원래 체결 시점에 강제되지
  않으므로 불변식이 약해지는 것이 아니다. 체결 시점 버퍼 강제가 필요하다는 판단이 서면
  별도 과제로 다룬다.
- **대기 매도 대금에 헤어컷을 두지 않는다.** 매수 사이징과 같은 추정가를 쓰는 것이
  일관되고, 헤어컷은 튜닝 대상 파라미터를 하나 늘린다. 하드 바닥이 이미 있으므로 필요하지
  않다고 판단했다.
- **한국 전략은 이 수정의 대상이 아니다.** 거부 왜곡이 실측된 것은 미국 표본이다. 한국은
  데이터 기간이 짧아 판정 자체가 불가능하며(설계 §3.4), 2021~2022 패널 수집이 선행이다.
- **유니버스 확장·신규 팩터·틸트는 이 계획에 없다.** P1 이후이며, 설계 §6.3의 유니버스
  결정은 **P1 재측정 결과를 보고** 내리기로 했다.
