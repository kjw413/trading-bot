"""A universe recomputed from price history: the most traded N names.

Five hundred names cannot be maintained by hand, and writing down today's
list would backdate today's winners into the past. So membership is computed,
from data dated before the day it applies to.

Two properties matter more than the ranking itself.

**Determinism.** The universe for a period is computed from a fixed anchor —
the last trading day before the period begins — not from whenever the caller
first happened to ask. Otherwise a backtest starting in March and one starting
in January would disagree about March's universe, and neither would be wrong
in a way anyone could see.

**Stability.** Membership is fixed within a rebalance period. Recomputing
daily would churn the names sitting either side of the cutoff, and every one
of those crossings is a round trip the strategy pays for and the signal never
asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.engine.calendar import get_calendar
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

# Periods a universe can be held fixed for, and how a date maps to the period
# it belongs to. Anything finer than weekly defeats the purpose.
_PERIOD_KEYS = {
    "monthly": lambda dt: (dt.year, dt.month),
    "weekly": lambda dt: dt.isocalendar()[:2],
    "quarterly": lambda dt: (dt.year, (dt.month - 1) // 3),
}


def period_start(dt: date, rebalance: str) -> date:
    """First calendar day of the period `dt` falls in."""
    if rebalance == "monthly":
        return date(dt.year, dt.month, 1)
    if rebalance == "quarterly":
        return date(dt.year, ((dt.month - 1) // 3) * 3 + 1, 1)
    if rebalance == "weekly":
        return dt - pd.Timedelta(days=dt.weekday()).to_pytimedelta()
    raise ValueError(f"Unknown rebalance period: {rebalance}. Available: {', '.join(_PERIOD_KEYS)}")


@dataclass
class LiquidityUniverse:
    """Top `top_n` candidates by average dollar volume, recomputed per period.

    `data_store` needs only `price_history(symbol, end, lookback)`; the store
    enforces the date cutoff, and this class never reads around it.
    """

    market: str
    candidates: Sequence[str]
    data_store: object
    top_n: int
    lookback_days: int = 20
    min_listing_days: int = 400
    min_dollar_volume: float = 0.0
    rebalance: str = "monthly"
    _cache: dict[date, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.rebalance not in _PERIOD_KEYS:
            raise ValueError(
                f"Unknown rebalance period: {self.rebalance}. "
                f"Available: {', '.join(sorted(_PERIOD_KEYS))}"
            )
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")

    def anchor_for(self, dt: date) -> date:
        """Last trading day before `dt`'s period begins.

        The anchor, not `dt`, is what the ranking reads. That is what makes the
        answer depend only on the date asked about — never on when the question
        was first asked — and it keeps the whole period's membership decided
        before the period starts.
        """
        calendar = get_calendar(self.market)
        return calendar.previous_trading_day(period_start(dt, self.rebalance))

    def members(self, dt: date) -> list[str]:
        anchor = self.anchor_for(dt)
        if anchor not in self._cache:
            self._cache[anchor] = self._rank(anchor)
        return self._cache[anchor]

    def _rank(self, anchor: date) -> list[str]:
        """Rank candidates by dollar volume as of `anchor`.

        A symbol is skipped rather than scored when it has no data, too little
        history, or no traded value. Each of those is "not investable here",
        not "worth zero" — ranking them as zero would be the same answer by
        accident and would break the moment the cutoff moved.
        """
        history_needed = max(self.lookback_days, self.min_listing_days)
        scored: list[tuple[float, str]] = []
        skipped = 0

        for symbol in self.candidates:
            try:
                history = self.data_store.price_history(symbol, anchor, history_needed)
            except (FileNotFoundError, KeyError):
                skipped += 1
                continue
            if len(history) < self.min_listing_days:
                skipped += 1
                continue

            window = history.tail(self.lookback_days)
            value = float((window["close"] * window["volume"]).mean())
            if not value > 0 or value < self.min_dollar_volume:
                skipped += 1
                continue
            scored.append((value, symbol.upper()))

        # Descending by value, then by symbol so ties resolve the same way on
        # every run rather than by whatever order the candidates arrived in.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        selected = sorted(symbol for _, symbol in scored[: self.top_n])
        LOGGER.info(
            "Liquidity universe at %s: %s of %s candidates scoreable, top %s selected",
            anchor,
            len(scored),
            len(self.candidates),
            len(selected),
        )
        if skipped:
            LOGGER.debug("%s candidates skipped at %s", skipped, anchor)
        return selected
