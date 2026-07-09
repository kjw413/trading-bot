from __future__ import annotations

from datetime import date

from tradingbot.broker.base import Broker
from tradingbot.models import Fill, Order, OrderSide, OrderStatus, OrderType, Position
from tradingbot.portfolio import Portfolio


class BacktestBroker(Broker):
    def __init__(self, initial_cash: float, commission_rate: float = 0.00015) -> None:
        self.portfolio = Portfolio(initial_cash=initial_cash)
        self.commission_rate = float(commission_rate)
        self._open_orders: list[Order] = []
        self.fills: list[Fill] = []
        self.rejected_orders: list[Order] = []

    def submit(self, order: Order) -> Order:
        if order.qty <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "quantity must be positive"
            self.rejected_orders.append(order)
            return order
        if order.order_type is not OrderType.MARKET:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "M1 BacktestBroker supports MARKET orders only"
            self.rejected_orders.append(order)
            return order
        self._open_orders.append(order)
        return order

    def open_orders(self) -> list[Order]:
        return [order for order in self._open_orders if order.status is OrderStatus.OPEN]

    def on_session_open(self, dt: date, opens: dict[str, float]) -> list[Fill]:
        fills: list[Fill] = []
        remaining: list[Order] = []

        for order in self._open_orders:
            if order.status is not OrderStatus.OPEN:
                continue
            if order.symbol not in opens:
                remaining.append(order)
                continue

            price = float(opens[order.symbol])
            qty = self._fillable_qty(order)
            if qty <= 0:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "no quantity available to fill"
                self.rejected_orders.append(order)
                continue

            gross = price * qty
            fee = gross * self.commission_rate
            if order.side is OrderSide.BUY and gross + fee > self.cash + 1e-9:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "insufficient cash"
                self.rejected_orders.append(order)
                continue

            fill = Fill(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                qty=qty,
                price=price,
                fee=fee,
                dt=dt,
            )
            self.portfolio.apply_fill(fill)
            order.status = OrderStatus.FILLED
            fills.append(fill)

        self._open_orders = remaining
        self.fills.extend(fills)
        return fills

    def _fillable_qty(self, order: Order) -> int:
        if order.side is OrderSide.BUY:
            return order.qty
        return min(order.qty, self.position(order.symbol).qty)

    def mark_to_market(self, prices: dict[str, float]) -> None:
        self.portfolio.mark_to_market(prices)

    def position(self, symbol: str) -> Position:
        return self.portfolio.position(symbol)

    @property
    def cash(self) -> float:
        return float(self.portfolio.cash)

    @property
    def equity(self) -> float:
        return self.portfolio.equity
