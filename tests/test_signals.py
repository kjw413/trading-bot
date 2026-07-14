from __future__ import annotations

from datetime import date

from tradingbot.strategies.signals import SignalLedger, make_signal_id
from tradingbot.strategies.state import JsonStateStore, MemoryStateStore


class TestMakeSignalId:
    def test_contains_all_decision_fields(self):
        signal_id = make_signal_id("etf_momentum", date(2026, 1, 30), "spy", "buy", 0.35)
        assert signal_id == "etf_momentum|2026-01-30|SPY|BUY|0.350000"

    def test_weight_float_noise_is_stable(self):
        a = make_signal_id("s", date(2026, 1, 30), "SPY", "BUY", 0.1 + 0.2)
        b = make_signal_id("s", date(2026, 1, 30), "SPY", "BUY", 0.3)
        assert a == b

    def test_no_weight_uses_placeholder(self):
        signal_id = make_signal_id("s", date(2026, 1, 30), "SPY", "SELL")
        assert signal_id.endswith("|NA")

    def test_different_dates_produce_different_ids(self):
        a = make_signal_id("s", date(2026, 1, 30), "SPY", "BUY", 0.5)
        b = make_signal_id("s", date(2026, 2, 27), "SPY", "BUY", 0.5)
        assert a != b


class TestSignalLedger:
    def test_first_claim_wins_second_is_rejected(self):
        ledger = SignalLedger("etf_momentum", MemoryStateStore())
        signal_id = make_signal_id("etf_momentum", date(2026, 1, 30), "SPY", "BUY", 0.35)
        assert ledger.claim(signal_id) is True
        assert ledger.claim(signal_id) is False

    def test_duplicate_run_after_restart_places_no_order(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        signal_id = make_signal_id("etf_momentum", date(2026, 1, 30), "SPY", "BUY", 0.35)

        orders: list[str] = []

        def run_once() -> None:
            # Simulates one full execution of the same rebalance decision.
            ledger = SignalLedger("etf_momentum", JsonStateStore(tmp_path / "state.json"))
            if ledger.claim(signal_id):
                orders.append(signal_id)

        run_once()
        run_once()  # identical second execution (e.g. crash-restart replay)
        assert len(orders) == 1

    def test_unclaimed_signal_is_not_processed(self):
        ledger = SignalLedger("s", MemoryStateStore())
        assert not ledger.is_processed("anything")

    def test_ledger_without_store_still_dedupes_in_process(self):
        ledger = SignalLedger("s")
        assert ledger.claim("sig") is True
        assert ledger.claim("sig") is False

    def test_trims_oldest_entries_beyond_max(self):
        ledger = SignalLedger("s", MemoryStateStore(), max_entries=3)
        for i in range(5):
            assert ledger.claim(f"sig-{i}")
        assert not ledger.is_processed("sig-0")
        assert not ledger.is_processed("sig-1")
        assert ledger.is_processed("sig-4")

    def test_ledger_namespaces_do_not_collide_with_strategy_state(self, tmp_path):
        store = JsonStateStore(tmp_path / "state.json")
        store.save("etf_momentum", {"last_rebalance": "2026-01-30"})
        ledger = SignalLedger("etf_momentum", store)
        ledger.claim("sig")
        assert store.load("etf_momentum") == {"last_rebalance": "2026-01-30"}
        assert "sig" in store.load("etf_momentum#signals")["processed"]
