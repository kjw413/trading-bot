from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import count

import pandas as pd

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.models import (
    Fill,
    Order,
    OrderPhase,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SessionClose,
    SessionOpen,
    TimeInForce,
)
from tradingbot.risk import RiskManager
from tradingbot.strategies.base import Strategy


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_equity: float
    equity_curve: pd.DataFrame
    fills: list[Fill]
    rejected_orders: list[Order]
    expired_orders: list[Order]

    @property
    def return_pct(self) -> float:
        if self.initial_cash == 0:
            return 0.0
        return (self.final_equity / self.initial_cash - 1) * 100

    @property
    def trade_count(self) -> int:
        return len(self.fills)


class EngineContext:
    def __init__(
        self,
        feed: HistoricalDataFeed,
        broker: BacktestBroker,
        risk_manager: RiskManager | None = None,
        order_start: int = 1,
    ) -> None:
        self.feed = feed
        self.broker = broker
        self.risk_manager = risk_manager
        self._current_dt: date | None = None
        self._current_bars = {}
        self._current_opens = {}
        self._phase = OrderPhase.CLOSE
        self._order_seq = count(order_start)

    def set_datetime(self, dt: date) -> None:
        self._current_dt = dt

    def set_bars(self, bars) -> None:
        self._current_bars = bars

    def set_opens(self, opens) -> None:
        self._current_opens = opens

    def set_phase(self, phase: OrderPhase) -> None:
        self._phase = phase

    def history(self, symbol: str, n: int) -> pd.DataFrame:
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        symbol = symbol.upper()
        include_current = self._phase is OrderPhase.CLOSE
        history = self.feed.history(symbol, self._current_dt, n, include_current=include_current)
        if include_current and symbol in self._current_bars:
            bar = self._current_bars[symbol]
            key = pd.Timestamp(bar.dt)
            if key not in history.index:
                current = pd.DataFrame(
                    [{"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume}],
                    index=[key],
                )
                history = pd.concat([history, current]).sort_index().tail(n)
        return history

    def position(self, symbol: str) -> Position:
        return self.broker.position(symbol.upper())

    def cash(self) -> float:
        return self.broker.cash

    def equity(self) -> float:
        return self.broker.equity

    def has_open_order(self, symbol: str, side: str | None = None) -> bool:
        symbol = symbol.upper()
        side_enum = OrderSide(side.upper()) if side else None
        return any(
            order.symbol == symbol and (side_enum is None or order.side is side_enum)
            for order in self.broker.open_orders()
        )

    def buy(
        self,
        symbol: str,
        qty: int | None = None,
        weight: float | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        symbol = symbol.upper()
        estimated_price = self._estimate_price(symbol, order_type, limit_price, stop_price)
        order_qty = self._resolve_qty(estimated_price, qty, weight)
        return self._submit(
            symbol=symbol,
            side=OrderSide.BUY,
            qty=order_qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            estimated_price=estimated_price,
        )

    def sell(
        self,
        symbol: str,
        qty: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        symbol = symbol.upper()
        estimated_price = self._estimate_price(symbol, order_type, limit_price, stop_price)
        return self._submit(
            symbol=symbol,
            side=OrderSide.SELL,
            qty=int(qty),
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            estimated_price=estimated_price,
        )

    def _resolve_qty(self, estimated_price: float, qty: int | None, weight: float | None) -> int:
        if qty is not None:
            return int(qty)
        if weight is None:
            raise ValueError("qty or weight is required")
        budget = max(0.0, self.equity() * float(weight))
        return int(budget // estimated_price)

    def _estimate_price(
        self,
        symbol: str,
        order_type: OrderType,
        limit_price: float | None,
        stop_price: float | None,
    ) -> float:
        if order_type is OrderType.LIMIT and limit_price is not None:
            return float(limit_price)
        if order_type is OrderType.STOP and stop_price is not None:
            return float(stop_price)
        return self._last_price(symbol)

    def _last_price(self, symbol: str) -> float:
        if symbol in self._current_bars:
            return float(self._current_bars[symbol].close)
        if symbol in self._current_opens:
            return float(self._current_opens[symbol])
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        history = self.feed.history(symbol, self._current_dt, 1, include_current=self._phase is OrderPhase.CLOSE)
        if history.empty:
            raise ValueError(f"No price available for {symbol}")
        return float(history.iloc[-1]["close"])

    def _submit(
        self,
        symbol: str,
        side: OrderSide,
        qty: int,
        order_type: OrderType,
        limit_price: float | None,
        stop_price: float | None,
        tif: TimeInForce,
        estimated_price: float,
    ) -> Order:
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        order = Order(
            id=f"O{next(self._order_seq):08d}",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            tif=tif,
            created_at=self._current_dt,
            created_phase=self._phase,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        if self.risk_manager is not None:
            reason = self.risk_manager.validate(order, self.broker, estimated_price)
            if reason is not None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = reason
                self.broker.rejected_orders.append(order)
                return order
        return self.broker.submit(order)


class BacktestEngine:
    def __init__(
        self,
        feed: HistoricalDataFeed,
        broker: BacktestBroker,
        strategy: Strategy,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self.feed = feed
        self.broker = broker
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.context = EngineContext(feed=feed, broker=broker, risk_manager=risk_manager)
        self.equity_points: list[tuple[date, float]] = []

    def run(self) -> BacktestResult:
        self.strategy.init(self.context)

        for event in self.feed.events():
            self.context.set_datetime(event.dt)
            if isinstance(event, SessionOpen):
                self._handle_open(event)
            elif isinstance(event, SessionClose):
                self._handle_close(event)

        equity_curve = pd.DataFrame(self.equity_points, columns=["date", "equity"])
        return BacktestResult(
            initial_cash=self.broker.portfolio.initial_cash,
            final_equity=self.broker.equity,
            equity_curve=equity_curve,
            fills=list(self.broker.fills),
            rejected_orders=list(self.broker.rejected_orders),
            expired_orders=list(self.broker.expired_orders),
        )

    def _handle_open(self, event: SessionOpen) -> None:
        self.context.set_phase(OrderPhase.OPEN)
        self.context.set_opens(event.opens)
        fills = self.broker.on_session_open(event.dt, event.opens)
        self.broker.mark_to_market(event.opens)
        self._notify_fills(fills)
        if self.risk_manager is not None:
            self.risk_manager.start_day(self.broker.equity)
        self.strategy.on_open(self.context, event.dt, event.opens)

    def _handle_close(self, event: SessionClose) -> None:
        self.context.set_bars(event.bars)
        close_prices = {symbol: bar.close for symbol, bar in event.bars.items()}
        self.broker.mark_to_market(close_prices)

        self.context.set_phase(OrderPhase.INTRADAY)
        intraday_fills = self.broker.on_intraday_bars(event.dt, event.bars)
        self._notify_fills(intraday_fills)

        self.context.set_phase(OrderPhase.MOC)
        moc_fills = self.broker.on_session_close(event.dt, event.bars)
        self._notify_fills(moc_fills)
        self.broker.expire_day_orders(event.dt)
        self.broker.mark_to_market(close_prices)

        if self.risk_manager is not None:
            self.risk_manager.update_daily_loss(self.broker.equity)

        self.context.set_phase(OrderPhase.CLOSE)
        for symbol in self.feed.symbols:
            bar = event.bars.get(symbol)
            if bar is not None:
                self.strategy.on_bar(self.context, bar)

        if self.risk_manager is not None:
            for symbol in self.risk_manager.stop_loss_symbols(self.broker, event.bars):
                position = self.broker.position(symbol)
                if position.qty > 0 and not self.context.has_open_order(symbol, side="SELL"):
                    self.context.sell(symbol, qty=position.qty)

        self.equity_points.append((event.dt, self.broker.equity))

    def _notify_fills(self, fills: list[Fill]) -> None:
        for fill in fills:
            self.strategy.on_fill(self.context, fill)
