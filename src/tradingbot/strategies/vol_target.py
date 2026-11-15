"""Hold one instrument at whatever size keeps its risk near a fixed level.

    realised_vol_t = std of the last `vol_days` daily log returns, annualised
    target_t       = clip(target_vol / realised_vol_t, min_weight, max_weight)

When the instrument is calm the position is large; when it is thrashing the
position shrinks in proportion. Nothing here forecasts direction. The rule
leans only on the one thing about markets that is reliably persistent: a
violent week is usually followed by another, so recent volatility says
something about the risk of holding tomorrow even though it says nothing
about which way tomorrow goes.

That is the opposite stance from `pid_reversion`, which adds to a falling
position. A falling market is usually a volatile one, so this rule is
lightest exactly where that one is heaviest.

The cap is what makes it long-only and unlevered: in a quiet market the
formula would ask for more than the whole account, and gets `max_weight`.

No state is kept. Each target is recomputed from price history.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

import numpy as np
import pandas as pd

from tradingbot.allocation.rebalance import plan_rebalance
from tradingbot.models import Bar
from tradingbot.strategies.base import Strategy, StrategyContext

TRADING_DAYS = 252


@dataclass(frozen=True)
class VolTargetParams:
    vol_days: int = 20
    target_vol: float = 0.20
    min_weight: float = 0.0
    # Not 1.0: the engine keeps a 2% cash buffer, and a target it can never
    # reach would be re-ordered every day.
    max_weight: float = 0.98
    # Realised volatility moves a little every day. Without a band the rule
    # would trade daily to follow noise in its own estimate.
    band: float = 0.10

    def __post_init__(self) -> None:
        if self.vol_days < 2:
            raise ValueError("vol_days must be at least 2")
        if self.target_vol <= 0:
            raise ValueError("target_vol must be positive")
        if not 0.0 <= self.min_weight <= self.max_weight <= 1.0:
            raise ValueError("weights must satisfy 0 <= min_weight <= max_weight <= 1")
        if self.band < 0:
            raise ValueError("band must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "VolTargetParams":
        """Build from a config table, rejecting keys this rule does not have."""
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise ValueError(f"Unknown vol_target parameters: {', '.join(unknown)}")
        return cls(
            **{
                name: int(value) if name == "vol_days" else float(value)
                for name, value in mapping.items()
            }
        )

    @property
    def warmup_days(self) -> int:
        """Closes needed before the first target exists: one more than the returns."""
        return self.vol_days + 1


def realized_volatility(closes: pd.Series, vol_days: int) -> pd.Series:
    """Annualised standard deviation of the last `vol_days` daily log returns.

    NaN until that many returns exist. The value at a row uses only that row
    and earlier ones.
    """
    returns = np.log(closes.astype(float)).diff()
    return returns.rolling(vol_days, min_periods=vol_days).std(ddof=1) * np.sqrt(TRADING_DAYS)


def vol_target_weights(closes: pd.Series, params: VolTargetParams) -> pd.DataFrame:
    """Realised volatility and the target weight for every close."""
    vol = realized_volatility(closes, params.vol_days)
    # A window of identical closes has zero volatility. That is a data gap or
    # a halted instrument, not an invitation to hold the maximum: no view.
    usable = vol.where(vol > 0)
    target = (params.target_vol / usable).clip(params.min_weight, params.max_weight)
    return pd.DataFrame({"vol": vol, "target": target})


class VolTargetStrategy(Strategy):
    """Sizes one instrument to a volatility target. Built for a single symbol:
    each symbol is sized on its own against total equity."""

    name = "vol_target"
    default_params = {field.name: field.default for field in fields(VolTargetParams)}

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.rule = VolTargetParams.from_mapping(self.params)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        needed = self.rule.warmup_days
        closes = ctx.history(bar.symbol, needed)["close"]
        if len(closes) < needed:
            return
        target = float(vol_target_weights(closes, self.rule)["target"].iloc[-1])
        if np.isnan(target):
            return
        if ctx.has_open_order(bar.symbol):
            return
        equity = ctx.equity()
        if equity <= 0:
            return

        position = ctx.position(bar.symbol)
        current = position.qty * bar.close / equity
        for intent in plan_rebalance(
            targets={bar.symbol: target},
            current_weights={bar.symbol: current},
            positions={bar.symbol: position.qty},
            band=self.rule.band,
        ):
            if intent.side == "SELL":
                ctx.sell(intent.symbol, qty=int(intent.qty))
            else:
                ctx.buy(intent.symbol, weight=float(intent.weight))
