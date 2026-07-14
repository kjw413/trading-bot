from __future__ import annotations

from datetime import date

import pytest

from tradingbot.models import Fill, OrderSide
from tradingbot.strategies.rsi_reversion import RsiReversionStrategy
from tradingbot.strategies.state import JsonStateStore, MemoryStateStore


def make_fill(symbol: str, side: OrderSide) -> Fill:
    return Fill(order_id="O1", symbol=symbol, side=side, qty=1, price=100.0, fee=0.0, dt=date(2020, 1, 2))


class TestJsonStateStore:
    def test_missing_file_loads_empty_state(self, tmp_path):
        store = JsonStateStore(tmp_path / "missing.json")
        assert store.load("rsi_reversion") == {}

    def test_round_trip(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        store.save("rsi_reversion", {"holding_days": {"AAA": 3}})

        reopened = JsonStateStore(tmp_path / "state.json")
        assert reopened.load("rsi_reversion") == {"holding_days": {"AAA": 3}}

    def test_multiple_strategies_share_one_file(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        store.save("a", {"x": 1})
        store.save("b", {"y": 2})
        assert store.load("a") == {"x": 1}
        assert store.load("b") == {"y": 2}

    def test_corrupted_file_raises_instead_of_resetting(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        store = JsonStateStore(path)
        with pytest.raises(ValueError, match="corrupted"):
            store.load("rsi_reversion")

    def test_empty_file_loads_empty_state(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("", encoding="utf-8")
        assert JsonStateStore(path).load("rsi_reversion") == {}

    def test_unexpected_format_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="unexpected format"):
            JsonStateStore(path).load("rsi_reversion")


class TestRsiStatePersistence:
    def test_snapshot_and_restore_round_trip(self):
        strategy = RsiReversionStrategy()
        strategy.holding_days = {"AAA": 4, "BBB": 1}

        restored = RsiReversionStrategy()
        restored.restore_state(strategy.snapshot_state())
        assert restored.holding_days == {"AAA": 4, "BBB": 1}

    def test_restore_from_empty_state(self):
        strategy = RsiReversionStrategy()
        strategy.restore_state({})
        assert strategy.holding_days == {}

    def test_bind_state_store_restores_previous_run(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        first = RsiReversionStrategy()
        first.bind_state_store(store)
        first.on_fill(None, make_fill("AAA", OrderSide.BUY))
        first.holding_days["AAA"] = 7
        first.persist_state()

        # Simulated restart: a fresh instance restores holding days.
        second = RsiReversionStrategy()
        second.bind_state_store(store)
        assert second.holding_days == {"AAA": 7}

    def test_sell_fill_clears_state_in_store(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        strategy = RsiReversionStrategy()
        strategy.bind_state_store(store)
        strategy.on_fill(None, make_fill("AAA", OrderSide.BUY))
        strategy.on_fill(None, make_fill("AAA", OrderSide.SELL))

        restarted = RsiReversionStrategy()
        restarted.bind_state_store(store)
        assert restarted.holding_days == {}

    def test_memory_store_round_trip(self):
        store = MemoryStateStore()
        strategy = RsiReversionStrategy()
        strategy.bind_state_store(store)
        strategy.on_fill(None, make_fill("AAA", OrderSide.BUY))
        assert store.load("rsi_reversion") == {"holding_days": {"AAA": 0}}

    def test_persist_without_store_is_noop(self):
        strategy = RsiReversionStrategy()
        strategy.on_fill(None, make_fill("AAA", OrderSide.BUY))  # must not raise
        assert strategy.holding_days == {"AAA": 0}
