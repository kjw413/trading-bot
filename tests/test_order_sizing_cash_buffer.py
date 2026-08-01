"""주문 사이징이 최소 현금 버퍼를 미리 떼어두는지 검증한다.

설계: docs/superpowers/specs/2026-08-01-order-sizing-cash-buffer-design.md

이 엔진에서 종가에 낸 주문은 다음 날 시가에 체결되고, 매수를 먹여살릴 매도는
같은 주문장에 한 칸 앞서 줄 서 있다. 그래서 "지금 통장에 얼마 있나"가 아니라
"체결 시점에 얼마 있겠나"로 심사해야 한다. 여기 있는 테스트는 그 계약을
고정한다 — 자금이 조달된 로테이션은 통과하고, 같은 현금을 두 번 쓰지 않으며,
버퍼는 끝까지 남는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.engine.engine import EngineContext
from tradingbot.models import Bar, Order, OrderPhase, OrderSide, OrderStatus
from tradingbot.risk import RiskLimits, RiskManager

BUFFER = 0.02
CASH = 100_000.0


class _Feed:
    """The context only reaches for the feed when a price is not in bars."""

    symbols = ["AAA", "BBB", "CCC"]

    def history(self, symbol, dt, n, include_current=False):  # pragma: no cover
        raise AssertionError(f"unexpected feed lookup for {symbol}")


def _bars(dt, prices):
    return {
        symbol: Bar(symbol=symbol, dt=dt, open=price, high=price, low=price, close=price, volume=1e6)
        for symbol, price in prices.items()
    }


@pytest.fixture
def env():
    """US market, no slippage/fees unless a test asks — keeps arithmetic exact."""
    broker = BacktestBroker(initial_cash=CASH, market="US", fee_model=FeeModel("US"), slippage_bps=0)
    risk = RiskManager(
        RiskLimits(max_position_pct=1.0, max_positions=11, min_cash_buffer_pct=BUFFER)
    )
    context = EngineContext(_Feed(), broker, risk)
    return context, broker


def _open_close(context, broker, dt, prices):
    """Settle the book at dt's open, then hand the context dt's close."""
    broker.on_session_open(dt, prices)
    broker.mark_to_market(prices)
    context.set_datetime(dt)
    context.set_bars(_bars(dt, prices))
    context.set_phase(OrderPhase.CLOSE)


class TestFundedRotationSurvives:
    def test_rotation_out_of_a_fully_invested_book_is_not_rejected(self, env):
        """The regression: sell A, buy B on the same close.

        `apply_constraints` caps targets at 1 - buffer, so a fully invested
        account sits at *exactly* the buffer. Judging the buy against today's
        balance made `cash - gross < equity * buffer` true for every buy, and
        the sell still filled the next morning — leaving the portfolio in cash
        for a whole rebalance period.
        """
        context, broker = env
        prices = {"AAA": 100.0, "BBB": 50.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)
        context.buy("AAA", weight=0.98)

        _open_close(context, broker, date(2024, 2, 1), prices)
        assert broker.cash == pytest.approx(CASH * BUFFER)  # sitting on the buffer

        sell = context.sell("AAA", qty=broker.position("AAA").qty)
        buy = context.buy("BBB", weight=0.98)

        assert sell.status is OrderStatus.OPEN
        assert buy.status is OrderStatus.OPEN
        assert buy.qty > 0
        assert broker.rejected_orders == []

    def test_the_rotation_actually_settles(self, env):
        context, broker = env
        prices = {"AAA": 100.0, "BBB": 50.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)
        context.buy("AAA", weight=0.98)

        _open_close(context, broker, date(2024, 2, 1), prices)
        context.sell("AAA", qty=broker.position("AAA").qty)
        context.buy("BBB", weight=0.98)
        _open_close(context, broker, date(2024, 2, 2), prices)

        assert broker.position("AAA").qty == 0
        assert broker.position("BBB").qty > 0
        assert broker.rejected_orders == []


class TestBufferSurvivesSizing:
    def test_cash_never_falls_below_the_buffer(self, env):
        context, broker = env
        prices = {"AAA": 100.0, "BBB": 50.0, "CCC": 25.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)
        for symbol in ("AAA", "BBB", "CCC"):
            context.buy(symbol, weight=0.98 / 3)

        _open_close(context, broker, date(2024, 2, 1), prices)

        assert broker.cash >= broker.equity * BUFFER

    def test_same_day_buys_do_not_spend_the_same_cash_twice(self, env):
        """Three buys of 0.4 each want 1.2x the account; the book is the limit.

        Every buy used to be measured against the same untouched balance, so
        all three "fit" and the overspend only surfaced at fill time. Now each
        one sees the commitments queued ahead of it, and the third is sized
        down to what is left rather than rejected.
        """
        context, broker = env
        prices = {"AAA": 100.0, "BBB": 50.0, "CCC": 25.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)

        orders = [context.buy(symbol, weight=0.4) for symbol in ("AAA", "BBB", "CCC")]

        assert [o.qty for o in orders] == [400, 800, 720]  # 40k + 40k + 18k = 98k
        assert orders[-1].qty < int(CASH * 0.4 // prices["CCC"])  # the last one got clipped
        committed = sum(o.qty * prices[o.symbol] for o in orders)
        assert committed == pytest.approx(CASH * (1 - BUFFER))

        _open_close(context, broker, date(2024, 2, 1), prices)
        assert broker.cash == pytest.approx(broker.equity * BUFFER)
        assert broker.rejected_orders == []

    def test_a_buy_with_nothing_left_stays_visible_as_a_rejection(self, env):
        """Clipping must not hide 'the strategy wanted in and could not get in'.

        Sizing to zero shares still reaches the broker, which rejects it — so
        the fact survives in `rejected_orders` instead of evaporating.
        """
        context, broker = env
        prices = {"AAA": 100.0, "CCC": 25.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)

        context.buy("AAA", weight=0.98)
        starved = context.buy("CCC", weight=0.5)

        assert starved.qty == 0
        assert starved.status is OrderStatus.REJECTED
        assert starved.reject_reason == "quantity must be positive"

    def test_slippage_and_fees_come_out_of_the_budget(self):
        """A weight-sized buy must not overspend the weight it asked for."""
        broker = BacktestBroker(
            initial_cash=CASH,
            market="US",
            fee_model=FeeModel("US", commission_rate=0.001),
            slippage_bps=50.0,
        )
        risk = RiskManager(RiskLimits(max_position_pct=1.0, min_cash_buffer_pct=BUFFER))
        context = EngineContext(_Feed(), broker, risk)
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), {"AAA": 100.0}))
        context.set_phase(OrderPhase.CLOSE)

        order = context.buy("AAA", weight=0.5)

        _open_close(context, broker, date(2024, 2, 1), {"AAA": 100.0})
        spent = CASH - broker.cash
        assert order.qty > 0
        assert spent <= CASH * 0.5 + 1e-9


class TestExplicitQuantityKeepsItsSemantics:
    def test_explicit_qty_that_breaches_the_buffer_is_still_rejected(self, env):
        """`qty=` is an instruction, not a request: it is not silently clipped."""
        context, broker = env
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), {"AAA": 100.0}))
        context.set_phase(OrderPhase.CLOSE)

        order = context.buy("AAA", qty=1_000)  # 100,000 of a 100,000 account

        assert order.status is OrderStatus.REJECTED
        assert order.reject_reason == "minimum cash buffer breached"

    def test_explicit_qty_inside_the_buffer_passes(self, env):
        context, broker = env
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), {"AAA": 100.0}))
        context.set_phase(OrderPhase.CLOSE)

        order = context.buy("AAA", qty=900)

        assert order.status is OrderStatus.OPEN


class TestProjectedCash:
    def test_pending_sell_is_credited_at_its_estimate(self, env):
        context, broker = env
        prices = {"AAA": 100.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)
        context.buy("AAA", weight=0.98)
        _open_close(context, broker, date(2024, 2, 1), prices)

        held = broker.position("AAA").qty
        context.sell("AAA", qty=held)

        assert broker.projected_cash() == pytest.approx(broker.cash + held * 100.0)

    def test_a_sell_larger_than_the_position_only_credits_what_is_held(self, env):
        """`_fillable_qty` clamps a sell to the position; the projection must too,
        or an oversized sell would fund a buy with shares that do not exist."""
        context, broker = env
        prices = {"AAA": 100.0}
        context.set_datetime(date(2024, 1, 31))
        context.set_bars(_bars(date(2024, 1, 31), prices))
        context.set_phase(OrderPhase.CLOSE)
        context.buy("AAA", weight=0.5)
        _open_close(context, broker, date(2024, 2, 1), prices)

        held = broker.position("AAA").qty
        context.sell("AAA", qty=held * 10)

        assert broker.projected_cash() == pytest.approx(broker.cash + held * 100.0)

    def test_an_order_without_a_price_estimate_contributes_nothing(self, env):
        """Under-counting a pending sell is safe; over-counting is not."""
        _, broker = env
        broker.submit(
            Order(id="O99", symbol="AAA", side=OrderSide.SELL, qty=10, created_at=date(2024, 1, 31))
        )

        assert broker.projected_cash() == pytest.approx(broker.cash)


class TestAffordableQty:
    def test_returns_what_the_budget_actually_covers(self):
        broker = BacktestBroker(
            initial_cash=CASH, market="US", fee_model=FeeModel("US", commission_rate=0.001),
            slippage_bps=50.0,
        )
        qty = broker.affordable_qty(100.0, 10_000.0)
        price = broker._execution_price(100.0, OrderSide.BUY)
        cost = price * qty + broker.fee_model.calculate(OrderSide.BUY, qty, price)

        assert qty > 0
        assert cost <= 10_000.0 + 1e-9
        # and one more share would not have fit
        next_cost = price * (qty + 1) + broker.fee_model.calculate(OrderSide.BUY, qty + 1, price)
        assert next_cost > 10_000.0

    @pytest.mark.parametrize("budget", [0.0, -1.0])
    def test_no_budget_buys_nothing(self, budget):
        broker = BacktestBroker(initial_cash=CASH, market="US", fee_model=FeeModel("US"))
        assert broker.affordable_qty(100.0, budget) == 0
