from __future__ import annotations

from typing import Callable

from tradingbot.factors.base import Factor
from tradingbot.factors.flow import NetBuyIntensityFactor
from tradingbot.factors.momentum import BlendedMomentumFactor, MomentumFactor
from tradingbot.factors.short_interest import (
    FlowCrowdingFactor,
    ShortBalanceChangeFactor,
    ShortBalanceRatioFactor,
    ShortVolumeIntensityFactor,
)
from tradingbot.factors.value import BookToMarketFactor, EarningsYieldFactor

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

# research.toml `[strategy.etf_momentum] momentum_weights`가 예고해 둔 조합.
# 아직 채택 게이트를 통과하지 않았으므로 [factor_weights]에는 올리지 않는다 —
# `research report --factors momentum_blend`로 먼저 재고, 통과한 뒤에 쓴다.
register_factor(
    "momentum_blend", lambda: BlendedMomentumFactor({"m3": 0.2, "m6": 0.3, "m12": 0.5})
)
# 단기 반전을 피하는 변형: 가장 최근 1개월을 뺀 12개월을 장기 축으로 쓴다.
register_factor(
    "momentum_blend_ex1m",
    lambda: BlendedMomentumFactor(
        {"m3": 0.2, "m6": 0.3, "m12_ex1": 0.5}, name="momentum_blend_ex1m"
    ),
)

# 수급·포지셔닝 팩터 (한국 전용 — KRX가 종목별 일 단위로 공시한다).
# 설계: docs/superpowers/specs/2026-08-02-flow-positioning-redesign-design.md
# 전부 "높을수록 좋음"으로 방향을 선언한 상태이며, 게이트를 재기 전에는
# [factor_weights]에 올리지 않는다.
register_factor("short_balance_ratio", lambda: ShortBalanceRatioFactor())
register_factor("short_balance_change_20d", lambda: ShortBalanceChangeFactor(20))
register_factor("short_balance_change_60d", lambda: ShortBalanceChangeFactor(60))
register_factor("short_volume_intensity_20d", lambda: ShortVolumeIntensityFactor(20))
register_factor("flow_crowding_20d", lambda: FlowCrowdingFactor(20))

register_factor("foreign_net_20d", lambda: NetBuyIntensityFactor("foreign", 20))
register_factor("foreign_net_60d", lambda: NetBuyIntensityFactor("foreign", 60))
register_factor("institution_net_20d", lambda: NetBuyIntensityFactor("institution", 20))
register_factor("earnings_yield", lambda: EarningsYieldFactor())
register_factor("book_to_market", lambda: BookToMarketFactor())
