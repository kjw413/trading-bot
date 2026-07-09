from __future__ import annotations

from tradingbot.strategies.base import Strategy
from tradingbot.strategies.ma_cross import MovingAverageCrossStrategy
from tradingbot.strategies.rsi_reversion import RsiReversionStrategy
from tradingbot.strategies.vol_breakout import VolatilityBreakoutStrategy


_STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageCrossStrategy.name: MovingAverageCrossStrategy,
    RsiReversionStrategy.name: RsiReversionStrategy,
    VolatilityBreakoutStrategy.name: VolatilityBreakoutStrategy,
}


def get_strategy(name: str) -> type[Strategy]:
    try:
        return _STRATEGIES[name]
    except KeyError as exc:
        available = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy: {name}. Available: {available}") from exc


def list_strategies() -> list[str]:
    return sorted(_STRATEGIES)
