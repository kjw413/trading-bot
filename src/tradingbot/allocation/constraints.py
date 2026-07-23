"""Hard limits between a computed portfolio and the orders it becomes.

Two rules, applied in order:
1. No single name above `max_weight`. Excess goes to cash, never to the
   other names — concentration caps exist to limit risk, and redistributing
   the excess would just relocate the concentration.
2. Total equity exposure stays at or below `1 - cash_buffer`, scaling every
   weight down proportionally when it doesn't.
"""

from __future__ import annotations


def apply_constraints(
    weights: dict[str, float], *, max_weight: float, cash_buffer: float
) -> dict[str, float]:
    if max_weight <= 0:
        raise ValueError("max_weight must be positive")
    if not 0.0 <= cash_buffer < 1.0:
        raise ValueError("cash_buffer must be in [0, 1)")
    if not weights:
        return {}

    capped = {symbol: min(weight, max_weight) for symbol, weight in weights.items()}
    budget = 1.0 - cash_buffer
    total = sum(capped.values())
    if total <= budget:
        return capped
    scale = budget / total
    return {symbol: weight * scale for symbol, weight in capped.items()}
