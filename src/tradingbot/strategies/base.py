from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Protocol

import pandas as pd

from tradingbot.models import Bar, Fill, Order, OrderType, Position, TimeInForce
from tradingbot.strategies.state import StrategyStateStore


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
        self._state_store: StrategyStateStore | None = None

    def bind_state_store(self, store: StrategyStateStore) -> None:
        """Attach a persistence store and restore any previously saved state."""
        self._state_store = store
        self.restore_state(store.load(self.name))

    def snapshot_state(self) -> dict:
        """Return the strategy's persistable state. Override in subclasses."""
        return {}

    def restore_state(self, state: dict) -> None:
        """Restore state produced by snapshot_state. Override in subclasses."""

    def persist_state(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self.name, self.snapshot_state())

    def init(self, ctx: StrategyContext) -> None:
        pass

    def on_open(self, ctx: StrategyContext, dt, opens: dict[str, float]) -> None:
        pass

    @abstractmethod
    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        raise NotImplementedError

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        pass
