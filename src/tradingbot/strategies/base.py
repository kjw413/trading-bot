from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Protocol

import pandas as pd

from tradingbot.models import Bar, Fill, Order, OrderType, Position, TimeInForce


class StrategyContext(Protocol):
    def history(self, symbol: str, n: int) -> pd.DataFrame:
        ...

    def position(self, symbol: str) -> Position:
        ...

    def cash(self) -> float:
        ...

    def equity(self) -> float:
        ...

    def has_open_order(self, symbol: str, side: str | None = None) -> bool:
        ...

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
        ...

    def sell(
        self,
        symbol: str,
        qty: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        ...


class Strategy(ABC):
    name: ClassVar[str]
    default_params: ClassVar[dict] = {}

    def __init__(self, **params) -> None:
        self.params = {**self.default_params, **params}

    def init(self, ctx: StrategyContext) -> None:
        pass

    def on_open(self, ctx: StrategyContext, dt, opens: dict[str, float]) -> None:
        pass

    @abstractmethod
    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        raise NotImplementedError

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        pass
