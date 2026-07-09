from __future__ import annotations

from tradingbot.models import Bar, Fill, OrderSide, OrderType, TimeInForce
from tradingbot.strategies.base import Strategy, StrategyContext


class VolatilityBreakoutStrategy(Strategy):
    name = "vol_breakout"
    default_params = {
        "k": 0.5,
        "weight": 0.20,
        "exit": "moc",
    }

    def on_open(self, ctx: StrategyContext, dt, opens: dict[str, float]) -> None:
        k = float(self.params["k"])
        weight = float(self.params["weight"])
        for symbol, open_price in opens.items():
            if ctx.position(symbol).qty > 0 or ctx.has_open_order(symbol):
                continue
            history = ctx.history(symbol, 1)
            if history.empty:
                continue
            prev = history.iloc[-1]
            target = float(open_price) + k * (float(prev["high"]) - float(prev["low"]))
            ctx.buy(
                symbol,
                weight=weight,
                order_type=OrderType.STOP,
                stop_price=target,
                tif=TimeInForce.DAY,
            )

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        pass

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        if fill.side is not OrderSide.BUY:
            return
        exit_mode = str(self.params.get("exit", "moc")).lower()
        if exit_mode == "moc":
            ctx.sell(fill.symbol, qty=fill.qty, order_type=OrderType.MOC, tif=TimeInForce.DAY)
        elif exit_mode == "next_open":
            ctx.sell(fill.symbol, qty=fill.qty, order_type=OrderType.MARKET, tif=TimeInForce.DAY)
