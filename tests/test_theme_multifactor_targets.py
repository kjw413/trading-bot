from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy

AS_OF = date(2024, 6, 28)
# momentum_3m needs 3*21+1 = 64 closes.
HISTORY_DAYS = 70

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""


@pytest.fixture
def research_config(tmp_path):
    path = tmp_path / "research.toml"
    path.write_text(RESEARCH_TOML, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_prices(
    store, symbol: str, start_price: float, end_price: float, end: date = AS_OF
) -> None:
    closes = list(np.linspace(start_price, end_price, HISTORY_DAYS))
    index = pd.bdate_range(end=pd.Timestamp(end), periods=HISTORY_DAYS)
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


def write_macro(store, closes: list[float]) -> None:
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=len(closes))
    frame = pd.DataFrame({"date": index, "symbol": "kospi", "close": closes})
    PanelStore(store.processed_root, "macro", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


def make_strategy(research_config, **overrides) -> ThemeMultifactorStrategy:
    params = {"research_config": str(research_config), "top_n": 2, "weighting": "equal"}
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


class TestGenerateTargets:
    def test_picks_the_strongest_momentum_names(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)   # +100%
        write_prices(store, "WIN2", 100.0, 150.0)   # +50%
        write_prices(store, "LOSE", 100.0, 80.0)    # -20%
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2", "LOSE"], store
        )
        assert set(targets) == {"WIN1", "WIN2"}

    def test_equal_weighting_respects_cash_buffer(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # 2 names, equal, capped by max_weight 0.40 then total <= 0.98.
        assert targets["WIN1"] == pytest.approx(0.40)
        assert targets["WIN2"] == pytest.approx(0.40)

    def test_no_data_returns_empty_not_orders(self, store, research_config):
        # The freshness gate: nothing scoreable -> no rebalance at all.
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["GHOST"], store
        )
        assert targets == {}

    def test_bear_regime_halves_exposure(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        # Index well below its 200-day mean -> bear.
        write_macro(store, [100.0] * 200 + [50.0])
        targets = make_strategy(research_config, bear_exposure=0.5).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # equal 0.5 -> exposure x0.5 = 0.25 (the 0.40 cap then has nothing to cut).
        assert targets["WIN1"] == pytest.approx(0.25)

    def test_missing_macro_keeps_full_exposure(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )
        # UNKNOWN regime must not silently de-risk.
        assert targets["WIN1"] == pytest.approx(0.40)

    def test_inverse_volatility_prefers_the_calm_name(self, store, research_config):
        write_prices(store, "CALM", 100.0, 140.0)
        # Same total return, wilder path: alternate +/- swings around the trend.
        closes = list(np.linspace(100.0, 140.0, HISTORY_DAYS))
        wild = [c * (1.05 if i % 2 else 0.95) for i, c in enumerate(closes)]
        index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
        store.cache.write(
            "KR",
            "WILD",
            pd.DataFrame(
                {"open": wild, "high": [c * 1.01 for c in wild],
                 "low": [c * 0.99 for c in wild], "close": wild,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )
        targets = make_strategy(
            research_config, weighting="inverse_volatility"
        ).generate_targets(AS_OF, ["CALM", "WILD"], store)
        assert targets["CALM"] > targets["WILD"]

    def test_typo_factor_name_raises_immediately(self, store, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text(
            "[factor_weights]\nmomentum_3m_typo = 1.0\n"
            "[risk_limits]\nmax_position_weight = 0.4\nmin_cash_weight = 0.02\n",
            encoding="utf-8",
        )
        strategy = ThemeMultifactorStrategy(research_config=str(bad))
        # A typo'd factor silently zero-weighted was the Phase 3 review's
        # deferred trap; here it must fail loudly instead.
        with pytest.raises(ValueError, match="momentum_3m_typo"):
            strategy.factor_weights

    def test_empty_universe_returns_empty(self, store, research_config):
        assert make_strategy(research_config).generate_targets(AS_OF, [], store) == {}


class TestStalenessGate:
    """The strategy must not trade on prices the data pipeline stopped updating.

    Spec §10 requires skipping a rebalance rather than acting on stale data;
    the all-NaN score check alone cannot catch this, because a cache frozen a
    week ago still produces perfectly computable factor scores.
    """

    def test_fresh_data_produces_targets(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 150.0)
        assert make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )

    def test_stale_cache_skips_the_rebalance(self, store, research_config):
        # The update job died two weeks ago; scores still compute cleanly.
        stale_end = date(2024, 6, 12)
        write_prices(store, "WIN1", 100.0, 200.0, end=stale_end)
        write_prices(store, "WIN2", 100.0, 150.0, end=stale_end)
        assert (
            make_strategy(research_config).generate_targets(
                AS_OF, ["WIN1", "WIN2"], store
            )
            == {}
        )

    def test_gap_at_the_limit_is_still_traded(self, store, research_config):
        # 2024-06-27 is the trading day right before AS_OF (Fri 2024-06-28).
        write_prices(store, "WIN1", 100.0, 200.0, end=date(2024, 6, 27))
        write_prices(store, "WIN2", 100.0, 150.0, end=date(2024, 6, 27))
        assert make_strategy(research_config, max_staleness_days=1).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )

    def test_gap_one_past_the_limit_is_skipped(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0, end=date(2024, 6, 26))
        write_prices(store, "WIN2", 100.0, 150.0, end=date(2024, 6, 26))
        assert (
            make_strategy(research_config, max_staleness_days=1).generate_targets(
                AS_OF, ["WIN1", "WIN2"], store
            )
            == {}
        )

    def test_one_halted_symbol_does_not_block_the_others(self, store, research_config):
        # Freshness is a property of the pipeline, not of one ticker: a single
        # suspended name must not stop the whole portfolio from rebalancing.
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "HALTED", 100.0, 150.0, end=date(2024, 5, 2))
        assert make_strategy(research_config).generate_targets(
            AS_OF, ["WIN1", "HALTED"], store
        )

    def test_check_can_be_disabled(self, store, research_config):
        stale_end = date(2024, 6, 12)
        write_prices(store, "WIN1", 100.0, 200.0, end=stale_end)
        write_prices(store, "WIN2", 100.0, 150.0, end=stale_end)
        assert make_strategy(research_config, max_staleness_days=-1).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )


class TestExplicitFactorSelection:
    MULTI_TOML = """
[factor_weights]
momentum_3m = 0.5
momentum_6m = 0.5

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""

    @pytest.fixture
    def multi_config(self, tmp_path):
        path = tmp_path / "multi.toml"
        path.write_text(self.MULTI_TOML, encoding="utf-8")
        return path

    def test_default_uses_every_weighted_factor(self, multi_config):
        strategy = ThemeMultifactorStrategy(research_config=str(multi_config))
        assert set(strategy.factor_weights) == {"momentum_3m", "momentum_6m"}

    def test_explicit_list_restricts_the_set(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_3m"]
        )
        # A US run declares momentum-only instead of silently degrading when
        # the flow and value panels come back all-NaN.
        assert set(strategy.factor_weights) == {"momentum_3m"}

    def test_weights_still_come_from_the_config(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_6m"]
        )
        assert strategy.factor_weights["momentum_6m"] == pytest.approx(0.5)

    def test_factor_without_a_weight_raises(self, multi_config):
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_12m"]
        )
        # momentum_12m is a real registered factor but carries no weight here;
        # running it at an implicit zero would be the silent trap Phase 3 closed.
        with pytest.raises(ValueError, match="momentum_12m"):
            strategy.factor_weights

    def test_unregistered_factor_name_still_raises(self, tmp_path):
        path = tmp_path / "typo.toml"
        path.write_text(
            "[factor_weights]\nmomentum_3m_typo = 1.0\n"
            "[risk_limits]\nmax_position_weight = 0.4\nmin_cash_weight = 0.02\n",
            encoding="utf-8",
        )
        strategy = ThemeMultifactorStrategy(
            research_config=str(path), factors=["momentum_3m_typo"]
        )
        with pytest.raises(ValueError, match="momentum_3m_typo"):
            strategy.factor_weights

    def test_unused_config_weights_are_not_an_error(self, multi_config):
        # One config file must be able to carry both markets' weights.
        strategy = ThemeMultifactorStrategy(
            research_config=str(multi_config), factors=["momentum_3m"]
        )
        assert "momentum_6m" not in strategy.factor_weights

    def test_selected_factors_drive_the_targets(self, store, multi_config):
        write_prices(store, "WIN", 100.0, 200.0)
        write_prices(store, "LOSE", 100.0, 80.0)
        targets = ThemeMultifactorStrategy(
            research_config=str(multi_config),
            factors=["momentum_3m"],
            top_n=1,
            weighting="equal",
        ).generate_targets(AS_OF, ["WIN", "LOSE"], store)
        assert set(targets) == {"WIN"}


class TestAbsoluteMomentumFilter:
    def write_falling_then_flat(self, store, symbol: str) -> None:
        """A name well below its own moving average at AS_OF."""
        closes = [200.0] * (HISTORY_DAYS - 10) + [100.0] * 10
        index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
        store.cache.write(
            "KR",
            symbol,
            pd.DataFrame(
                {"open": closes, "high": [c * 1.01 for c in closes],
                 "low": [c * 0.99 for c in closes], "close": closes,
                 "volume": [1000.0] * HISTORY_DAYS},
                index=index,
            ),
        )

    def test_disabled_by_default(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["RISER", "FALLER"], store
        )
        # Default 0 must preserve the Korean strategy's recorded behavior.
        assert set(targets) == {"RISER", "FALLER"}

    def test_excludes_a_name_below_its_moving_average(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60
        ).generate_targets(AS_OF, ["RISER", "FALLER"], store)
        # Relative momentum alone would still buy the least-bad asset.
        assert set(targets) == {"RISER"}

    def test_all_names_filtered_skips_the_rebalance(self, store, research_config):
        self.write_falling_then_flat(store, "FALLER1")
        self.write_falling_then_flat(store, "FALLER2")
        assert (
            make_strategy(research_config, abs_momentum_ma_days=60).generate_targets(
                AS_OF, ["FALLER1", "FALLER2"], store
            )
            == {}
        )

    def test_short_history_is_excluded_not_admitted(self, store, research_config):
        # Long enough to be judged by a 200-day filter window.
        long_closes = list(np.linspace(100.0, 200.0, 250))
        long_index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=250)
        store.cache.write(
            "KR",
            "RISER",
            pd.DataFrame(
                {"open": long_closes, "high": long_closes, "low": long_closes,
                 "close": long_closes, "volume": [1000.0] * 250},
                index=long_index,
            ),
        )
        # Scoreable by momentum_3m (needs 64 closes) and the STRONGER mover,
        # so without the filter it would certainly be picked — but it has too
        # little history to judge, and admitting it would let a new listing
        # bypass the filter entirely.
        short_closes = list(np.linspace(100.0, 300.0, 70))
        short_index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=70)
        store.cache.write(
            "KR",
            "NEW",
            pd.DataFrame(
                {"open": short_closes, "high": short_closes, "low": short_closes,
                 "close": short_closes, "volume": [1000.0] * 70},
                index=short_index,
            ),
        )
        targets = make_strategy(
            research_config, abs_momentum_ma_days=200, top_n=2
        ).generate_targets(AS_OF, ["RISER", "NEW"], store)
        assert set(targets) == {"RISER"}

    def test_fewer_survivors_than_top_n_holds_what_remains(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        self.write_falling_then_flat(store, "FALLER")
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60, top_n=2
        ).generate_targets(AS_OF, ["RISER", "FALLER"], store)
        assert set(targets) == {"RISER"}

    def test_unscoreable_names_stay_excluded(self, store, research_config):
        write_prices(store, "RISER", 100.0, 200.0)
        # GHOST has no data at all, so the factor already scored it NaN; the
        # filter must not resurrect it. (The filter's own missing-data branch
        # is unreachable in practice — a symbol with a score necessarily has
        # price history — so it stays as defensive code, matching the
        # convention in factors/momentum.py.)
        targets = make_strategy(
            research_config, abs_momentum_ma_days=60, top_n=2
        ).generate_targets(AS_OF, ["RISER", "GHOST"], store)
        assert set(targets) == {"RISER"}
