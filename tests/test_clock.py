from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradingbot.engine.calendar import WeekdayCalendar
from tradingbot.engine.clock import TradingSessionClock


def test_kr_session_clock_boundaries():
    tz = ZoneInfo("Asia/Seoul")
    # WeekdayCalendar keeps this test about clock boundary logic only;
    # real-calendar behavior (holidays, late opens) is covered in test_calendar.py.
    clock = TradingSessionClock("KR", calendar=WeekdayCalendar("KR"))

    assert clock.is_before_open(datetime(2020, 1, 2, 8, 59, tzinfo=tz))
    assert clock.is_session_open(datetime(2020, 1, 2, 9, 0, tzinfo=tz))
    assert clock.is_session_open(datetime(2020, 1, 2, 15, 29, tzinfo=tz))
    assert clock.is_after_close(datetime(2020, 1, 2, 15, 30, tzinfo=tz))
    assert not clock.is_session_open(datetime(2020, 1, 4, 10, 0, tzinfo=tz))


def test_us_session_clock_uses_new_york_dst():
    clock = TradingSessionClock("US")
    utc = ZoneInfo("UTC")

    summer_open = clock.session_open_at(datetime(2026, 7, 9, 12, 0, tzinfo=utc))
    winter_open = clock.session_open_at(datetime(2026, 1, 9, 12, 0, tzinfo=utc))

    assert summer_open.hour == 9
    assert summer_open.minute == 30
    assert summer_open.utcoffset() == timedelta(hours=-4)
    assert winter_open.hour == 9
    assert winter_open.minute == 30
    assert winter_open.utcoffset() == timedelta(hours=-5)


def test_clock_now_provider_is_localized():
    clock = TradingSessionClock(
        "KR",
        now_provider=lambda: datetime(2020, 1, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert clock.now().tzinfo == ZoneInfo("Asia/Seoul")
    assert clock.now().hour == 9
