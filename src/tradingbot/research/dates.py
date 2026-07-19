from __future__ import annotations

from datetime import date

from tradingbot.engine.calendar import get_calendar


def month_end_trading_days(market: str, start: date, end: date) -> list[date]:
    """Last trading day of each month between start and end (inclusive)."""
    last_by_month: dict[tuple[int, int], date] = {}
    for day in get_calendar(market).trading_days(start, end):
        last_by_month[(day.year, day.month)] = day
    return sorted(last_by_month.values())
