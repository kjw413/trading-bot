from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.models import OrderSide


@dataclass(frozen=True)
class FeeModel:
    market: str
    commission_rate: float = 0.0
    sell_tax_rate: float = 0.0
    sec_fee_rate: float = 0.0
    finra_taf_per_share: float = 0.0
    finra_taf_cap: float = 0.0

    @classmethod
    def from_config(cls, market: str, config: dict) -> "FeeModel":
        market = market.upper()
        fees = config.get("fees", {}).get(market, {})
        return cls(
            market=market,
            commission_rate=float(fees.get("commission_rate", 0.0)),
            sell_tax_rate=float(fees.get("sell_tax_rate", 0.0)),
            sec_fee_rate=float(fees.get("sec_fee_rate", 0.0)),
            finra_taf_per_share=float(fees.get("finra_taf_per_share", 0.0)),
            finra_taf_cap=float(fees.get("finra_taf_cap", 0.0)),
        )

    def calculate(self, side: OrderSide, qty: int, price: float) -> float:
        gross = qty * price
        fee = gross * self.commission_rate
        if side is OrderSide.SELL:
            if self.market == "KR":
                fee += gross * self.sell_tax_rate
            elif self.market == "US":
                fee += gross * self.sec_fee_rate
                taf = qty * self.finra_taf_per_share
                if self.finra_taf_cap > 0:
                    taf = min(taf, self.finra_taf_cap)
                fee += taf
        return fee


def apply_slippage(price: float, side: OrderSide, slippage_bps: float) -> float:
    if slippage_bps <= 0:
        return price
    adjustment = slippage_bps / 10_000
    if side is OrderSide.BUY:
        return price * (1 + adjustment)
    return price * (1 - adjustment)


def round_execution_price(market: str, price: float, side: OrderSide) -> float:
    tick = tick_size(market, price)
    if side is OrderSide.BUY:
        return math.ceil((price - 1e-12) / tick) * tick
    return math.floor((price + 1e-12) / tick) * tick


def tick_size(market: str, price: float) -> float:
    if price <= 0:
        raise ValueError("price must be positive")
    if market.upper() == "US":
        return 0.01
    if market.upper() != "KR":
        raise ValueError(f"Unsupported market: {market}")
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000
