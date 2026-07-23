"""E2E fixture backtest for the theme multifactor strategy.

Runs the strategy through the real `services.run_backtest` pipeline (feed,
broker, engine, risk manager) end to end on synthetic-but-realistic data,
pinning the no-lookahead contract: a signal that fires at a month's last
trading day close must fill on the *next* trading day, never the same day.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.services import run_backtest

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.60
min_cash_weight = 0.02
"""

THEMES_TOML = """
[themes.e2e]
name = "E2E"
market = "KR"
members = [
    { symbol = "WIN1", from = "2023-01-01" },
    { symbol = "LOSE", from = "2023-01-01" },
]
"""

# Prices start 2023-12-01 so momentum_3m (64 rows) is fully warmed up by the
# first monthly signal (2024-03-29 close); the backtest itself starts in March.
DATA_START = date(2023, 12, 1)
START = date(2024, 3, 4)
DAYS = 150  # through May: warmup + one full monthly rebalance cycle


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")
    themes = tmp_path / "themes.toml"
    themes.write_text(THEMES_TOML, encoding="utf-8")

    cache_root = tmp_path / "cache"
    cache = ParquetCache(cache_root)
    index = pd.bdate_range(start=pd.Timestamp(DATA_START), periods=DAYS)
    for symbol, end_price in [("WIN1", 200.0), ("LOSE", 80.0)]:
        closes = list(np.linspace(100.0, end_price, DAYS))
        cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [10000.0] * DAYS},
                index=index,
            ),
        )

    config = {
        "backtest": {"initial_cash_kr": 10_000_000},
        "data": {"cache_dir": str(cache_root)},
        "fees": {"KR": {"commission_rate": 0.00015}},
        # The engine-level RiskManager is a separate layer from the strategy's
        # own risk_limits.max_position_weight (RESEARCH_TOML, applied inside
        # apply_constraints): its default max_position_pct is 0.20, which
        # would reject this fixture's top_n=1 equal-weighted ~0.60 target.
        # Raised here so the E2E test exercises the strategy/engine fill
        # pipeline, not the separate position-size risk control.
        "risk": {"max_position_pct": 1.0},
        "strategies": {
            "theme_multifactor": {
                "theme": "e2e",
                "research_config": str(research),
                "themes_path": str(themes),
                "data_root": str(cache_root),
                "processed_root": str(tmp_path / "processed"),
                "top_n": 1,
                "weighting": "equal",
            }
        },
    }
    return config


class TestEndToEndBacktest:
    def test_backtest_runs_and_buys_the_winner(self, env):
        result = run_backtest(
            env,
            market="KR",
            symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor",
            start=START.isoformat(),
            end=None,
        )
        symbols_bought = {fill.symbol for fill in result.fills if fill.side.value == "BUY"}
        assert symbols_bought == {"WIN1"}
        assert result.final_equity > 0

    def test_close_signal_fills_at_next_session_open(self, env):
        result = run_backtest(
            env,
            market="KR",
            symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor",
            start=START.isoformat(),
            end=None,
        )
        first_fill = min(result.fills, key=lambda fill: fill.dt)
        # The first monthly signal fires at March's last trading day close
        # (2024-03-29); the fill must land on the NEXT trading day.
        assert first_fill.dt == date(2024, 4, 1)

    def test_deterministic_across_runs(self, env):
        first = run_backtest(
            env, market="KR", symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor", start=START.isoformat(), end=None,
        )
        second = run_backtest(
            env, market="KR", symbols=["WIN1", "LOSE"],
            strategy_name="theme_multifactor", start=START.isoformat(), end=None,
        )
        assert first.final_equity == second.final_equity
        assert len(first.fills) == len(second.fills)
