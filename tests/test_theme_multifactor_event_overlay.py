from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.models import Bar, Position
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

EVENTS = pd.DataFrame(
    {
        "date": pd.to_datetime(["2023-01-09", "2023-04-10", "2023-07-10", "2023-10-10"]),
        "symbol": ["005930"] * 4,
        "event_kind": ["provisional"] * 4,
    }
)


class FakeStore:
    """Panel-only store: the overlay path never reads prices."""

    market = "KR"

    def __init__(self, events: pd.DataFrame = EVENTS) -> None:
        self.events = events
        self.queried_as_of: list[date] = []

    def panel(self, dataset, as_of, symbols=None, *, start=None):
        assert dataset == "events"
        self.queried_as_of.append(as_of)
        frame = self.events
        if symbols is not None:
            frame = frame[frame["symbol"].isin({str(s).upper() for s in symbols})]
        return frame.reset_index(drop=True)


class FakeContext:
    def __init__(self, positions: dict[str, int]) -> None:
        self._positions = positions
        self.sells: list[tuple[str, int]] = []

    def position(self, symbol):
        return Position(symbol=symbol, qty=self._positions.get(symbol, 0), avg_price=100.0)

    def equity(self):
        return 1_000_000.0

    def sell(self, symbol, qty, **kwargs):
        self.sells.append((symbol, qty))
        return None

    def buy(self, symbol, **kwargs):  # pragma: no cover - overlay never buys
        raise AssertionError("the event overlay must never buy")


def strategy(**overrides) -> ThemeMultifactorStrategy:
    params = {
        "theme": "ai_semiconductor",
        "market": "KR",
        "event_overlay_window_days": 5,
        "event_overlay_scale": 0.5,
    }
    params.update(overrides)
    strat = ThemeMultifactorStrategy(**params)
    strat._data_store = FakeStore()
    return strat


def bar(symbol: str, day: date) -> Bar:
    return Bar(symbol=symbol, dt=day, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)


class TestEventDaysToNext:
    def test_reads_the_events_panel_as_of_the_date(self):
        strat = strategy()
        days = strat._event_days_to_next(date(2024, 1, 5), ["005930"], strat._data_store)
        assert strat._data_store.queried_as_of == [date(2024, 1, 5)]
        assert days["005930"] is not None

    def test_a_symbol_with_no_events_is_unknown(self):
        strat = strategy()
        days = strat._event_days_to_next(date(2024, 1, 5), ["000660"], strat._data_store)
        assert days["000660"] is None

    def test_an_empty_panel_makes_every_symbol_unknown(self):
        empty = pd.DataFrame(columns=["date", "symbol", "event_kind"])
        strat = strategy()
        strat._data_store = FakeStore(empty)
        days = strat._event_days_to_next(date(2024, 1, 5), ["005930"], strat._data_store)
        assert days == {"005930": None}


class TestApplyEventTrims:
    def test_trims_a_held_name_inside_the_window(self):
        # Estimated next event 2024-01-09; on 2024-01-05 that is 4 days out.
        strat = strategy()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == [("005930", 50)]

    def test_does_not_trim_outside_the_window(self):
        strat = strategy()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2023, 12, 1))
        assert ctx.sells == []

    def test_does_not_trim_the_same_event_twice(self):
        strat = strategy()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        ctx._positions["005930"] = 50
        strat._apply_event_trims(ctx, date(2024, 1, 8))
        assert ctx.sells == [("005930", 50)]

    def test_does_not_re_trim_daily_once_the_estimate_is_overdue(self):
        # Regression. An overdue estimate reads as "due today", so a key built
        # from dt + days_to_event moves with the calendar and lets the same
        # announcement be trimmed again every day — halving the position over
        # and over until nothing is left. Caught by the end-to-end backtest,
        # which showed 45 fills where there should have been 4.
        strat = strategy()
        ctx = FakeContext({"005930": 1024})
        for offset in range(10):
            strat._apply_event_trims(ctx, date(2024, 1, 10 + offset))
            if ctx.sells:
                held = 1024 - sum(qty for _, qty in ctx.sells)
                ctx._positions["005930"] = held
        assert len(ctx.sells) == 1

    def test_a_new_announcement_allows_a_fresh_trim(self):
        # The flip side: the dedup key must not be so stable that the next
        # quarter's announcement goes unprotected.
        strat = strategy()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert len(ctx.sells) == 1

        later = EVENTS.copy()
        later.loc[len(later)] = {
            "date": pd.Timestamp("2024-01-09"),
            "symbol": "005930",
            "event_kind": "provisional",
        }
        strat._data_store = FakeStore(later)
        ctx._positions["005930"] = 50
        # Next estimate is roughly 2024-04-09; trim as it comes into range.
        strat._apply_event_trims(ctx, date(2024, 4, 5))
        assert len(ctx.sells) == 2

    def test_does_nothing_without_a_position(self):
        strat = strategy()
        ctx = FakeContext({})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_a_position_too_small_to_halve_is_left_alone(self):
        strat = strategy()
        ctx = FakeContext({"005930": 1})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_never_liquidates_a_position(self):
        # This overlay trims exposure; it does not exit. An aggressive scale
        # on a small position rounds the retained share count to zero, and
        # selling the lot would be a different action than the one configured.
        strat = strategy(event_overlay_scale=0.1)
        ctx = FakeContext({"005930": 3})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_an_odd_position_keeps_at_least_one_share(self):
        strat = strategy()
        ctx = FakeContext({"005930": 3})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == [("005930", 1)]

    def test_disabled_by_default(self):
        # The default must not change any existing backtest.
        strat = ThemeMultifactorStrategy(theme="ai_semiconductor", market="KR")
        strat._data_store = FakeStore()
        assert strat.params["event_overlay_window_days"] < 0
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_missing_events_panel_trims_nothing(self):
        empty = pd.DataFrame(columns=["date", "symbol", "event_kind"])
        strat = strategy()
        strat._data_store = FakeStore(empty)
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == []

    def test_an_order_failure_does_not_stop_the_other_symbols(self):
        class Failing(FakeContext):
            def sell(self, symbol, qty, **kwargs):
                if symbol == "005930":
                    raise RuntimeError("broker rejected")
                return super().sell(symbol, qty, **kwargs)

        events = pd.concat([EVENTS, EVENTS.assign(symbol="000660")], ignore_index=True)
        strat = strategy()
        strat._data_store = FakeStore(events)
        ctx = Failing({"005930": 100, "000660": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))
        assert ctx.sells == [("000660", 50)]


class TestStatePersistence:
    def test_trims_survive_a_restart(self):
        strat = strategy()
        ctx = FakeContext({"005930": 100})
        strat._apply_event_trims(ctx, date(2024, 1, 5))

        restored = strategy()
        restored.restore_state(strat.snapshot_state())
        ctx2 = FakeContext({"005930": 50})
        restored._apply_event_trims(ctx2, date(2024, 1, 8))
        assert ctx2.sells == []

    def test_snapshot_keeps_the_existing_keys(self):
        state = strategy().snapshot_state()
        assert {"last_seen_date", "last_rebalance_date", "last_targets"} <= set(state)
        assert "event_trims" in state


RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

TARGETS_AS_OF = date(2024, 6, 28)
HISTORY_DAYS = 70


def write_prices(store, symbol: str, start_price: float, end_price: float) -> None:
    import numpy as np

    closes = list(np.linspace(start_price, end_price, HISTORY_DAYS))
    index = pd.bdate_range(end=pd.Timestamp(TARGETS_AS_OF), periods=HISTORY_DAYS)
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0] * HISTORY_DAYS,
            },
            index=index,
        ),
    )


def write_events(store, symbol: str, days: list[str]) -> None:
    from tradingbot.data.panel import PanelStore, attach_metadata

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "symbol": symbol,
            "event_kind": "provisional",
        }
    )
    PanelStore(store.processed_root, "events", "KR").append(
        attach_metadata(
            frame, source="test", available_at=frame["date"], data_version="1"
        )
    )


class TestGenerateTargetsOverlay:
    """The rebalance path must honour the overlay too.

    Without this, a rebalance landing between the daily trim and the
    announcement would compute the full target weight and buy the position
    straight back, undoing the reduction while the event is still ahead.
    """

    def real_store(self, tmp_path):
        from tradingbot.data.cache import ParquetCache
        from tradingbot.data.store import ParquetDataStore

        return ParquetDataStore(
            ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
        )

    def make(self, tmp_path, **overrides):
        path = tmp_path / "research.toml"
        path.write_text(RESEARCH_TOML, encoding="utf-8")
        params = {
            "research_config": str(path),
            "top_n": 2,
            "weighting": "equal",
            "event_overlay_window_days": 5,
            "event_overlay_scale": 0.5,
        }
        params.update(overrides)
        return ThemeMultifactorStrategy(**params)

    def test_target_is_halved_for_a_name_about_to_report(self, tmp_path):
        store = self.real_store(tmp_path)
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        # Quarterly events ending 2024-04-01 put the next one near 2024-07-01,
        # three days after AS_OF.
        write_events(store, "WIN1", ["2023-07-03", "2023-10-02", "2024-01-02", "2024-04-01"])
        targets = self.make(tmp_path).generate_targets(
            TARGETS_AS_OF, ["WIN1", "WIN2"], store
        )
        assert targets["WIN1"] == pytest.approx(0.20)
        assert targets["WIN2"] == pytest.approx(0.40)

    def test_targets_unchanged_when_the_overlay_is_off(self, tmp_path):
        store = self.real_store(tmp_path)
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        write_events(store, "WIN1", ["2023-07-03", "2023-10-02", "2024-01-02", "2024-04-01"])
        targets = self.make(tmp_path, event_overlay_window_days=-1).generate_targets(
            TARGETS_AS_OF, ["WIN1", "WIN2"], store
        )
        assert targets["WIN1"] == pytest.approx(0.40)
        assert targets["WIN2"] == pytest.approx(0.40)

    def test_no_events_panel_leaves_targets_alone(self, tmp_path):
        store = self.real_store(tmp_path)
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        targets = self.make(tmp_path).generate_targets(
            TARGETS_AS_OF, ["WIN1", "WIN2"], store
        )
        assert targets["WIN1"] == pytest.approx(0.40)
        assert targets["WIN2"] == pytest.approx(0.40)


class TestSignalIdIsolation:
    def test_an_event_trim_does_not_block_a_rebalance_sell(self):
        # Both paths can sell the same symbol on the same day. If they shared a
        # signal id the ledger would treat the second as already handled and
        # drop it, silently losing a rebalance order.
        from tradingbot.strategies.signals import make_signal_id

        trim_id = make_signal_id(
            "theme_multifactor:event_trim", date(2024, 1, 5), "005930", "SELL", 0.5
        )
        rebalance_id = make_signal_id(
            "theme_multifactor", date(2024, 1, 5), "005930", "SELL", 0.5
        )
        assert trim_id != rebalance_id


class TestOnBarWiring:
    def test_trims_run_on_a_non_rebalance_day(self, monkeypatch):
        # The whole point of the daily path: an event early in the month must
        # not wait for month-end.
        strat = strategy()
        called: list[date] = []
        monkeypatch.setattr(strat, "_apply_event_trims", lambda ctx, dt: called.append(dt))
        ctx = FakeContext({"005930": 100})
        strat.on_bar(ctx, bar("005930", date(2024, 1, 5)))
        assert called == [date(2024, 1, 5)]

    def test_trims_run_once_per_day_not_once_per_symbol(self, monkeypatch):
        strat = strategy()
        called: list[date] = []
        monkeypatch.setattr(strat, "_apply_event_trims", lambda ctx, dt: called.append(dt))
        ctx = FakeContext({"005930": 100})
        for symbol in ("005930", "000660", "042700"):
            strat.on_bar(ctx, bar(symbol, date(2024, 1, 5)))
        assert called == [date(2024, 1, 5)]
