"""Short-selling and crowding factors.

Every factor here is declared "higher is better" before it is measured, the
way the adoption gate's signed IC requires. The declarations and their
reasoning are in the design spec; the short ones are repeated in each class
so a reader does not have to leave the file to know what was predicted.

The direction of `ShortBalanceChangeFactor` is genuinely contested — the
informed-short hypothesis predicts one sign and the short-squeeze hypothesis
the other. It is declared once, up front, and **not flipped if the
measurement disagrees**. Trying both directions and keeping whichever scores
better is two chances at significance dressed up as one result; a reversed
finding gets recorded and re-confirmed on untouched data before it is
adopted. `momentum_12m_ex1m` was discovered to be a reverse signal and was
dropped rather than negated — the same rule.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore
from tradingbot.factors.base import Factor

# Calendar days to span a given number of trading days, with slack for
# holidays. Over-fetching is harmless — the window is trimmed by row count.
_CALENDAR_SLACK = 2.0

SHORT_BALANCE_DATASET = "short_balance"
SHORT_VOLUME_DATASET = "short_volume"


def _panel_window(
    data_store: PriceDataStore, dataset: str, dt: date, symbols: Sequence[str], days: int
) -> pd.DataFrame:
    start = dt - timedelta(days=int(days * _CALENDAR_SLACK) + 7)
    return data_store.panel(dataset, dt, list(symbols), start=start)


def _rows_for(panel: pd.DataFrame, symbol: str, days: int) -> pd.DataFrame | None:
    """The last `days` rows for one symbol, or None if the window is short.

    A partial window is not a shorter window — it is a different measurement
    from the one every other name in the cross-section is being scored on.
    """
    rows = panel[panel["symbol"] == symbol].sort_values("date").tail(days)
    if len(rows) < days:
        return None
    return rows


class ShortBalanceRatioFactor(Factor):
    """Short balance as a share of shares outstanding.

    Declared direction: **negative** — a heavily shorted name is one that
    informed sellers have taken a position against, so the raw ratio is
    negated to keep "higher is better".

    This is the *level*. It is slow-moving and largely a property of the
    stock (borrow availability, index membership, arbitrage structures)
    rather than a view on it, which is why the change factor below exists
    alongside it.
    """

    name = "short_balance_ratio"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values
        panel = _panel_window(data_store, SHORT_BALANCE_DATASET, dt, values.index, 1)
        if panel.empty:
            return values

        for symbol in values.index:
            rows = _rows_for(panel, symbol, 1)
            if rows is None:
                continue
            ratio = float(rows["short_balance_ratio"].iloc[-1])
            if pd.isna(ratio):
                continue
            values.loc[symbol] = -ratio
        return values


class ShortBalanceChangeFactor(Factor):
    """Change in short-balance ratio over `days` trading days.

    Declared direction: **negative** — a *rising* balance means shorts are
    adding, and the informed-short hypothesis says that predicts weakness.
    The competing short-squeeze hypothesis predicts the opposite sign. See
    this module's docstring: the declaration stands regardless of what the
    measurement says, and a reversal is a finding to re-confirm, not a
    licence to negate.

    The change, not the level, is the part that can carry information: a
    stock that is always 5% short tells you about its borrow market, while
    one that went from 1% to 5% tells you somebody decided something.
    """

    def __init__(self, days: int = 20) -> None:
        if days <= 0:
            raise ValueError("days must be positive")
        self.days = days
        self.name = f"short_balance_change_{days}d"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values
        # days + 1 rows give `days` changes.
        panel = _panel_window(data_store, SHORT_BALANCE_DATASET, dt, values.index, self.days + 1)
        if panel.empty:
            return values

        for symbol in values.index:
            rows = _rows_for(panel, symbol, self.days + 1)
            if rows is None:
                continue
            series = rows["short_balance_ratio"].astype(float)
            first, last = series.iloc[0], series.iloc[-1]
            if pd.isna(first) or pd.isna(last):
                continue
            values.loc[symbol] = -(float(last) - float(first))
        return values


class ShortVolumeIntensityFactor(Factor):
    """Short-sale volume as a share of total volume, averaged over `days`.

    Declared direction: **negative** — sustained short-side pressure is
    read as informed selling.

    Averaged rather than summed so a symbol that missed a session is not
    penalised for the gap; the full-window requirement already handles the
    case where too much is missing.
    """

    def __init__(self, days: int = 20) -> None:
        if days <= 0:
            raise ValueError("days must be positive")
        self.days = days
        self.name = f"short_volume_intensity_{days}d"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values
        panel = _panel_window(data_store, SHORT_VOLUME_DATASET, dt, values.index, self.days)
        if panel.empty:
            return values

        for symbol in values.index:
            rows = _rows_for(panel, symbol, self.days)
            if rows is None:
                continue
            ratio = rows["short_volume_ratio"].astype(float).mean()
            if pd.isna(ratio):
                continue
            values.loc[symbol] = -float(ratio)
        return values


class FlowCrowdingFactor(Factor):
    """How one-sided institutional and foreign buying has been.

    Declared direction: **negative** — a name that both groups have been
    piling into is a crowded name, and July 2026 is the reminder of what a
    crowded name does when the flow stops: everyone leaves through the same
    door at once.

    Crowding is the *sum* of the two groups' net buying relative to traded
    value, so the two agreeing scores much higher than either alone. That is
    deliberate — one buyer is a view, two buyers on the same side is a crowd.

    Unlike `NetBuyIntensityFactor`, which reads flow as a bullish signal,
    this reads the same data as a risk. Both can be true at different
    horizons, which is exactly why they are separate factors and why the
    design keeps crowding out of the score and in the brake.
    """

    def __init__(self, days: int = 20) -> None:
        if days <= 0:
            raise ValueError("days must be positive")
        self.days = days
        self.name = f"flow_crowding_{days}d"

    def compute(
        self, dt: date, universe: Sequence[str], data_store: PriceDataStore
    ) -> pd.Series:
        values = self._empty(universe)
        if not len(values):
            return values
        panel = _panel_window(data_store, "flows", dt, values.index, self.days)
        if panel.empty:
            return values

        for symbol in values.index:
            rows = _rows_for(panel, symbol, self.days)
            if rows is None:
                continue
            try:
                prices = data_store.price_history(symbol, dt, self.days)
            except (FileNotFoundError, KeyError):
                continue
            if len(prices) < self.days:
                continue
            traded_value = float((prices["close"] * prices["volume"]).sum())
            if traded_value <= 0:
                continue
            crowd = float(rows["foreign_net"].sum() + rows["institution_net"].sum())
            values.loc[symbol] = -(crowd / traded_value)
        return values
