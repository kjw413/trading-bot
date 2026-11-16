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

**The stop.** With `max_drawdown_stop` above zero, the rule tracks the
account's highest closing equity. A close more than that fraction below the
peak sells everything at the next open, and the rule never buys again until
an operator clears its saved state. This is not part of the sizing and is not
expected to trigger: the risk level is calibrated so the worst historical fall
stays inside the limit. It is the line past which that calibration is known to
have failed, and the right response to a failed calibration is to stop and
look, not to keep trading it.

The stop acts on the close and fills at the next open, so a gap down overnight
is taken in full. The realised loss can exceed the limit.

Targets carry no state; the stop's peak and its halted flag do, and are
persisted through the strategy state store so a restart cannot forget them.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

import numpy as np
import pandas as pd

from tradingbot.allocation.rebalance import plan_rebalance
from tradingbot.models import Bar
from tradingbot.strategies.base import Strategy, StrategyContext
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

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
    # Fraction below the highest closing equity at which the rule sells out
    # and stops. Zero disables it.
    max_drawdown_stop: float = 0.0

    def __post_init__(self) -> None:
        if self.vol_days < 2:
            raise ValueError("vol_days must be at least 2")
        if self.target_vol <= 0:
            raise ValueError("target_vol must be positive")
        if not 0.0 <= self.min_weight <= self.max_weight <= 1.0:
            raise ValueError("weights must satisfy 0 <= min_weight <= max_weight <= 1")
        if self.band < 0:
            raise ValueError("band must be non-negative")
        if not 0.0 <= self.max_drawdown_stop < 1.0:
            raise ValueError("max_drawdown_stop must be in [0, 1)")

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
        self.peak_equity: float | None = None
        self.halted = False
        self.halted_on: str | None = None
        self.halted_equity: float | None = None

    def snapshot_state(self) -> dict:
        return {
            "peak_equity": self.peak_equity,
            "halted": self.halted,
            "halted_on": self.halted_on,
            "halted_equity": self.halted_equity,
        }

    def restore_state(self, state: dict) -> None:
        peak = state.get("peak_equity")
        self.peak_equity = float(peak) if peak is not None else None
        self.halted = bool(state.get("halted", False))
        self.halted_on = state.get("halted_on")
        equity = state.get("halted_equity")
        self.halted_equity = float(equity) if equity is not None else None

    def _check_stop(self, ctx: StrategyContext, bar: Bar) -> bool:
        """Update the peak; return True when the rule must hold nothing."""
        limit = self.rule.max_drawdown_stop
        if limit <= 0:
            return False
        equity = ctx.equity()
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if not self.halted and equity < self.peak_equity * (1 - limit):
            self.halted = True
            self.halted_on = bar.dt.isoformat()
            self.halted_equity = equity
            LOGGER.warning(
                "vol_target stopped on %s: equity %.2f is %.1f%% below its peak %.2f "
                "(limit %.0f%%). Selling out; no further buys until the strategy state is cleared.",
                self.halted_on,
                equity,
                (1 - equity / self.peak_equity) * 100,
                self.peak_equity,
                limit * 100,
            )
            self.persist_state()
        return self.halted

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        if self._check_stop(ctx, bar):
            position = ctx.position(bar.symbol)
            if position.qty > 0 and not ctx.has_open_order(bar.symbol):
                ctx.sell(bar.symbol, qty=position.qty)
            return

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
