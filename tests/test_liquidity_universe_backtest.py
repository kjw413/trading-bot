"""E2E fixture backtest for the liquidity universe.

The unit tests pin the ranking in isolation. This runs the real
`services.run_backtest` pipeline against a computed universe, because that is
where the M1 work found its two real bugs — a daily path that re-fired every
day, and a schedule built from interleaved filings — and both were invisible
to unit tests that were all passing.

Nothing here touches the network. The candidate pool is injected rather than
downloaded, and prices are synthetic.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.data.universe_liquidity import LiquidityUniverse
from tradingbot.services import run_backtest
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

DATA_START = date(2023, 1, 2)
START = date(2024, 3, 4)
DAYS = 400

# Four names with clearly separated liquidity, and momentum that disagrees
# with liquidity — so a bug that confuses the two shows up as the wrong
# holdings rather than as no holdings at all.
FIXTURE = {
    #        start,  end,    volume
    "LIQWIN": (100.0, 200.0, 5_000_000),   # liquid and rising
    "LIQLOSE": (100.0, 60.0, 4_000_000),   # liquid and falling
    "THINWIN": (100.0, 300.0, 10),         # rising but untradeable
    "THINLOSE": (100.0, 50.0, 10),         # neither
}


@pytest.fixture
def env(tmp_path):
    research = tmp_path / "research.toml"
    research.write_text(RESEARCH_TOML, encoding="utf-8")

    cache_root = tmp_path / "cache"
    cache = ParquetCache(cache_root)
    index = pd.bdate_range(start=pd.Timestamp(DATA_START), periods=DAYS)
    for symbol, (start_price, end_price, volume) in FIXTURE.items():
        closes = np.linspace(start_price, end_price, DAYS)
        cache.write(
            "US",
            symbol,
            pd.DataFrame(
                {
                    "open": closes,
                    "high": closes * 1.01,
                    "low": closes * 0.99,
                    "close": closes,
                    "volume": np.full(DAYS, float(volume)),
                },
                index=index,
            ),
        )

    return {
        "backtest": {"initial_cash_us": 1_000_000},
        "data": {"cache_dir": str(cache_root)},
        "fees": {"US": {"commission_rate": 0.0}},
        "risk": {"max_position_pct": 1.0},
        "strategies": {
            "theme_multifactor": {
                "market": "US",
                "research_config": str(research),
                "data_root": str(cache_root),
                "processed_root": str(tmp_path / "processed"),
                "universe": "liquidity",
                "universe_size": 2,
                "universe_min_listing_days": 200,
                "universe_min_dollar_volume": 1_000_000,
                "top_n": 1,
                "weighting": "equal",
            }
        },
    }


def store_for(env) -> ParquetDataStore:
    params = env["strategies"]["theme_multifactor"]
    return ParquetDataStore(ParquetCache(params["data_root"]), "US")


def patched_universe(monkeypatch, candidates):
    """Inject the candidate pool so nothing reaches the listing directory."""
    monkeypatch.setattr(
        "tradingbot.data.listings.UsCommonStockListing.load",
        lambda self, fetch=True: list(candidates),
    )


def backtest(env):
    return run_backtest(
        env,
        market="US",
        symbols=sorted(FIXTURE),
        strategy_name="theme_multifactor",
        start=START.isoformat(),
        end=None,
    )


class TestLiquidityUniverseEndToEnd:
    def test_the_backtest_runs_and_trades(self, env, monkeypatch):
        patched_universe(monkeypatch, FIXTURE)
        result = backtest(env)
        assert result.fills
        assert result.final_equity > 0

    def test_it_buys_the_liquid_winner_not_the_thin_one(self, env, monkeypatch):
        # THINWIN has the strongest momentum by far and must never be bought:
        # the universe screen removes it before the factor ever sees it.
        patched_universe(monkeypatch, FIXTURE)
        bought = {fill.symbol for fill in backtest(env).fills if fill.side.value == "BUY"}
        assert bought == {"LIQWIN"}

    def test_an_illiquid_name_is_never_held(self, env, monkeypatch):
        patched_universe(monkeypatch, FIXTURE)
        traded = {fill.symbol for fill in backtest(env).fills}
        assert "THINWIN" not in traded
        assert "THINLOSE" not in traded

    def test_deterministic_across_runs(self, env, monkeypatch):
        patched_universe(monkeypatch, FIXTURE)
        first = backtest(env)
        second = backtest(env)
        assert first.final_equity == second.final_equity
        assert len(first.fills) == len(second.fills)

    def test_candidates_outside_the_pool_are_ignored(self, env, monkeypatch):
        # A symbol with price data but absent from the pool must not be
        # traded. The pool is the gate, not the cache directory.
        patched_universe(monkeypatch, ["LIQLOSE", "THINWIN", "THINLOSE"])
        bought = {fill.symbol for fill in backtest(env).fills if fill.side.value == "BUY"}
        assert "LIQWIN" not in bought

    def test_an_empty_pool_trades_nothing_rather_than_erroring(self, env, monkeypatch):
        # A listing refresh that returned nothing must not be read as "buy
        # whatever is in the cache".
        patched_universe(monkeypatch, [])
        assert backtest(env).fills == []


class TestUniverseIsRecomputedNotFrozenOnce:
    def test_membership_tracks_liquidity_over_time(self, tmp_path):
        # A name that becomes liquid must enter the universe in a later
        # period, and one that dries up must leave. A universe computed once
        # and reused would pass every test above and still be wrong.
        cache = ParquetCache(tmp_path / "cache")
        index = pd.bdate_range(start=pd.Timestamp(DATA_START), periods=DAYS)
        half = DAYS // 2
        closes = np.full(DAYS, 100.0)

        for symbol, volumes in {
            "FADING": np.concatenate([np.full(half, 5_000_000.0), np.full(DAYS - half, 1.0)]),
            "RISING": np.concatenate([np.full(half, 1.0), np.full(DAYS - half, 5_000_000.0)]),
        }.items():
            cache.write(
                "US",
                symbol,
                pd.DataFrame(
                    {"open": closes, "high": closes, "low": closes, "close": closes,
                     "volume": volumes},
                    index=index,
                ),
            )

        universe = LiquidityUniverse(
            market="US",
            candidates=["FADING", "RISING"],
            data_store=ParquetDataStore(cache, "US"),
            top_n=1,
            min_listing_days=100,
        )
        early = index[half - 5].date()
        late = index[-1].date()
        assert universe.members(early) == ["FADING"]
        assert universe.members(late) == ["RISING"]


class TestStrategyReusesOneUniverse:
    def test_the_universe_is_built_once(self, env, monkeypatch):
        # Rebuilding per call would re-read the listing cache on every bar and
        # discard the ranking cache with it, turning a monthly computation
        # into a daily one over hundreds of candidates.
        calls: list[int] = []
        monkeypatch.setattr(
            "tradingbot.data.listings.UsCommonStockListing.load",
            lambda self, fetch=True: calls.append(1) or list(FIXTURE),
        )
        strategy = ThemeMultifactorStrategy(**env["strategies"]["theme_multifactor"])
        strategy.universe()
        strategy.universe()
        assert len(calls) == 1
