from __future__ import annotations

from datetime import date

import pytest

from tradingbot.broker.base import Broker
from tradingbot.broker.kis import LIVE_BASE_URL, PAPER_BASE_URL, KISBroker, KISConfig
from tradingbot.models import Order, OrderSide


def make_broker(paper: bool = True) -> KISBroker:
    return KISBroker(KISConfig(app_key="k", app_secret="s", account_no="12345678-01", paper=paper))


def test_kis_config_selects_paper_and_live_domains():
    assert KISConfig("k", "s", "12345678-01", paper=True).base_url == PAPER_BASE_URL
    assert KISConfig("k", "s", "12345678-01", paper=False).base_url == LIVE_BASE_URL


def test_kis_config_splits_account_number():
    config = KISConfig("k", "s", "12345678-01")
    assert config.cano == "12345678"
    assert config.acnt_prdt_cd == "01"


def test_kis_broker_satisfies_broker_contract_but_is_unimplemented():
    broker = make_broker()
    assert isinstance(broker, Broker)

    order = Order(id="O1", symbol="005930", side=OrderSide.BUY, qty=1)
    with pytest.raises(NotImplementedError):
        broker.submit(order)
    with pytest.raises(NotImplementedError):
        broker.cancel("O1")
    with pytest.raises(NotImplementedError):
        broker.open_orders()
    with pytest.raises(NotImplementedError):
        broker.on_session_open(date(2026, 1, 2), {})
    with pytest.raises(NotImplementedError):
        broker.position("005930")
    with pytest.raises(NotImplementedError):
        broker.cash
    with pytest.raises(NotImplementedError):
        broker.equity
