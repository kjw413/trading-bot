from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from functools import lru_cache
from typing import Protocol
from zoneinfo import ZoneInfo

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class MarketSession:
    market: str
    timezone: str
    open_time: time
    close_time: time


SESSIONS = {
    "KR": MarketSession("KR", "Asia/Seoul", time(9, 0), time(15, 30)),
    "US": MarketSession("US", "America/New_York", time(9, 30), time(16, 0)),
}


class ExchangeCalendar(Protocol):
    market: str

    def is_trading_day(self, day: date) -> bool:
        ...

    def open_time(self, day: date) -> time:
        ...

    def close_time(self, day: date) -> time:
        ...

    def next_trading_day(self, day: date) -> date:
        ...

    def previous_trading_day(self, day: date) -> date:
        ...

    def trading_days(self, start: date, end: date) -> list[date]:
        ...


class WeekdayCalendar:
    """Weekday-only calendar with fixed session times.

    Legacy behavior: no holiday awareness. Used as a fallback when the
    exchange_calendars package is unavailable or a date is outside its bounds.
    """

    def __init__(self, market: str) -> None:
        self.market = market.upper()
        self.session = SESSIONS[self.market]

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def open_time(self, day: date) -> time:
        return self.session.open_time

    def close_time(self, day: date) -> time:
        return self.session.close_time

    def next_trading_day(self, day: date) -> date:
        current = day + timedelta(days=1)
        while not self.is_trading_day(current):
            current += timedelta(days=1)
        return current

    def previous_trading_day(self, day: date) -> date:
        current = day - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current

    def trading_days(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days


class XCalsCalendar:
    """Exchange calendar backed by the exchange_calendars package.

    Handles official holidays and early closes (e.g. NYSE 13:00 closes,
    KRX year-end closure and lunar holidays). Dates outside the underlying
    calendar's bounds fall back to the weekday rule with a one-time warning.
    """

    CODES = {"KR": "XKRX", "US": "XNYS"}

    def __init__(self, market: str) -> None:
        import exchange_calendars as xcals

        self.market = market.upper()
        self.session = SESSIONS[self.market]
        self._cal = xcals.get_calendar(self.CODES[self.market])
        self._tz = ZoneInfo(self.session.timezone)
        self._fallback = WeekdayCalendar(self.market)
        self._warned_out_of_bounds = False

    def _in_bounds(self, day: date) -> bool:
        return self._cal.first_session.date() <= day <= self._cal.last_session.date()

    def _fallback_for(self, day: date) -> WeekdayCalendar:
        if not self._warned_out_of_bounds:
            LOGGER.warning(
                "%s exchange calendar bounds are %s..%s; %s is outside, using weekday rule",
                self.market,
                self._cal.first_session.date(),
                self._cal.last_session.date(),
                day,
            )
            self._warned_out_of_bounds = True
        return self._fallback

    def is_trading_day(self, day: date) -> bool:
        if not self._in_bounds(day):
            return self._fallback_for(day).is_trading_day(day)
        return bool(self._cal.is_session(day))

    def open_time(self, day: date) -> time:
        if not self._in_bounds(day) or not self.is_trading_day(day):
            return self.session.open_time
        return self._cal.session_open(day).tz_convert(self._tz).time()

    def close_time(self, day: date) -> time:
        if not self._in_bounds(day) or not self.is_trading_day(day):
            return self.session.close_time
        return self._cal.session_close(day).tz_convert(self._tz).time()

    def next_trading_day(self, day: date) -> date:
        candidate = day + timedelta(days=1)
        if not self._in_bounds(candidate):
            return self._fallback_for(candidate).next_trading_day(day)
        return self._cal.date_to_session(candidate, direction="next").date()

    def previous_trading_day(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        if not self._in_bounds(candidate):
            return self._fallback_for(candidate).previous_trading_day(day)
        return self._cal.date_to_session(candidate, direction="previous").date()

    def trading_days(self, start: date, end: date) -> list[date]:
        if start > end:
            return []
        if not self._in_bounds(start) or not self._in_bounds(end):
            return self._fallback_for(start if not self._in_bounds(start) else end).trading_days(start, end)
        return [session.date() for session in self._cal.sessions_in_range(start, end)]


@lru_cache(maxsize=None)
def get_calendar(market: str) -> ExchangeCalendar:
    """Return the exchange calendar for a market, preferring real holiday data."""
    market = market.upper()
    if market not in SESSIONS:
        raise KeyError(f"Unknown market: {market}")
    try:
        return XCalsCalendar(market)
    except ImportError:
        LOGGER.warning(
            "exchange_calendars package unavailable; falling back to weekday-only calendar for %s",
            market,
        )
        return WeekdayCalendar(market)
