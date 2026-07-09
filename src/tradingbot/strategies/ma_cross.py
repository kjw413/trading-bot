from __future__ import annotations

import pandas as pd

from tradingbot.models import Bar
from tradingbot.strategies.base import Strategy, StrategyContext


class MovingAverageCrossStrategy(Strategy):
    name = "ma_cross"
    default_params = {
        "fast": 20,
        "slow": 60,
        "weight": 0.20,
    }

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        weight = float(self.params["weight"])
        if fast <= 0 or slow <= 0 or fast >= slow:
            raise ValueError("ma_cross requires 0 < fast < slow")

        history = ctx.history(bar.symbol, slow + 1)
        if len(history) < slow + 1:
            return

        closes = history["close"]
        fast_ma = closes.rolling(fast).mean()
        slow_ma = closes.rolling(slow).mean()
        prev_fast, curr_fast = fast_ma.iloc[-2], fast_ma.iloc[-1]
        prev_slow, curr_slow = slow_ma.iloc[-2], slow_ma.iloc[-1]
        if any(pd.isna(value) for value in (prev_fast, curr_fast, prev_slow, curr_slow)):
            return

        crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
        crossed_down = prev_fast >= prev_slow and curr_fast < curr_slow
        position = ctx.position(bar.symbol)

        if ctx.has_open_order(bar.symbol):
            return
        if position.qty == 0 and crossed_up:
            ctx.buy(bar.symbol, weight=weight)
        elif position.qty > 0 and crossed_down:
            ctx.sell(bar.symbol, qty=position.qty)
