from __future__ import annotations

from dataclasses import dataclass, field

from tradingbot.models import Fill, OrderSide, Position


@dataclass
class Portfolio:
    initial_cash: float
    cash: float | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.cash is None:
            self.cash = float(self.initial_cash)

    def position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(symbol=symbol))

    def apply_fill(self, fill: Fill) -> None:
        if fill.qty <= 0:
            raise ValueError("fill qty must be positive")

        position = self.positions.get(fill.symbol, Position(symbol=fill.symbol))
        gross = fill.gross_value

        if fill.side is OrderSide.BUY:
            new_qty = position.qty + fill.qty
            if new_qty <= 0:
                raise ValueError("buy fill produced invalid position")
            position.avg_price = (
                (position.avg_price * position.qty) + gross
            ) / new_qty
            position.qty = new_qty
            position.last_price = fill.price
            self.cash = float(self.cash) - gross - fill.fee
        else:
            if fill.qty > position.qty:
                raise ValueError("sell fill exceeds current position")
            self.realized_pnl += (fill.price - position.avg_price) * fill.qty - fill.fee
            position.qty -= fill.qty
            position.last_price = fill.price
            self.cash = float(self.cash) + gross - fill.fee
            if position.qty == 0:
                position.avg_price = 0.0

        if position.qty == 0:
            self.positions.pop(fill.symbol, None)
        else:
            self.positions[fill.symbol] = position

    def mark_to_market(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            if symbol in self.positions:
                self.positions[symbol].last_price = float(price)

    @property
    def equity(self) -> float:
        return float(self.cash) + sum(p.market_value for p in self.positions.values())
