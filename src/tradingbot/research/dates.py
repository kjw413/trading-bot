from __future__ import annotations

from datetime import date

from tradingbot.engine.calendar import get_calendar


PERIOD_KEYS = {
    "in_sample": ("in_sample_start", "in_sample_end"),
    "validation": ("validation_start", "validation_end"),
    "out_of_sample": ("out_of_sample_start", None),
}


def research_period(config: dict, name: str) -> tuple[date, date | None]:
    """Resolve a named protocol window from config/research.toml."""
    try:
        start_key, end_key = PERIOD_KEYS[name]
        periods = config["periods"]
        start = date.fromisoformat(periods[start_key])
        end = date.fromisoformat(periods[end_key]) if end_key else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid research period {name!r}") from exc
    return start, end


def month_end_trading_days(market: str, start: date, end: date) -> list[date]:
    """Last trading day of each month between start and end (inclusive)."""
    last_by_month: dict[tuple[int, int], date] = {}
    for day in get_calendar(market).trading_days(start, end):
        last_by_month[(day.year, day.month)] = day
    return sorted(last_by_month.values())
