from __future__ import annotations

import re
from datetime import date
from typing import Mapping, Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor
from tradingbot.factors.transform import combine, standardize

TRADING_DAYS_PER_MONTH = 21

# research.toml `[strategy.etf_momentum] momentum_weights` names horizons as
# m3 / m6 / m12, with m12_ex1 available for the classic 12-1 form.
_HORIZON_PATTERN = re.compile(r"^m(\d+)(?:_ex(\d+))?$")


class MomentumFactor(Factor):
    """Price momentum: total return over the past `months` months.

    `skip_months` excludes the most recent months from the window
    (e.g. months=12, skip_months=1 is the classic 12-1 momentum that avoids
    short-term reversal). Windows are measured in trading days
    (21 per month), using close prices as of the computation date.
    """

    def __init__(self, months: int, skip_months: int = 0) -> None:
        if months <= 0:
            raise ValueError("months must be positive")
        if skip_months < 0:
            raise ValueError("skip_months cannot be negative")
        self.months = months
        self.skip_months = skip_months
        suffix = f"_ex{skip_months}m" if skip_months else ""
        self.name = f"momentum_{months}m{suffix}"

    def compute(self, dt: date, universe: Sequence[str], data_store: PriceDataStore) -> pd.Series:
        window_days = self.months * TRADING_DAYS_PER_MONTH
        skip_days = self.skip_months * TRADING_DAYS_PER_MONTH
        lookback = window_days + skip_days + 1

        values = self._empty(universe)
        for symbol in values.index:
            try:
                history = data_store.price_history(symbol, dt, lookback)
            except (FileNotFoundError, KeyError):
                continue
            closes = history["close"].dropna()
            if len(closes) < lookback:
                continue
            end_price = float(closes.iloc[-1 - skip_days])
            start_price = float(closes.iloc[-lookback])
            if start_price <= 0:
                continue
            values.loc[symbol] = end_price / start_price - 1.0
        return values


def parse_horizon(spec: str) -> MomentumFactor:
    """`m3` -> 3-month momentum, `m12_ex1` -> 12-1 momentum."""
    match = _HORIZON_PATTERN.match(spec.strip())
    if match is None:
        raise ValueError(
            f"Unknown momentum horizon: {spec!r}. Expected forms like 'm3' or 'm12_ex1'."
        )
    months, skip = match.groups()
    return MomentumFactor(int(months), skip_months=int(skip) if skip else 0)


class BlendedMomentumFactor(Factor):
    """Weighted blend of several momentum lookbacks.

    A single lookback is a noisy estimate of trend: whichever window you pick,
    some of what it measures is the window's own luck about where it happened
    to start. Averaging several windows cancels part of that noise, which is
    what the adoption gate's IC IR actually rewards — it divides mean IC by
    IC volatility, so steadying the signal counts as much as strengthening it.

    Each horizon is standardized cross-sectionally **before** weighting. Raw
    12-month returns are several times larger than 3-month ones, so blending
    the raw values would hand the long horizon the blend regardless of the
    weights the config declares. Standardizing first makes the declared
    weights mean what they say.

    A symbol needs *every* horizon to be scored. Scoring a name on its short
    horizons alone would quietly hand a newly listed fund a score built from
    different evidence than its competitors', and `MomentumFactor` already
    treats insufficient history as NaN rather than as a shorter window.
    """

    def __init__(self, weights: Mapping[str, float], *, name: str = "momentum_blend") -> None:
        if not weights:
            raise ValueError("momentum blend needs at least one horizon")
        negative = sorted(spec for spec, weight in weights.items() if float(weight) < 0)
        if negative:
            raise ValueError(f"momentum blend weights must be non-negative: {negative}")
        if sum(float(weight) for weight in weights.values()) <= 0:
            raise ValueError("momentum blend weights sum to zero")
        self.name = name
        self._components = {spec: parse_horizon(spec) for spec in weights}
        self._weights = {spec: float(weight) for spec, weight in weights.items()}

    @property
    def horizons(self) -> list[str]:
        return list(self._components)

    def compute(self, dt: date, universe: Sequence[str], data_store: PriceDataStore) -> pd.Series:
        if not universe:
            return self._empty(universe)
        scores = {
            spec: standardize(factor.compute(dt, universe, data_store))
            for spec, factor in self._components.items()
        }
        blended = combine(scores, self._weights, min_factors=len(scores))
        blended.name = self.name
        return blended
