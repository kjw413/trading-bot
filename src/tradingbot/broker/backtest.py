from __future__ import annotations

from datetime import date

from tradingbot.broker.base import Broker
from tradingbot.broker.fees import FeeModel, apply_slippage, round_execution_price
from tradingbot.models import (
    Bar,
    Fill,
    Order,
    OrderPhase,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from tradingbot.portfolio import Portfolio


class BacktestBroker(Broker):
    def __init__(
        self,
        initial_cash: float,
        market: str = "KR",
        fee_model: FeeModel | None = None,
        commission_rate: float | None = None,
        slippage_bps: float = 0.0,
    ) -> None:
        self.market = market.upper()
        if fee_model is None:
            fee_model = FeeModel(self.market, commission_rate=float(commission_rate or 0.0))
        self.fee_model = fee_model
        self.slippage_bps = float(slippage_bps)
        self.portfolio = Portfolio(initial_cash=initial_cash)
        self._open_orders: list[Order] = []
        self.fills: list[Fill] = []
        self.rejected_orders: list[Order] = []
        self.expired_orders: list[Order] = []

    def submit(self, order: Order) -> Order:
        order.symbol = order.symbol.upper()
        if order.qty <= 0:
            return self._reject(order, "quantity must be positive")
        if order.order_type is OrderType.LIMIT and order.limit_price is None:
            return self._reject(order, "LIMIT order requires limit_price")
        if order.order_type is OrderType.STOP and order.stop_price is None:
            return self._reject(order, "STOP order requires stop_price")
        self._open_orders.append(order)
        return order

    def cancel(self, order_id: str) -> bool:
        for order in self._open_orders:
            if order.id == order_id and order.status is OrderStatus.OPEN:
                order.status = OrderStatus.CANCELED
                return True
        return False

    def open_orders(self) -> list[Order]:
        return [order for order in self._open_orders if order.status is OrderStatus.OPEN]

    def on_session_open(self, dt: date, opens: dict[str, float]) -> list[Fill]:
        return self._process_orders(dt, opens, {OrderType.MARKET})

    def on_intraday_bars(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        fills: list[Fill] = []
        remaining: list[Order] = []
        for order in self._open_orders:
            if order.status is not OrderStatus.OPEN:
                continue
            if order.order_type not in {OrderType.LIMIT, OrderType.STOP}:
                remaining.append(order)
                continue
            bar = bars.get(order.symbol)
            if bar is None:
                remaining.append(order)
                continue
            trigger_price = self._trigger_price(order, bar)
            if trigger_price is None:
                remaining.append(order)
                continue
            fill = self._fill(order, dt, trigger_price)
            if fill is not None:
                fills.append(fill)
        self._open_orders = remaining
        self.fills.extend(fills)
        return fills

    def on_session_close(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        prices = {symbol: bar.close for symbol, bar in bars.items()}
        return self._process_orders(dt, prices, {OrderType.MOC}, require_same_day_moc=True)

    def expire_day_orders(self, dt: date) -> list[Order]:
        expired: list[Order] = []
        remaining: list[Order] = []
        for order in self._open_orders:
            if order.status is not OrderStatus.OPEN:
                continue
            if self._should_expire_day_order(order, dt):
                order.status = OrderStatus.EXPIRED
                expired.append(order)
                continue
            remaining.append(order)
        self._open_orders = remaining
        self.expired_orders.extend(expired)
        return expired

    def _should_expire_day_order(self, order: Order, dt: date) -> bool:
        if order.tif is not TimeInForce.DAY or order.created_at is None:
            return False
        if order.created_at < dt:
            return True
        if order.created_at > dt:
            return False
        if order.order_type is OrderType.MARKET:
            return False
        return order.created_phase is not OrderPhase.CLOSE

    def _process_orders(
        self,
        dt: date,
        prices: dict[str, float],
        order_types: set[OrderType],
        *,
        require_same_day_moc: bool = False,
    ) -> list[Fill]:
        fills: list[Fill] = []
        remaining: list[Order] = []
        for order in self._open_orders:
            if order.status is not OrderStatus.OPEN:
                continue
            if order.order_type not in order_types:
                remaining.append(order)
                continue
            if require_same_day_moc and not self._can_fill_moc(order, dt):
                remaining.append(order)
                continue
            if order.symbol not in prices:
                remaining.append(order)
                continue
            fill = self._fill(order, dt, float(prices[order.symbol]))
            if fill is not None:
                fills.append(fill)
        self._open_orders = remaining
        self.fills.extend(fills)
        return fills

    def _can_fill_moc(self, order: Order, dt: date) -> bool:
        if order.order_type is not OrderType.MOC:
            return True
        if order.created_at != dt:
            return False
        return order.created_phase in {OrderPhase.OPEN, OrderPhase.INTRADAY, OrderPhase.MOC, None}

    def _trigger_price(self, order: Order, bar: Bar) -> float | None:
        if order.order_type is OrderType.STOP:
            stop = float(order.stop_price)
            if order.side is OrderSide.BUY and bar.high >= stop:
                return max(stop, bar.open)
            if order.side is OrderSide.SELL and bar.low <= stop:
                return min(stop, bar.open)
        elif order.order_type is OrderType.LIMIT:
            limit = float(order.limit_price)
            if order.side is OrderSide.BUY and bar.low <= limit:
                return min(limit, bar.open)
            if order.side is OrderSide.SELL and bar.high >= limit:
                return max(limit, bar.open)
        return None

    def _fill(self, order: Order, dt: date, base_price: float) -> Fill | None:
        qty = self._fillable_qty(order)
        if qty <= 0:
            self._reject(order, "no quantity available to fill")
            return None

        slipped = apply_slippage(base_price, order.side, self.slippage_bps)
        price = round_execution_price(self.market, slipped, order.side)
        fee = self.fee_model.calculate(order.side, qty, price)
        gross = price * qty
        if order.side is OrderSide.BUY and gross + fee > self.cash + 1e-9:
            self._reject(order, "insufficient cash")
            return None

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
        return fill

    def _fillable_qty(self, order: Order) -> int:
        if order.side is OrderSide.BUY:
            return order.qty
        return min(order.qty, self.position(order.symbol).qty)

    def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        self.rejected_orders.append(order)
        return order

    def mark_to_market(self, prices: dict[str, float]) -> None:
        self.portfolio.mark_to_market(prices)

    def position(self, symbol: str) -> Position:
        return self.portfolio.position(symbol.upper())

    @property
    def cash(self) -> float:
        return float(self.portfolio.cash)

    @property
    def equity(self) -> float:
        return self.portfolio.equity
