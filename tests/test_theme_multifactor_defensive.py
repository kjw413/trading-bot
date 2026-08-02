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


class TestRankTilt:
    """`tilt_strength` holds the basket instead of selecting a few names."""

    def test_tilt_holds_every_scoreable_name(self, store, research_config):
        for symbol, end in [("AAA", 200.0), ("BBB", 170.0), ("CCC", 140.0), ("DDD", 120.0)]:
            write_prices(store, symbol, 100.0, end)
        write_macro(store, 2000.0, 3000.0)  # bull: no regime scaling

        targets = make_strategy(
            research_config, tilt_strength=0.5, top_n=2, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["AAA", "BBB", "CCC", "DDD"], store)

        assert set(targets) == {"AAA", "BBB", "CCC", "DDD"}
        assert targets["AAA"] > targets["DDD"]

    def test_selection_is_the_default(self, store, research_config):
        """Without tilt_strength the original top_n behaviour must survive,
        so the two structures can be compared rather than one replacing it."""
        for symbol, end in [("AAA", 200.0), ("BBB", 170.0), ("CCC", 140.0), ("DDD", 120.0)]:
            write_prices(store, symbol, 100.0, end)
        write_macro(store, 2000.0, 3000.0)

        targets = make_strategy(
            research_config, top_n=2, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["AAA", "BBB", "CCC", "DDD"], store)

        assert set(targets) == {"AAA", "BBB"}

    def test_absolute_momentum_still_excludes_names_under_tilt(self, store, research_config):
        """A per-asset floor is not a ranking opinion — the tilt must not
        quietly readmit a name the filter threw out."""
        write_prices(store, "RISER", 100.0, 200.0)
        write_prices(store, "FALLER", 200.0, 100.0)
        write_macro(store, 2000.0, 3000.0)

        targets = make_strategy(
            research_config, tilt_strength=0.5, abs_momentum_ma_days=200
        ).generate_targets(AS_OF, ["RISER", "FALLER"], store)

        assert "FALLER" not in targets

    def test_zero_tilt_reproduces_the_equal_weight_benchmark(self, store, research_config):
        for symbol, end in [("AAA", 200.0), ("BBB", 170.0), ("CCC", 140.0)]:
            write_prices(store, symbol, 100.0, end)
        write_macro(store, 2000.0, 3000.0)

        targets = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["AAA", "BBB", "CCC"], store)

        assert len(set(round(w, 9) for w in targets.values())) == 1


def write_flows(store, values: dict[str, tuple[float, float]]) -> None:
    """Daily foreign/institution net buying for the crowding factor."""
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=DAYS)
    rows = []
    for symbol, (foreign, institution) in values.items():
        rows.append(
            pd.DataFrame(
                {
                    "date": index,
                    "symbol": symbol,
                    "foreign_net": [foreign] * DAYS,
                    "institution_net": [institution] * DAYS,
                    "individual_net": [-(foreign + institution)] * DAYS,
                }
            )
        )
    frame = pd.concat(rows, ignore_index=True)
    PanelStore(store.processed_root, "flows", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


class TestCrowdingBrake:
    """A risk control, not a factor — it must stay separable from the score."""

    def test_the_most_crowded_name_is_trimmed(self, store, research_config):
        write_prices(store, "CROWDED", 100.0, 200.0)
        write_prices(store, "QUIET", 100.0, 200.0)  # same momentum
        write_macro(store, 2000.0, 3000.0)
        write_flows(store, {"CROWDED": (9e8, 9e8), "QUIET": (1e5, -1e5)})

        plain = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["CROWDED", "QUIET"], store)
        braked = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0,
            crowding_percentile=0.5, crowding_retain=0.5,
        ).generate_targets(AS_OF, ["CROWDED", "QUIET"], store)

        assert plain["CROWDED"] == pytest.approx(plain["QUIET"])
        assert braked["CROWDED"] < braked["QUIET"]

    def test_disabled_by_default(self, store, research_config):
        write_prices(store, "CROWDED", 100.0, 200.0)
        write_prices(store, "QUIET", 100.0, 200.0)
        write_macro(store, 2000.0, 3000.0)
        write_flows(store, {"CROWDED": (9e8, 9e8), "QUIET": (1e5, -1e5)})

        targets = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["CROWDED", "QUIET"], store)

        assert targets["CROWDED"] == pytest.approx(targets["QUIET"])

    def test_trimmed_weight_goes_to_cash_not_to_the_other_names(
        self, store, research_config
    ):
        """Redeploying it into the next-most-crowded name defeats the purpose."""
        write_prices(store, "CROWDED", 100.0, 200.0)
        write_prices(store, "QUIET", 100.0, 200.0)
        write_macro(store, 2000.0, 3000.0)
        write_flows(store, {"CROWDED": (9e8, 9e8), "QUIET": (1e5, -1e5)})

        plain = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["CROWDED", "QUIET"], store)
        braked = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0,
            crowding_percentile=0.5, crowding_retain=0.5,
        ).generate_targets(AS_OF, ["CROWDED", "QUIET"], store)

        assert braked["QUIET"] == pytest.approx(plain["QUIET"])
        assert sum(braked.values()) < sum(plain.values())

    def test_missing_flow_panel_leaves_weights_untouched(self, store, research_config):
        """A market without flow data must not have its portfolio silently cut."""
        write_prices(store, "AAA", 100.0, 200.0)
        write_prices(store, "BBB", 100.0, 180.0)
        write_macro(store, 2000.0, 3000.0)

        without_brake = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0
        ).generate_targets(AS_OF, ["AAA", "BBB"], store)
        with_brake = make_strategy(
            research_config, tilt_strength=0.0, abs_momentum_ma_days=0,
            crowding_percentile=0.5,
        ).generate_targets(AS_OF, ["AAA", "BBB"], store)

        assert with_brake == pytest.approx(without_brake)

    @pytest.mark.parametrize("cutoff", [0.0, 1.0, -0.1])
    def test_invalid_percentile_is_rejected(self, store, research_config, cutoff):
        write_prices(store, "AAA", 100.0, 200.0)
        write_macro(store, 2000.0, 3000.0)
        write_flows(store, {"AAA": (1e6, 1e6)})

        with pytest.raises(ValueError, match="crowding_percentile"):
            make_strategy(
                research_config, tilt_strength=0.0, abs_momentum_ma_days=0,
                crowding_percentile=cutoff,
            ).generate_targets(AS_OF, ["AAA"], store)
