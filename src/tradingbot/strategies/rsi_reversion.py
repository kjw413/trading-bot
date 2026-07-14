from __future__ import annotations

import pandas as pd

from tradingbot.models import Bar, Fill, OrderSide
from tradingbot.strategies.base import Strategy, StrategyContext


class RsiReversionStrategy(Strategy):
    name = "rsi_reversion"
    default_params = {
        "period": 14,
        "buy_below": 30,
        "exit_above": 55,
        "max_hold_days": 10,
        "weight": 0.20,
    }

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.holding_days: dict[str, int] = {}

    def snapshot_state(self) -> dict:
        return {"holding_days": dict(self.holding_days)}

    def restore_state(self, state: dict) -> None:
        raw = state.get("holding_days", {})
        self.holding_days = {str(symbol): int(days) for symbol, days in raw.items()}

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        period = int(self.params["period"])
        buy_below = float(self.params["buy_below"])
        exit_above = float(self.params["exit_above"])
        max_hold_days = int(self.params["max_hold_days"])
        weight = float(self.params["weight"])

        lookback = max(period * 10 + 1, period + 1)
        history = ctx.history(bar.symbol, lookback)
        if len(history) < period + 1:
            return
        rsi = _wilder_rsi(history["close"], period)
        if pd.isna(rsi):
            return

        position = ctx.position(bar.symbol)
        if position.qty > 0:
            self.holding_days[bar.symbol] = self.holding_days.get(bar.symbol, 0) + 1
        if ctx.has_open_order(bar.symbol):
            return

        if position.qty == 0 and rsi < buy_below:
            ctx.buy(bar.symbol, weight=weight)
        elif position.qty > 0 and (rsi > exit_above or self.holding_days.get(bar.symbol, 0) >= max_hold_days):
            ctx.sell(bar.symbol, qty=position.qty)

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        if fill.side is OrderSide.BUY:
            self.holding_days[fill.symbol] = 0
        elif fill.side is OrderSide.SELL:
            self.holding_days.pop(fill.symbol, None)
        self.persist_state()


def _wilder_rsi(closes: pd.Series, period: int) -> float:
    delta = closes.diff().dropna()
    if len(delta) < period:
        return float("nan")
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.iloc[:period].mean()
    avg_loss = losses.iloc[:period].mean()
    for idx in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[idx]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
