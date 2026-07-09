from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Market(str, Enum):
    KR = "KR"
    US = "US"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    MOC = "MOC"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Bar:
    symbol: str
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    qty: int
    order_type: OrderType = OrderType.MARKET
    tif: TimeInForce = TimeInForce.DAY
    created_at: date | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.OPEN
    reject_reason: str | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    fee: float
    dt: date

    @property
    def gross_value(self) -> float:
        return self.qty * self.price


@dataclass
class Position:
    symbol: str
    qty: int = 0
    avg_price: float = 0.0
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * self.last_price


@dataclass(frozen=True)
class SessionOpen:
    dt: date
    opens: dict[str, float]


@dataclass(frozen=True)
class PriceTick:
    dt: date
    prices: dict[str, float]


@dataclass(frozen=True)
class SessionClose:
    dt: date
    bars: dict[str, Bar]
