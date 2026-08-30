from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import EngineContext
from tradingbot.models import Bar, OrderStatus
from tradingbot.risk import RiskLimits, RiskManager


def sizing_context(*, cash: float = 1_000.0, price: float = 100.0) -> tuple[EngineContext, BacktestBroker]:
    broker = BacktestBroker(initial_cash=cash, market="US")
    risk_manager = RiskManager(
        RiskLimits(
            max_position_pct=1.0,
            min_cash_buffer_pct=0.20,
        )
    )
    context = EngineContext(
        feed=Mock(spec=HistoricalDataFeed),
        broker=broker,
        risk_manager=risk_manager,
    )
    context.set_datetime(date(2020, 1, 1))
    context.set_bars(
        {
            "AAA": Bar(
                symbol="AAA",
                dt=date(2020, 1, 1),
                open=price,
                high=price,
                low=price,
                close=price,
            )
        }
    )
    return context, broker


def test_weight_sizing_reserves_cash_buffer_before_risk_validation():
    context, broker = sizing_context()

    submitted = context.buy("AAA", weight=1.0)

    assert submitted is not None
    assert submitted.qty == 8
    assert submitted.status is OrderStatus.OPEN
    fills = broker.on_session_open(date(2020, 1, 2), {"AAA": 100.0})
    assert len(fills) == 1
    assert fills[0].qty == 8
    assert submitted.status is OrderStatus.FILLED
    assert broker.cash == pytest.approx(200.0)
    assert broker.rejected_orders == []


def test_weight_sizing_below_one_share_creates_no_order_or_rejection():
    context, broker = sizing_context(price=900.0)

    submitted = context.buy("AAA", weight=1.0)

    assert submitted is None
    assert broker.open_orders() == []
    assert broker.rejected_orders == []
    assert broker.fills == []


def test_cash_buffer_risk_backstop_still_rejects_explicit_quantity():
    context, broker = sizing_context()

    submitted = context.buy("AAA", qty=9)

    assert submitted is not None
    assert submitted.status is OrderStatus.REJECTED
    assert submitted.reject_reason == "minimum cash buffer breached"
    assert broker.rejected_orders == [submitted]
    assert broker.open_orders() == []
