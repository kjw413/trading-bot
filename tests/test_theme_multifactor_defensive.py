"""Going defensive: what the strategy does when nothing is worth holding.

Two behaviours meet here.

The defect: when every name failed its absolute-momentum test, the strategy
returned the same empty dict it uses for "the data is stale", and the caller
skipped the rebalance — carrying the existing risky book straight through the
decline the filter exists to sidestep. 2022 is the case that matters, when
stocks and bonds fell together and nothing passed.

The enhancement: `research.toml` declares `safe_asset = "IEF"` and nothing
read it, so weight the regime filter took off the table sat in cash earning
zero. It can now be parked — but only while the safe asset is itself in an
uptrend, because a falling bond fund is not a refuge either.
"""

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
DAYS = 260  # enough for a 200-day moving average

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
    closes = list(np.linspace(start_price, end_price, DAYS))
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=DAYS)
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0] * DAYS,
            },
            index=index,
        ),
    )


def write_macro(store, start: float, end: float) -> None:
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=DAYS)
    frame = pd.DataFrame(
        {"date": index, "symbol": "kospi", "close": list(np.linspace(start, end, DAYS))}
    )
    PanelStore(store.processed_root, "macro", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


def make_strategy(research_config, **overrides) -> ThemeMultifactorStrategy:
    params = {
        "research_config": str(research_config),
        "top_n": 2,
        "weighting": "equal",
        "abs_momentum_ma_days": 200,
        "regime_series": "kospi",
    }
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


class TestEverythingBelowTrend:
    def test_liquidates_instead_of_skipping(self, store, research_config):
        """The regression. `{}` is a decision to hold nothing; `None` would
        have meant 'cannot judge' and left the risky book in place."""
        write_prices(store, "AAA", 200.0, 100.0)
        write_prices(store, "BBB", 200.0, 120.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(research_config).generate_targets(
            AS_OF, ["AAA", "BBB"], store
        )

        assert targets == {}

    def test_parks_in_the_safe_asset_when_it_is_holding_up(self, store, research_config):
        write_prices(store, "AAA", 200.0, 100.0)
        write_prices(store, "BBB", 200.0, 120.0)
        write_prices(store, "SAFE", 100.0, 130.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(research_config, safe_asset="SAFE").generate_targets(
            AS_OF, ["AAA", "BBB"], store
        )

        assert set(targets) == {"SAFE"}
        assert targets["SAFE"] == pytest.approx(0.40)  # max_position_weight

    def test_holds_cash_when_the_safe_asset_is_falling_too(self, store, research_config):
        """2022: stocks and bonds together. A falling refuge is not a refuge."""
        write_prices(store, "AAA", 200.0, 100.0)
        write_prices(store, "BBB", 200.0, 120.0)
        write_prices(store, "SAFE", 130.0, 100.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(research_config, safe_asset="SAFE").generate_targets(
            AS_OF, ["AAA", "BBB"], store
        )

        assert targets == {}

    def test_missing_safe_asset_history_falls_back_to_cash(self, store, research_config):
        write_prices(store, "AAA", 200.0, 100.0)
        write_prices(store, "BBB", 200.0, 120.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(research_config, safe_asset="ABSENT").generate_targets(
            AS_OF, ["AAA", "BBB"], store
        )

        assert targets == {}


class TestRegimeFreedWeight:
    def test_freed_weight_goes_to_the_safe_asset(self, store, research_config):
        """Bear regime halves exposure; the other half need not sit idle."""
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 180.0)
        write_prices(store, "SAFE", 100.0, 130.0)
        write_macro(store, 3000.0, 2000.0)  # bear: close below its own average

        targets = make_strategy(
            research_config, safe_asset="SAFE", bear_exposure=0.5
        ).generate_targets(AS_OF, ["WIN1", "WIN2"], store)

        assert "SAFE" in targets
        assert targets["SAFE"] == pytest.approx(0.40)  # 0.5 freed, capped at max weight

    def test_without_a_safe_asset_the_freed_weight_stays_in_cash(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 180.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(research_config, bear_exposure=0.5).generate_targets(
            AS_OF, ["WIN1", "WIN2"], store
        )

        assert set(targets) == {"WIN1", "WIN2"}
        assert sum(targets.values()) == pytest.approx(0.5)

    def test_a_bull_regime_frees_nothing(self, store, research_config):
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 180.0)
        write_prices(store, "SAFE", 100.0, 130.0)
        write_macro(store, 2000.0, 3000.0)  # bull

        targets = make_strategy(
            research_config, safe_asset="SAFE", bear_exposure=0.5
        ).generate_targets(AS_OF, ["WIN1", "WIN2"], store)

        assert "SAFE" not in targets

    def test_safe_asset_already_selected_is_topped_up_not_duplicated(
        self, store, research_config
    ):
        """SAFE is in the universe and wins on momentum; the freed weight adds
        to the position it already has rather than creating a second one."""
        write_prices(store, "SAFE", 100.0, 300.0)
        write_prices(store, "WIN2", 100.0, 180.0)
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(
            research_config, safe_asset="SAFE", bear_exposure=0.5, top_n=2
        ).generate_targets(AS_OF, ["SAFE", "WIN2"], store)

        assert sorted(targets) == ["SAFE", "WIN2"]
        assert targets["SAFE"] > targets["WIN2"]


class TestFilterDisabled:
    def test_safe_asset_needs_no_trend_check_when_the_filter_is_off(
        self, store, research_config
    ):
        """`abs_momentum_ma_days=0` switches the per-asset floor off entirely;
        the safe asset must not keep enforcing a rule nothing else obeys."""
        write_prices(store, "WIN1", 100.0, 200.0)
        write_prices(store, "WIN2", 100.0, 180.0)
        write_prices(store, "SAFE", 130.0, 100.0)  # falling
        write_macro(store, 3000.0, 2000.0)

        targets = make_strategy(
            research_config,
            safe_asset="SAFE",
            bear_exposure=0.5,
            abs_momentum_ma_days=0,
        ).generate_targets(AS_OF, ["WIN1", "WIN2"], store)

        assert "SAFE" in targets
