from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


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


class TradingSessionClock:
    def __init__(
        self,
        market: str,
        poll_interval: timedelta = timedelta(minutes=5),
        now_provider=None,
    ) -> None:
        self.session = SESSIONS[market.upper()]
        self.tz = ZoneInfo(self.session.timezone)
        self.poll_interval = poll_interval
        self._now_provider = now_provider

    def now(self) -> datetime:
        current = self._now_provider() if self._now_provider is not None else datetime.now(self.tz)
        return self.localize(current)

    def localize(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.tz)
        return value.astimezone(self.tz)

    def is_trading_day(self, value: datetime | None = None) -> bool:
        current = self.localize(value) if value is not None else self.now()
        return current.weekday() < 5

    def session_open_at(self, value: datetime | None = None) -> datetime:
        current = self.localize(value) if value is not None else self.now()
        return datetime.combine(current.date(), self.session.open_time, self.tz)

    def session_close_at(self, value: datetime | None = None) -> datetime:
        current = self.localize(value) if value is not None else self.now()
        return datetime.combine(current.date(), self.session.close_time, self.tz)

    def is_before_open(self, value: datetime | None = None) -> bool:
        current = self.localize(value) if value is not None else self.now()
        return self.is_trading_day(current) and current < self.session_open_at(current)

    def is_session_open(self, value: datetime | None = None) -> bool:
        current = self.localize(value) if value is not None else self.now()
        return self.is_trading_day(current) and self.session_open_at(current) <= current < self.session_close_at(current)

    def is_after_close(self, value: datetime | None = None) -> bool:
        current = self.localize(value) if value is not None else self.now()
        return self.is_trading_day(current) and current >= self.session_close_at(current)

    def should_poll(self, last_poll_at: datetime | None, value: datetime | None = None) -> bool:
        current = self.localize(value) if value is not None else self.now()
        if not self.is_session_open(current):
            return False
        if last_poll_at is None:
            return True
        return current - self.localize(last_poll_at) >= self.poll_interval

    def next_poll_after(self, last_poll_at: datetime | None, value: datetime | None = None) -> datetime:
        current = self.localize(value) if value is not None else self.now()
        if last_poll_at is None:
            return max(current, self.session_open_at(current))
        return self.localize(last_poll_at) + self.poll_interval
