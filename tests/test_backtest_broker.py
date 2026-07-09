from __future__ import annotations

from datetime import date

import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.models import Bar, Order, OrderPhase, OrderSide, OrderStatus, OrderType, TimeInForce


def order(symbol="AAA", side=OrderSide.BUY, qty=10, order_type=OrderType.MARKET, **kwargs):
    created_at = kwargs.pop("created_at", date(2020, 1, 1))
    return Order(
        id="O1",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        created_at=created_at,
        **kwargs,
    )


def broker(cash=100_000):
    return BacktestBroker(initial_cash=cash, market="KR", fee_model=FeeModel("KR"), slippage_bps=0)


def test_market_order_fills_at_open():
    b = broker()
    b.submit(order(qty=10))

    fills = b.on_session_open(date(2020, 1, 2), {"AAA": 1_000})

    assert len(fills) == 1
    assert fills[0].price == 1_000
    assert b.position("AAA").qty == 10


def test_buy_stop_gap_up_fills_at_open():
    b = broker()
    b.submit(order(qty=10, order_type=OrderType.STOP, stop_price=100))
    bar = Bar("AAA", date(2020, 1, 2), open=110, high=120, low=105, close=115)

    fills = b.on_intraday_bars(bar.dt, {"AAA": bar})

    assert len(fills) == 1
    assert fills[0].price == 110


def test_buy_limit_gap_down_fills_at_open():
    b = broker()
    b.submit(order(qty=10, order_type=OrderType.LIMIT, limit_price=100))
    bar = Bar("AAA", date(2020, 1, 2), open=90, high=95, low=85, close=92)

    fills = b.on_intraday_bars(bar.dt, {"AAA": bar})

    assert len(fills) == 1
    assert fills[0].price == 90


def test_moc_order_fills_at_close():
    b = broker()
    b.submit(order(qty=10, order_type=OrderType.MOC, created_at=date(2020, 1, 2), created_phase=OrderPhase.OPEN))
    bar = Bar("AAA", date(2020, 1, 2), open=90, high=95, low=85, close=92)

    fills = b.on_session_close(bar.dt, {"AAA": bar})

    assert len(fills) == 1
    assert fills[0].price == 92


def test_day_order_expires_when_not_triggered():
    b = broker()
    submitted = b.submit(order(qty=10, order_type=OrderType.LIMIT, limit_price=80, tif=TimeInForce.DAY))
    bar = Bar("AAA", date(2020, 1, 2), open=100, high=110, low=90, close=100)

    assert b.on_intraday_bars(bar.dt, {"AAA": bar}) == []
    expired = b.expire_day_orders(date(2020, 1, 2))

    assert expired == [submitted]
    assert submitted.status is OrderStatus.EXPIRED


def test_insufficient_cash_rejects_buy():
    b = broker(cash=100)
    submitted = b.submit(order(qty=10, order_type=OrderType.MARKET))

    fills = b.on_session_open(date(2020, 1, 2), {"AAA": 50})

    assert fills == []
    assert submitted.status is OrderStatus.REJECTED
    assert submitted.reject_reason == "insufficient cash"


