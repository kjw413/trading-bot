from __future__ import annotations

from typing import Callable

from tradingbot.factors.base import Factor
from tradingbot.factors.momentum import MomentumFactor

_FACTORIES: dict[str, Callable[[], Factor]] = {}


def register_factor(name: str, factory: Callable[[], Factor]) -> None:
    if name in _FACTORIES:
        raise ValueError(f"Factor already registered: {name}")
    _FACTORIES[name] = factory


def get_factor(name: str) -> Factor:
    try:
        factory = _FACTORIES[name]
    except KeyError as exc:
        available = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"Unknown factor: {name}. Available: {available}") from exc
    return factory()


def list_factors() -> list[str]:
    return sorted(_FACTORIES)


register_factor("momentum_3m", lambda: MomentumFactor(3))
register_factor("momentum_6m", lambda: MomentumFactor(6))
register_factor("momentum_12m", lambda: MomentumFactor(12))
register_factor("momentum_12m_ex1m", lambda: MomentumFactor(12, skip_months=1))

from tradingbot.factors.flow import NetBuyIntensityFactor
from tradingbot.factors.value import BookToMarketFactor, EarningsYieldFactor

register_factor("foreign_net_20d", lambda: NetBuyIntensityFactor("foreign", 20))
register_factor("foreign_net_60d", lambda: NetBuyIntensityFactor("foreign", 60))
register_factor("institution_net_20d", lambda: NetBuyIntensityFactor("institution", 20))
register_factor("earnings_yield", lambda: EarningsYieldFactor())
register_factor("book_to_market", lambda: BookToMarketFactor())
