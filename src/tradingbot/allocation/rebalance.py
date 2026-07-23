"""Turn target weights into a concrete, ordered list of trades.

Sells are planned before buys because both fill at the same next session
open — the sells free the cash the buys spend. Differences inside `band`
are ignored: rebalancing a 0.1%p drift buys nothing but transaction costs.

Rebalance timing is judged from the exchange calendar alone (is this the
period's last trading day?). The calendar is static data, so this cannot
leak price information from the future.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tradingbot.engine.calendar import ExchangeCalendar

FREQUENCIES = ("daily", "weekly", "monthly")


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    side: str  # "SELL" | "BUY"
    qty: int | None  # sells: concrete share count
    weight: float | None  # buys: equity fraction to add


def plan_rebalance(
    *,
    targets: dict[str, float],
    current_weights: dict[str, float],
    positions: dict[str, int],
    band: float = 0.005,
) -> list[TradeIntent]:
    """Sells first (freeing cash), then buys, both in symbol order."""
    sells: list[TradeIntent] = []
    buys: list[TradeIntent] = []
    symbols = sorted(set(targets) | set(current_weights))

    for symbol in symbols:
        target = float(targets.get(symbol, 0.0))
        current = float(current_weights.get(symbol, 0.0))
        delta = target - current
        if abs(delta) <= band:
            continue
        if delta < 0:
            held = int(positions.get(symbol, 0))
            if held <= 0:
                continue
            if target <= 0:
                qty = held
            else:
                qty = round(held * (-delta) / current) if current > 0 else held
            if qty >= 1:
                sells.append(TradeIntent(symbol=symbol, side="SELL", qty=qty, weight=None))
        else:
            buys.append(TradeIntent(symbol=symbol, side="BUY", qty=None, weight=delta))
    return sells + buys


def is_rebalance_date(dt: date, frequency: str, calendar: ExchangeCalendar) -> bool:
    """True on the last trading day of the period (signal at that close)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency}. Available: {', '.join(FREQUENCIES)}")
    if frequency == "daily":
        return True
    next_day = calendar.next_trading_day(dt)
    if frequency == "monthly":
        return (next_day.year, next_day.month) != (dt.year, dt.month)
    this_week = dt.isocalendar()[:2]
    next_week = next_day.isocalendar()[:2]
    return this_week != next_week
