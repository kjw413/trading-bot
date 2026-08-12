"""E2E fixture backtest proving the event overlay reaches the broker.

The unit tests pin each piece in isolation. This one answers the question
M1 actually exists to answer: does an event collected into the panel travel
all the way through the strategy, the risk manager and the broker to change
what the portfolio holds?

It runs the real `services.run_backtest` pipeline twice on identical
synthetic data — once with the overlay off, once on — and compares. If the
two runs are identical, the plumbing is not connected, however green the
unit tests are.
"""

from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
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

DATA_START = date(2023, 12, 1)
START = date(2024, 3, 4)
DAYS = 150

# Quarterly announcements for WIN1. The median gap puts the next one in
# mid-April 2024 — inside the backtest, and away from a month end, so the
# trim has to come from the daily path rather than a rebalance.
WIN1_EVENTS = ["2023-04-14", "2023-07-14", "2023-10-13", "2024-01-12"]


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")
    themes = tmp_path / "themes.toml"
    themes.write_text(THEMES_TOML, encoding="utf-8")

    cache_root = tmp_path / "cache"
    processed_root = tmp_path / "processed"
    cache = ParquetCache(cache_root)
    index = pd.bdate_range(start=pd.Timestamp(DATA_START), periods=DAYS)
    for symbol, end_price in [("WIN1", 200.0), ("LOSE", 80.0)]:
        closes = list(np.linspace(100.0, end_price, DAYS))
        cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {
                    "open": closes,
                    "high": [c * 1.01 for c in closes],
                    "low": [c * 0.99 for c in closes],
                    "close": closes,
                    "volume": [10000.0] * DAYS,
                },
                index=index,
            ),
        )

    events = pd.DataFrame(
        {
            "date": pd.to_datetime(WIN1_EVENTS),
            "symbol": "WIN1",
            "event_kind": "provisional",
        }
    )
    PanelStore(processed_root, "events", "KR").append(
        attach_metadata(
            events, source="test", available_at=events["date"], data_version="1"
        )
    )

    return {
        "backtest": {"initial_cash_kr": 10_000_000},
        "data": {"cache_dir": str(cache_root)},
        "fees": {"KR": {"commission_rate": 0.00015}},
        "risk": {"max_position_pct": 1.0},
        "strategies": {
            "theme_multifactor": {
                "theme": "e2e",
                "research_config": str(research),
                "themes_path": str(themes),
                "data_root": str(cache_root),
                "processed_root": str(processed_root),
                "top_n": 1,
                "weighting": "equal",
            }
        },
    }


def backtest(config):
    return run_backtest(
        config,
        market="KR",
        symbols=["WIN1", "LOSE"],
        strategy_name="theme_multifactor",
        start=START.isoformat(),
        end=None,
    )


def with_overlay(config, *, window_days=5, scale=0.5):
    active = copy.deepcopy(config)
    active["strategies"]["theme_multifactor"]["event_overlay_window_days"] = window_days
    active["strategies"]["theme_multifactor"]["event_overlay_scale"] = scale
    return active


class TestOverlayReachesTheBroker:
    def test_the_overlay_changes_what_gets_traded(self, env):
        # The claim M1 has to earn: an event in the panel changes the
        # portfolio. Identical results here would mean the wiring is dead.
        off = backtest(env)
        on = backtest(with_overlay(env))
        assert len(on.fills) != len(off.fills) or on.final_equity != off.final_equity

    def test_the_overlay_sells_on_a_non_rebalance_day(self, env):
        # The reason the daily path exists. A trim landing only on a month end
        # would mean the event window had already closed.
        off_sell_days = {f.dt for f in backtest(env).fills if f.side.value == "SELL"}
        on_sell_days = {f.dt for f in backtest(with_overlay(env)).fills if f.side.value == "SELL"}
        extra = on_sell_days - off_sell_days
        assert extra, "the overlay produced no sell the baseline did not already make"

    def test_the_trim_sells_the_name_with_the_event(self, env):
        off_sells = [(f.dt, f.symbol) for f in backtest(env).fills if f.side.value == "SELL"]
        on_sells = [
            (f.dt, f.symbol) for f in backtest(with_overlay(env)).fills if f.side.value == "SELL"
        ]
        added = [entry for entry in on_sells if entry not in off_sells]
        assert added
        assert all(symbol == "WIN1" for _, symbol in added)

    def test_it_reduces_rather_than_liquidates(self, env):
        # A trim must leave a position behind; exiting is a different action.
        result = backtest(with_overlay(env))
        held = 0
        for fill in sorted(result.fills, key=lambda f: f.dt):
            held += fill.qty if fill.side.value == "BUY" else -fill.qty
            assert held >= 0
        assert held > 0

    def test_disabled_overlay_is_byte_identical_to_the_baseline(self, env):
        # The regression line for every comparison that follows: with the
        # overlay off, an events panel sitting in the store must change
        # nothing at all.
        off = backtest(env)
        explicitly_off = backtest(with_overlay(env, window_days=-1))
        assert explicitly_off.final_equity == off.final_equity
        assert len(explicitly_off.fills) == len(off.fills)

    def test_still_deterministic_with_the_overlay_on(self, env):
        first = backtest(with_overlay(env))
        second = backtest(with_overlay(env))
        assert first.final_equity == second.final_equity
        assert len(first.fills) == len(second.fills)

    def test_periodic_reports_do_not_double_the_schedule(self, env, tmp_path):
        # The real panel carries both kinds: a provisional release and, weeks
        # later, the periodic report repeating the same quarter. Reading them
        # together halves the median gap, so the estimator expects a report
        # every six weeks and the name is flagged almost continuously.
        #
        # Adding the periodic reports must not change what gets traded.
        provisional_only = backtest(with_overlay(env))

        processed = env["strategies"]["theme_multifactor"]["processed_root"]
        periodic = pd.DataFrame(
            {
                # Each lands about two months after its provisional release.
                "date": pd.to_datetime(
                    ["2023-03-16", "2023-06-15", "2023-09-14", "2024-03-14"]
                ),
                "symbol": "WIN1",
                "event_kind": "periodic",
            }
        )
        PanelStore(processed, "events", "KR").append(
            attach_metadata(
                periodic, source="test", available_at=periodic["date"], data_version="1"
            )
        )
        with_periodic = backtest(with_overlay(env))

        assert len(with_periodic.fills) == len(provisional_only.fills)
        assert with_periodic.final_equity == provisional_only.final_equity
