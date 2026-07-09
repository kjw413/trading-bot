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
    OrderSide,
    OrderType,
    Position,
    SessionClose,
    SessionOpen,
)
from tradingbot.strategies.base import Strategy


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_equity: float
    equity_curve: pd.DataFrame
    fills: list[Fill]

    @property
    def return_pct(self) -> float:
        if self.initial_cash == 0:
            return 0.0
        return (self.final_equity / self.initial_cash - 1) * 100

    @property
    def trade_count(self) -> int:
        return len(self.fills)


class EngineContext:
    def __init__(self, feed: HistoricalDataFeed, broker: BacktestBroker) -> None:
        self.feed = feed
        self.broker = broker
        self._current_dt: date | None = None
        self._current_bars = {}
        self._order_seq = count(1)

    def set_datetime(self, dt: date) -> None:
        self._current_dt = dt

    def set_bars(self, bars) -> None:
        self._current_bars = bars

    def history(self, symbol: str, n: int) -> pd.DataFrame:
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        return self.feed.history(symbol, self._current_dt, n)

    def position(self, symbol: str) -> Position:
        return self.broker.position(symbol.upper())

    def cash(self) -> float:
        return self.broker.cash

    def equity(self) -> float:
        return self.broker.equity

    def has_open_order(self, symbol: str) -> bool:
        symbol = symbol.upper()
        return any(order.symbol == symbol for order in self.broker.open_orders())

    def buy(self, symbol: str, qty: int | None = None, weight: float | None = None) -> Order:
        symbol = symbol.upper()
        order_qty = self._resolve_qty(symbol, qty, weight)
        return self._submit(symbol=symbol, side=OrderSide.BUY, qty=order_qty)

    def sell(self, symbol: str, qty: int) -> Order:
        return self._submit(symbol=symbol.upper(), side=OrderSide.SELL, qty=int(qty))

    def _resolve_qty(self, symbol: str, qty: int | None, weight: float | None) -> int:
        if qty is not None:
            return int(qty)
        if weight is None:
            raise ValueError("qty or weight is required")
        price = self._last_price(symbol)
        budget = max(0.0, self.cash() * float(weight))
        return int(budget // price)

    def _last_price(self, symbol: str) -> float:
        if symbol in self._current_bars:
            return float(self._current_bars[symbol].close)
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        history = self.feed.history(symbol, self._current_dt, 1)
        if history.empty:
            raise ValueError(f"No price available for {symbol}")
        return float(history.iloc[-1]["close"])

    def _submit(self, symbol: str, side: OrderSide, qty: int) -> Order:
        if self._current_dt is None:
            raise RuntimeError("Current datetime is not set")
        order = Order(
            id=f"O{next(self._order_seq):08d}",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=OrderType.MARKET,
            created_at=self._current_dt,
        )
        return self.broker.submit(order)


class BacktestEngine:
    def __init__(
        self,
        feed: HistoricalDataFeed,
        broker: BacktestBroker,
        strategy: Strategy,
    ) -> None:
        self.feed = feed
        self.broker = broker
        self.strategy = strategy
        self.context = EngineContext(feed=feed, broker=broker)
        self.equity_points: list[tuple[date, float]] = []

    def run(self) -> BacktestResult:
        self.strategy.init(self.context)

        for event in self.feed.events():
            self.context.set_datetime(event.dt)
            if isinstance(event, SessionOpen):
                fills = self.broker.on_session_open(event.dt, event.opens)
                for fill in fills:
                    self.strategy.on_fill(self.context, fill)
                self.strategy.on_open(self.context, event.dt, event.opens)
            elif isinstance(event, SessionClose):
                prices = {symbol: bar.close for symbol, bar in event.bars.items()}
                self.broker.mark_to_market(prices)
                self.context.set_bars(event.bars)
                for symbol in self.feed.symbols:
                    bar = event.bars.get(symbol)
                    if bar is not None:
                        self.strategy.on_bar(self.context, bar)
                self.equity_points.append((event.dt, self.broker.equity))

        equity_curve = pd.DataFrame(self.equity_points, columns=["date", "equity"])
        return BacktestResult(
            initial_cash=self.broker.portfolio.initial_cash,
            final_equity=self.broker.equity,
            equity_curve=equity_curve,
            fills=list(self.broker.fills),
        )
