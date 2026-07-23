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


def write_prices(store, symbol: str, start_price: float, end_price: float) -> None:
    closes = list(np.linspace(start_price, end_price, HISTORY_DAYS))
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
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
