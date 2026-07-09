from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from tradingbot.models import Bar, Fill, Order, Position


class Broker(ABC):
    @abstractmethod
    def submit(self, order: Order) -> Order:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def open_orders(self) -> list[Order]:
        raise NotImplementedError

    @abstractmethod
    def on_session_open(self, dt: date, opens: dict[str, float]) -> list[Fill]:
        raise NotImplementedError

    @abstractmethod
    def on_intraday_bars(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        raise NotImplementedError

    @abstractmethod
    def on_session_close(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        raise NotImplementedError

    @abstractmethod
    def expire_day_orders(self, dt: date) -> list[Order]:
        raise NotImplementedError

    @abstractmethod
    def mark_to_market(self, prices: dict[str, float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def position(self, symbol: str) -> Position:
        raise NotImplementedError

    @property
    @abstractmethod
    def cash(self) -> float:
        raise NotImplementedError

    @property
    @abstractmethod
    def equity(self) -> float:
        raise NotImplementedError
