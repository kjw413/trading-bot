"""BlendedMomentumFactor: several lookbacks averaged in z-space.

The property that matters is that the declared weights actually govern the
blend. Blending raw returns would not do that — 12-month returns are several
times larger than 3-month ones, so the long horizon would win the average no
matter what the config said.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.factors.momentum import BlendedMomentumFactor, MomentumFactor, parse_horizon
from tradingbot.factors.registry import get_factor
from tradingbot.factors.transform import standardize

DT = date(2024, 6, 28)
DAYS = 400


class _Store:
    """Price history keyed by symbol; mirrors ParquetDataStore's contract."""

    def __init__(self, series: dict[str, np.ndarray]) -> None:
        index = pd.bdate_range(end=pd.Timestamp(DT), periods=DAYS)
        self._frames = {
            symbol: pd.DataFrame({"close": values}, index=index)
            for symbol, values in series.items()
        }

    def price_history(self, symbol: str, dt: date, n: int) -> pd.DataFrame:
        try:
            frame = self._frames[symbol]
        except KeyError as exc:
            raise FileNotFoundError(symbol) from exc
        return frame.loc[: pd.Timestamp(dt)].tail(n)


def _ramp(start: float, end: float) -> np.ndarray:
    return np.linspace(start, end, DAYS)


# MomentumFactor reads `closes.iloc[-lookback]` with lookback = months*21 + 1,
# so the 12-month window is exactly the last 253 rows and the 3-month window
# the last 64. Splitting the path on those boundaries lets a test put growth
# where only one horizon can see it.
_RECENT_ROWS = 3 * 21 + 1
_MID_ROWS = 12 * 21 + 1 - _RECENT_ROWS


def _from_growth(*, pre: float, mid: float, recent: float) -> np.ndarray:
    """A price path with prescribed growth in each horizon's exclusive stretch.

    `pre` lands outside the 12-month window entirely, `mid` inside the
    12-month window but outside the 3-month one, `recent` inside both.
    """
    lengths = (DAYS - _MID_ROWS - _RECENT_ROWS, _MID_ROWS, _RECENT_ROWS)
    level = 100.0
    segments = []
    for length, growth in zip(lengths, (pre, mid, recent)):
        segments.append(np.geomspace(level, level * growth, length))
        level *= growth
    return np.concatenate(segments)


class TestParseHorizon:
    def test_plain_months(self):
        factor = parse_horizon("m6")
        assert (factor.months, factor.skip_months) == (6, 0)

    def test_skip_form(self):
        factor = parse_horizon("m12_ex1")
        assert (factor.months, factor.skip_months) == (12, 1)

    @pytest.mark.parametrize("spec", ["", "6m", "mm3", "m", "months3", "m3_ex"])
    def test_rejects_unknown_forms(self, spec):
        with pytest.raises(ValueError, match="Unknown momentum horizon"):
            parse_horizon(spec)


class TestConstruction:
    def test_rejects_empty_weights(self):
        with pytest.raises(ValueError, match="at least one horizon"):
            BlendedMomentumFactor({})

    def test_rejects_negative_weight(self):
        with pytest.raises(ValueError, match="non-negative"):
            BlendedMomentumFactor({"m3": 0.5, "m6": -0.1})

    def test_rejects_all_zero_weights(self):
        with pytest.raises(ValueError, match="sum to zero"):
            BlendedMomentumFactor({"m3": 0.0, "m6": 0.0})

    def test_registry_exposes_the_configured_blend(self):
        factor = get_factor("momentum_blend")
        assert factor.name == "momentum_blend"
        assert factor.horizons == ["m3", "m6", "m12"]

    def test_registry_exposes_the_skip_variant(self):
        factor = get_factor("momentum_blend_ex1m")
        assert factor.name == "momentum_blend_ex1m"
        assert factor.horizons == ["m3", "m6", "m12_ex1"]


class TestBlending:
    def test_weights_govern_the_outcome(self):
        """AAA leads early and lags late; BBB is the mirror image.

        Weighting the long horizon must pick AAA, weighting the short horizon
        must pick BBB. If the blend used raw returns the long horizon would
        decide both, and the two rankings would come out identical.
        """
        store = _Store({
            # AAA tripled where only the 12m window sees it, then went flat.
            "AAA": _from_growth(pre=1.0, mid=3.0, recent=1.0),
            # BBB did all its work inside the last three months.
            "BBB": _from_growth(pre=1.0, mid=1.0, recent=1.5),
            "CCC": _from_growth(pre=1.0, mid=1.2, recent=1.1),
        })
        universe = ["AAA", "BBB", "CCC"]

        long_biased = BlendedMomentumFactor({"m3": 0.05, "m12": 0.95}).compute(DT, universe, store)
        short_biased = BlendedMomentumFactor({"m3": 0.95, "m12": 0.05}).compute(DT, universe, store)

        assert long_biased.idxmax() == "AAA"
        assert short_biased.idxmax() == "BBB"

    def test_matches_a_hand_computed_blend_of_standardized_horizons(self):
        store = _Store({
            "AAA": _ramp(100.0, 180.0),
            "BBB": _ramp(100.0, 140.0),
            "CCC": _ramp(100.0, 90.0),
        })
        universe = ["AAA", "BBB", "CCC"]
        weights = {"m3": 0.2, "m6": 0.3, "m12": 0.5}

        blended = BlendedMomentumFactor(weights).compute(DT, universe, store)

        parts = {
            spec: standardize(parse_horizon(spec).compute(DT, universe, store))
            for spec in weights
        }
        expected = sum(parts[spec] * weight for spec, weight in weights.items()) / sum(
            weights.values()
        )
        pd.testing.assert_series_equal(blended, expected, check_names=False)

    def test_a_symbol_missing_the_longest_horizon_is_nan(self):
        """Not scored on its short horizons alone — that would be a different
        yardstick than the one its competitors are measured with."""
        short = np.linspace(100.0, 150.0, DAYS)
        store = _Store({"AAA": _ramp(100.0, 180.0), "BBB": _ramp(100.0, 120.0), "NEW": short})
        # NEW has a full frame, so shorten it to under a year of history.
        store._frames["NEW"] = store._frames["NEW"].tail(100)

        blended = BlendedMomentumFactor({"m3": 0.5, "m12": 0.5}).compute(
            DT, ["AAA", "BBB", "NEW"], store
        )

        assert pd.isna(blended.loc["NEW"])
        assert blended.loc[["AAA", "BBB"]].notna().all()

    def test_unknown_symbol_is_nan_not_an_error(self):
        store = _Store({"AAA": _ramp(100.0, 180.0), "BBB": _ramp(100.0, 120.0)})

        blended = BlendedMomentumFactor({"m3": 0.5, "m12": 0.5}).compute(
            DT, ["AAA", "BBB", "GONE"], store
        )

        assert pd.isna(blended.loc["GONE"])

    def test_empty_universe_returns_empty(self):
        store = _Store({})
        assert BlendedMomentumFactor({"m3": 1.0}).compute(DT, [], store).empty

    def test_single_horizon_blend_ranks_like_that_horizon(self):
        store = _Store({
            "AAA": _ramp(100.0, 180.0),
            "BBB": _ramp(100.0, 140.0),
            "CCC": _ramp(100.0, 90.0),
        })
        universe = ["AAA", "BBB", "CCC"]

        blended = BlendedMomentumFactor({"m6": 1.0}).compute(DT, universe, store)
        plain = MomentumFactor(6).compute(DT, universe, store)

        assert list(blended.rank().sort_values().index) == list(plain.rank().sort_values().index)
