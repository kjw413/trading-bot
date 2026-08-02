"""Short-selling and crowding factors.

Two properties carry most of the weight here: the declared sign (every
factor must be "higher is better" before the gate sees it) and the
full-window rule (a partial window is a different measurement, not a
shorter one).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.factors.registry import get_factor
from tradingbot.factors.short_interest import (
    FlowCrowdingFactor,
    ShortBalanceChangeFactor,
    ShortBalanceRatioFactor,
    ShortVolumeIntensityFactor,
)

DT = date(2024, 6, 28)
DAYS = 40


class _Store:
    """Panels plus prices, matching ParquetDataStore's contract."""

    def __init__(self, panels: dict[str, pd.DataFrame], prices: dict[str, float] | None = None):
        self._panels = panels
        self._prices = prices or {}

    def panel(self, dataset, as_of, symbols=None, *, start=None):
        frame = self._panels.get(dataset)
        if frame is None:
            return pd.DataFrame()
        out = frame[frame["date"] <= pd.Timestamp(as_of)]
        if symbols is not None:
            out = out[out["symbol"].isin(list(symbols))]
        return out.reset_index(drop=True)

    def price_history(self, symbol, end, lookback):
        if symbol not in self._prices:
            raise FileNotFoundError(symbol)
        index = pd.bdate_range(end=pd.Timestamp(end), periods=lookback)
        return pd.DataFrame(
            {"close": [self._prices[symbol]] * lookback, "volume": [1000.0] * lookback},
            index=index,
        )


def balance_panel(series: dict[str, list[float]], days: int = DAYS) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp(DT), periods=days)
    rows = []
    for symbol, ratios in series.items():
        rows.append(
            pd.DataFrame(
                {"date": index, "symbol": symbol, "short_balance_ratio": ratios}
            )
        )
    return pd.concat(rows, ignore_index=True)


def volume_panel(series: dict[str, list[float]], days: int = DAYS) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp(DT), periods=days)
    rows = []
    for symbol, ratios in series.items():
        rows.append(
            pd.DataFrame({"date": index, "symbol": symbol, "short_volume_ratio": ratios})
        )
    return pd.concat(rows, ignore_index=True)


def flows_panel(series: dict[str, tuple[float, float]], days: int = DAYS) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp(DT), periods=days)
    rows = []
    for symbol, (foreign, institution) in series.items():
        rows.append(
            pd.DataFrame(
                {
                    "date": index,
                    "symbol": symbol,
                    "foreign_net": [foreign] * days,
                    "institution_net": [institution] * days,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


class TestDeclaredDirection:
    """Every factor must score "higher is better" before the gate sees it."""

    def test_heavier_short_balance_scores_lower(self):
        store = _Store(
            {"short_balance": balance_panel({"HEAVY": [0.08] * DAYS, "LIGHT": [0.01] * DAYS})}
        )

        values = ShortBalanceRatioFactor().compute(DT, ["HEAVY", "LIGHT"], store)

        assert values["LIGHT"] > values["HEAVY"]

    def test_rising_short_balance_scores_lower(self):
        rising = [0.01 + 0.001 * i for i in range(DAYS)]
        falling = [0.05 - 0.001 * i for i in range(DAYS)]
        store = _Store({"short_balance": balance_panel({"RISING": rising, "FALLING": falling})})

        values = ShortBalanceChangeFactor(20).compute(DT, ["RISING", "FALLING"], store)

        assert values["FALLING"] > values["RISING"]
        assert values["RISING"] < 0 < values["FALLING"]

    def test_heavier_short_volume_scores_lower(self):
        store = _Store(
            {"short_volume": volume_panel({"PRESSED": [0.30] * DAYS, "CALM": [0.02] * DAYS})}
        )

        values = ShortVolumeIntensityFactor(20).compute(DT, ["PRESSED", "CALM"], store)

        assert values["CALM"] > values["PRESSED"]

    def test_more_crowded_scores_lower(self):
        store = _Store(
            {"flows": flows_panel({"CROWDED": (5e6, 5e6), "QUIET": (1e5, -1e5)})},
            prices={"CROWDED": 100.0, "QUIET": 100.0},
        )

        values = FlowCrowdingFactor(20).compute(DT, ["CROWDED", "QUIET"], store)

        assert values["QUIET"] > values["CROWDED"]

    def test_both_groups_buying_counts_as_more_crowded_than_one(self):
        """One buyer is a view; two on the same side is a crowd."""
        store = _Store(
            {"flows": flows_panel({"BOTH": (5e6, 5e6), "ONE": (5e6, 0.0)})},
            prices={"BOTH": 100.0, "ONE": 100.0},
        )

        values = FlowCrowdingFactor(20).compute(DT, ["BOTH", "ONE"], store)

        assert values["BOTH"] < values["ONE"]


class TestChangeUsesTheChangeNotTheLevel:
    def test_a_constantly_shorted_name_scores_zero_change(self):
        """Always 5% short says something about the borrow market, not the stock."""
        store = _Store({"short_balance": balance_panel({"FLAT": [0.05] * DAYS})})

        values = ShortBalanceChangeFactor(20).compute(DT, ["FLAT"], store)

        assert values["FLAT"] == pytest.approx(0.0)

    def test_level_and_change_disagree_when_they_should(self):
        """HIGH is heavily but steadily shorted; LOW is lightly but rapidly.
        The level factor prefers LOW, the change factor prefers HIGH."""
        store = _Store(
            {
                "short_balance": balance_panel(
                    {"HIGH": [0.08] * DAYS, "LOW": [0.01 + 0.001 * i for i in range(DAYS)]}
                )
            }
        )

        level = ShortBalanceRatioFactor().compute(DT, ["HIGH", "LOW"], store)
        change = ShortBalanceChangeFactor(20).compute(DT, ["HIGH", "LOW"], store)

        assert level["LOW"] > level["HIGH"]
        assert change["HIGH"] > change["LOW"]


class TestPartialWindows:
    def test_short_history_is_nan_not_a_shorter_window(self):
        panel = balance_panel({"FULL": [0.02] * DAYS})
        short = balance_panel({"NEW": [0.02] * 5}, days=5)
        store = _Store({"short_balance": pd.concat([panel, short], ignore_index=True)})

        values = ShortBalanceChangeFactor(20).compute(DT, ["FULL", "NEW"], store)

        assert pd.isna(values["NEW"])
        assert not pd.isna(values["FULL"])

    def test_missing_symbol_is_nan(self):
        store = _Store({"short_balance": balance_panel({"HERE": [0.02] * DAYS})})

        values = ShortBalanceRatioFactor().compute(DT, ["HERE", "GONE"], store)

        assert pd.isna(values["GONE"])

    def test_absent_panel_yields_all_nan(self):
        """A machine that never collected short data must not score zeros."""
        values = ShortBalanceRatioFactor().compute(DT, ["AAA"], _Store({}))

        assert pd.isna(values["AAA"])

    def test_nan_ratio_is_not_scored(self):
        panel = balance_panel({"AAA": [float("nan")] * DAYS})
        store = _Store({"short_balance": panel})

        values = ShortBalanceRatioFactor().compute(DT, ["AAA"], store)

        assert pd.isna(values["AAA"])

    def test_crowding_without_prices_is_nan(self):
        store = _Store({"flows": flows_panel({"AAA": (1e6, 1e6)})}, prices={})

        values = FlowCrowdingFactor(20).compute(DT, ["AAA"], store)

        assert pd.isna(values["AAA"])

    def test_empty_universe_returns_empty(self):
        assert ShortBalanceRatioFactor().compute(DT, [], _Store({})).empty


class TestRegistry:
    @pytest.mark.parametrize(
        "name",
        [
            "short_balance_ratio",
            "short_balance_change_20d",
            "short_balance_change_60d",
            "short_volume_intensity_20d",
            "flow_crowding_20d",
        ],
    )
    def test_registered(self, name):
        assert get_factor(name).name == name

    @pytest.mark.parametrize("days", [0, -1])
    def test_non_positive_window_is_rejected(self, days):
        with pytest.raises(ValueError, match="days must be positive"):
            ShortBalanceChangeFactor(days)

    def test_new_factors_are_not_in_the_shipped_factor_weights(self):
        """Gate first. A factor reaches [factor_weights] only after it passes."""
        import tomllib
        from pathlib import Path

        research = tomllib.loads(
            Path("config/research.toml").read_text(encoding="utf-8")
        )
        weights = research["factor_weights"]

        for name in (
            "short_balance_ratio",
            "short_balance_change_20d",
            "short_volume_intensity_20d",
            "flow_crowding_20d",
        ):
            assert name not in weights
