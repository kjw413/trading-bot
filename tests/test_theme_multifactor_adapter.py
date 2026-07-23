from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.models import Bar, Position
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

# 2024-06-28 is the last KR trading day of June (Friday) -> monthly rebalance day.
REBALANCE_DAY = date(2024, 6, 28)
MID_MONTH_DAY = date(2024, 6, 14)
HISTORY_DAYS = 70

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

THEMES_TOML = """
[themes.test_theme]
name = "테스트"
market = "KR"
members = [
    { symbol = "WIN1", from = "2023-01-01" },
    { symbol = "WIN2", from = "2023-01-01" },
    { symbol = "LOSE", from = "2023-01-01" },
]
"""


class FakeContext:
    """Records orders; enough surface for the adapter, no engine needed."""

    def __init__(self, equity: float = 1_000_000.0):
        self._equity = equity
        self.positions: dict[str, Position] = {}
        self.orders: list[tuple] = []

    def history(self, symbol, n):
        raise AssertionError("adapter must use its own data store, not ctx.history")

    def position(self, symbol):
        return self.positions.get(symbol, Position(symbol=symbol))

    def cash(self):
        return self._equity

    def equity(self):
        return self._equity

    def has_open_order(self, symbol, side=None):
        return False

    def buy(self, symbol, qty=None, weight=None, **kwargs):
        self.orders.append(("BUY", symbol, qty, weight))

    def sell(self, symbol, qty, **kwargs):
        self.orders.append(("SELL", symbol, qty, None))


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")
    themes = tmp_path / "themes.toml"
    themes.write_text(THEMES_TOML, encoding="utf-8")

    cache = ParquetCache(tmp_path / "cache")
    for symbol, end_price in [("WIN1", 200.0), ("WIN2", 150.0), ("LOSE", 80.0)]:
        closes = list(np.linspace(100.0, end_price, HISTORY_DAYS))
        index = pd.bdate_range(end=pd.Timestamp(REBALANCE_DAY), periods=HISTORY_DAYS)
        cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )
    return {"research": research, "themes": themes, "root": tmp_path}


def make_strategy(env, **overrides) -> ThemeMultifactorStrategy:
    params = {
        "theme": "test_theme",
        "research_config": str(env["research"]),
        "themes_path": str(env["themes"]),
        "data_root": str(env["root"] / "cache"),
        "processed_root": str(env["root"] / "processed"),
        "top_n": 2,
        "weighting": "equal",
    }
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


def bar(symbol: str, dt: date) -> Bar:
    return Bar(symbol=symbol, dt=dt, open=100.0, high=101.0, low=99.0, close=100.0)


class TestOnBarAdapter:
    def test_rebalance_day_places_buy_orders(self, env):
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        sides_symbols = {(side, symbol) for side, symbol, *_ in ctx.orders}
        assert ("BUY", "WIN1") in sides_symbols
        assert ("BUY", "WIN2") in sides_symbols
        assert all(symbol != "LOSE" for _, symbol, *_ in ctx.orders)

    def test_runs_once_per_day_despite_per_symbol_calls(self, env):
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        first_count = len(ctx.orders)
        strategy.on_bar(ctx, bar("WIN2", REBALANCE_DAY))
        strategy.on_bar(ctx, bar("LOSE", REBALANCE_DAY))
        assert len(ctx.orders) == first_count

    def test_mid_month_day_is_a_no_op(self, env):
        ctx = FakeContext()
        make_strategy(env).on_bar(ctx, bar("WIN1", MID_MONTH_DAY))
        assert ctx.orders == []

    def test_rerun_of_the_same_decision_is_idempotent(self, env):
        # A paper-trading restart replays the same day; the ledger must
        # swallow the duplicate orders even when the day gate reopens.
        ctx = FakeContext()
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        first = list(ctx.orders)

        strategy._last_seen_date = None  # force the day gate open again
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ctx.orders == first  # ledger rejected every duplicate claim

    def test_trims_and_exits_existing_positions(self, env):
        ctx = FakeContext()
        # Holding LOSE (not selected) -> full exit sell before buys.
        ctx.positions["LOSE"] = Position(symbol="LOSE", qty=50, avg_price=100.0, last_price=100.0)
        strategy = make_strategy(env)
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ("SELL", "LOSE", 50, None) in ctx.orders
        sell_index = ctx.orders.index(("SELL", "LOSE", 50, None))
        buy_indices = [i for i, order in enumerate(ctx.orders) if order[0] == "BUY"]
        assert all(sell_index < i for i in buy_indices)

    def test_no_scoreable_data_skips_without_orders(self, env, tmp_path):
        ctx = FakeContext()
        strategy = make_strategy(env, data_root=str(tmp_path / "empty_cache"))
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        assert ctx.orders == []

    def test_state_round_trip(self, env):
        strategy = make_strategy(env)
        ctx = FakeContext()
        strategy.on_bar(ctx, bar("WIN1", REBALANCE_DAY))
        state = strategy.snapshot_state()
        assert state["last_rebalance_date"] == REBALANCE_DAY.isoformat()

        restored = make_strategy(env)
        restored.restore_state(state)
        assert restored._last_rebalance_date == REBALANCE_DAY
        assert restored._last_targets


class TestRegistration:
    def test_registered_in_strategy_registry(self):
        from tradingbot.strategies.registry import get_strategy, list_strategies

        assert "theme_multifactor" in list_strategies()
        assert get_strategy("theme_multifactor") is ThemeMultifactorStrategy

    def test_default_config_section_exists(self):
        from tradingbot.config import load_config

        config = load_config()
        assert "theme_multifactor" in config.get("strategies", {})
