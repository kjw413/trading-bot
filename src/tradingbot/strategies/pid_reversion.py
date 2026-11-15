"""PID-style position sizing around a fitted trend, for one long-biased instrument.

The idea being formalised: a quality index trends upward, so treat that trend
as a setpoint and size the position by how the price is behaving relative to
it — where it is now, where it has been, and how fast it is moving.

    error  z_t  how far today's close sits from the trend line, in units of
                the scatter the price has shown around that line
    P      z_t                              where the price is now
    I      mean of z over `integral_days`   where it has been
    D      z_t - z_(t - derivative_days)    how fast it is moving

    u_t      = kp*P + ki*I + kd*D
    target_t = clip(base_weight - u_t, min_weight, max_weight)

Positive `u` means stretched above trend, so the target falls below the base
weight; negative `u` raises it. The two cases the design was asked to cover
fall out of the signs. A sudden jump off a quiet trend makes P and D positive
together while I is still near zero, so the target drops sharply. A bounce
inside a long slump makes P or D positive while I is still negative, so I
offsets them and the position is kept.

The trend line is an ordinary least-squares fit of log price on time over the
`trend_days` closes *before* today, extended one step forward. Today's close
never enters its own setpoint: a line fitted through the jump it is supposed
to be judging would absorb part of it.

Two anti-windup guards, borrowed from the controllers this imitates. The
error is clipped at `z_cap` before it is averaged, so one gap cannot dominate
the integral for months; and the integral is a finite window rather than a
running sum, so a regime the trend line has not caught up with eventually
stops being counted against the price.

Nothing here claims the sizing adds value. That is measured separately
(`research/pid_sweep.py`) against holding the same average amount statically,
because a rule that simply holds less of a volatile asset looks safer than
buy-and-hold without having timed anything.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

import numpy as np
import pandas as pd

from tradingbot.allocation.rebalance import plan_rebalance
from tradingbot.models import Bar
from tradingbot.strategies.base import Strategy, StrategyContext

# Purely numerical: a window that is an exact straight line has zero scatter,
# and dividing by it would turn rounding noise into an infinite error.
_SIGMA_FLOOR = 1e-9


@dataclass(frozen=True)
class PidParams:
    trend_days: int = 250
    integral_days: int = 120
    derivative_days: int = 10
    kp: float = 0.2
    ki: float = 0.2
    kd: float = 0.2
    base_weight: float = 0.75
    min_weight: float = 0.0
    # Not 1.0: the engine keeps a 2% cash buffer, and a target it can never
    # reach would have the strategy re-ordering the same shortfall every day.
    max_weight: float = 0.98
    # Differences smaller than this are left alone. The target moves every
    # day; trading each wiggle would pay costs for no change in view.
    band: float = 0.10
    z_cap: float = 4.0

    def __post_init__(self) -> None:
        # Two points fit a line exactly, leaving no scatter to scale by.
        if self.trend_days < 3:
            raise ValueError("trend_days must be at least 3")
        if self.integral_days < 1 or self.derivative_days < 1:
            raise ValueError("integral_days and derivative_days must be positive")
        if not 0.0 <= self.min_weight <= self.max_weight <= 1.0:
            raise ValueError("weights must satisfy 0 <= min_weight <= max_weight <= 1")
        if self.band < 0:
            raise ValueError("band must be non-negative")
        if self.z_cap <= 0:
            raise ValueError("z_cap must be positive")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PidParams":
        """Build from a config table, rejecting keys this controller does not have.

        A misspelt gain would otherwise be ignored and the default used in its
        place, and the backtest would report on a controller nobody configured.
        """
        known = {field.name: field.type for field in fields(cls)}
        unknown = sorted(set(mapping) - set(known))
        if unknown:
            raise ValueError(f"Unknown pid_reversion parameters: {', '.join(unknown)}")
        integer = {"trend_days", "integral_days", "derivative_days"}
        return cls(
            **{
                name: int(value) if name in integer else float(value)
                for name, value in mapping.items()
            }
        )

    @property
    def warmup_days(self) -> int:
        """Closes needed before the first target exists.

        A term whose gain is zero is never read, so it does not extend the
        wait: a P-only controller should not sit out an integral window.
        """
        tail = 1
        if self.ki != 0:
            tail = max(tail, self.integral_days)
        if self.kd != 0:
            tail = max(tail, self.derivative_days + 1)
        return self.trend_days + tail


def trend_zscore(closes: pd.Series, trend_days: int, z_cap: float) -> pd.Series:
    """Each close's distance from the trend fitted on the closes before it.

    NaN for the first `trend_days` rows, which have no full window behind
    them. The value at row t depends only on rows up to t.
    """
    values = np.log(closes.to_numpy(dtype=float))
    out = np.full(len(values), np.nan)
    if len(values) <= trend_days:
        return pd.Series(out, index=closes.index, name="z")

    # Row i of `windows` is closes[i : i + trend_days]; it scores close i + trend_days.
    windows = np.lib.stride_tricks.sliding_window_view(values, trend_days)[:-1]
    x = np.arange(trend_days, dtype=float)
    x_centered = x - x.mean()
    mean = windows.mean(axis=1)
    centered = windows - mean[:, None]
    slope = centered @ x_centered / (x_centered @ x_centered)
    forecast = mean + slope * (trend_days - x.mean())
    sse = np.einsum("ij,ij->i", centered, centered) - slope * (centered @ x_centered)
    sigma = np.sqrt(np.maximum(sse, 0.0) / (trend_days - 2))
    z = (values[trend_days:] - forecast) / np.maximum(sigma, _SIGMA_FLOOR)
    out[trend_days:] = np.clip(z, -z_cap, z_cap)
    return pd.Series(out, index=closes.index, name="z")


def pid_terms(z: pd.Series, integral_days: int, derivative_days: int) -> pd.DataFrame:
    """The three views of the error the controller weighs."""
    return pd.DataFrame(
        {
            "p": z,
            "i": z.rolling(integral_days, min_periods=integral_days).mean(),
            "d": z - z.shift(derivative_days),
        }
    )


def target_weights(closes: pd.Series, params: PidParams) -> pd.DataFrame:
    """Error, terms, controller output and target weight for every close."""
    z = trend_zscore(closes, params.trend_days, params.z_cap)
    frame = pid_terms(z, params.integral_days, params.derivative_days)
    output = pd.Series(0.0, index=closes.index)
    for gain, column in ((params.kp, "p"), (params.ki, "i"), (params.kd, "d")):
        if gain != 0:
            # NaN propagates: a term that is in use but not yet measurable
            # leaves the target unknown rather than silently counted as zero.
            output = output + gain * frame[column]
    # A P-less controller still needs the trend window to exist.
    output = output.where(z.notna())
    frame.insert(0, "z", z)
    frame["u"] = output
    frame["target"] = (params.base_weight - output).clip(params.min_weight, params.max_weight)
    return frame


class PidReversionStrategy(Strategy):
    """Holds one instrument at the weight the controller asks for.

    Stateless on purpose. Every target is recomputed from price history, so a
    restart resumes with exactly the view it had and there is nothing to
    persist or to corrupt.

    Each symbol is sized on its own against total equity, so with more than
    one symbol the base weights add up. This is built and measured for a
    single instrument.
    """

    name = "pid_reversion"
    default_params = {field.name: field.default for field in fields(PidParams)}

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.pid = PidParams.from_mapping(self.params)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        needed = self.pid.warmup_days
        closes = ctx.history(bar.symbol, needed)["close"]
        if len(closes) < needed:
            return
        target = float(target_weights(closes, self.pid)["target"].iloc[-1])
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
            band=self.pid.band,
        ):
            if intent.side == "SELL":
                ctx.sell(intent.symbol, qty=int(intent.qty))
            else:
                ctx.buy(intent.symbol, weight=float(intent.weight))
