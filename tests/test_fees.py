from __future__ import annotations

import pytest

from tradingbot.broker.fees import FeeModel, round_execution_price, tick_size
from tradingbot.models import OrderSide


def test_kr_sell_fee_includes_commission_and_tax():
    model = FeeModel("KR", commission_rate=0.00015, sell_tax_rate=0.0015)

    fee = model.calculate(OrderSide.SELL, qty=10, price=70_000)

    assert fee == pytest.approx(1_155.0)


def test_us_sell_fee_includes_sec_fee_and_taf_cap():
    model = FeeModel(
        "US",
        commission_rate=0.0,
        sec_fee_rate=0.0000278,
        finra_taf_per_share=0.000166,
        finra_taf_cap=8.30,
    )

    fee = model.calculate(OrderSide.SELL, qty=100_000, price=10.0)

    assert fee == pytest.approx(27.8 + 8.30)


def test_execution_price_rounds_against_trader():
    assert tick_size("KR", 66_501) == 100
    assert round_execution_price("KR", 66_501, OrderSide.BUY) == 66_600
    assert round_execution_price("KR", 66_501, OrderSide.SELL) == 66_500
    assert round_execution_price("US", 10.001, OrderSide.BUY) == pytest.approx(10.01)
    assert round_execution_price("US", 10.009, OrderSide.SELL) == pytest.approx(10.00)
