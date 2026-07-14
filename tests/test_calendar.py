from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from tradingbot.engine.calendar import WeekdayCalendar, XCalsCalendar, get_calendar
from tradingbot.engine.clock import TradingSessionClock

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


class TestKoreaCalendar:
    def test_fixed_holiday_is_not_trading_day(self):
        cal = get_calendar("KR")
        assert not cal.is_trading_day(date(2026, 1, 1))  # 신정
        assert not cal.is_trading_day(date(2020, 1, 1))

    def test_lunar_new_year_holidays(self):
        cal = get_calendar("KR")
        assert not cal.is_trading_day(date(2026, 2, 16))
        assert not cal.is_trading_day(date(2026, 2, 17))  # 설날
        assert not cal.is_trading_day(date(2026, 2, 18))
        assert cal.is_trading_day(date(2026, 2, 13))
        assert cal.is_trading_day(date(2026, 2, 19))

    def test_year_end_closure(self):
        cal = get_calendar("KR")
        assert not cal.is_trading_day(date(2025, 12, 31))  # 연말 휴장
        assert cal.is_trading_day(date(2025, 12, 30))

    def test_weekend_is_not_trading_day(self):
        cal = get_calendar("KR")
        assert not cal.is_trading_day(date(2026, 2, 14))  # Saturday

    def test_regular_session_times(self):
        cal = get_calendar("KR")
        assert cal.open_time(date(2026, 2, 13)) == time(9, 0)
        assert cal.close_time(date(2026, 2, 13)) == time(15, 30)

    def test_navigation_skips_holidays(self):
        cal = get_calendar("KR")
        assert cal.previous_trading_day(date(2026, 1, 1)) == date(2025, 12, 30)
        assert cal.next_trading_day(date(2026, 2, 13)) == date(2026, 2, 19)

    def test_trading_days_range_excludes_holidays(self):
        cal = get_calendar("KR")
        days = cal.trading_days(date(2026, 2, 13), date(2026, 2, 20))
        assert days == [date(2026, 2, 13), date(2026, 2, 19), date(2026, 2, 20)]

    def test_new_year_first_session_opens_late(self):
        # KRX opens at 10:00 on the first trading day of the year.
        cal = get_calendar("KR")
        assert cal.open_time(date(2020, 1, 2)) == time(10, 0)
        assert cal.open_time(date(2020, 1, 3)) == time(9, 0)


class TestUsCalendar:
    def test_thanksgiving_is_holiday(self):
        cal = get_calendar("US")
        assert not cal.is_trading_day(date(2025, 11, 27))
        assert cal.is_trading_day(date(2025, 11, 26))

    def test_observed_independence_day(self):
        cal = get_calendar("US")
        # 2026-07-04 is a Saturday; observed on Friday 2026-07-03.
        assert not cal.is_trading_day(date(2026, 7, 3))

    def test_early_close_after_thanksgiving(self):
        cal = get_calendar("US")
        assert cal.close_time(date(2025, 11, 28)) == time(13, 0)
        assert cal.close_time(date(2025, 11, 26)) == time(16, 0)

    def test_early_close_christmas_eve(self):
        cal = get_calendar("US")
        assert cal.is_trading_day(date(2025, 12, 24))
        assert cal.close_time(date(2025, 12, 24)) == time(13, 0)
        assert not cal.is_trading_day(date(2025, 12, 25))

    def test_regular_session_times(self):
        cal = get_calendar("US")
        assert cal.open_time(date(2025, 11, 26)) == time(9, 30)
        assert cal.close_time(date(2025, 11, 26)) == time(16, 0)


class TestCalendarFallbacks:
    def test_weekday_calendar_ignores_holidays(self):
        cal = WeekdayCalendar("KR")
        assert cal.is_trading_day(date(2026, 1, 1))  # Thursday: weekday rule only
        assert not cal.is_trading_day(date(2026, 1, 3))  # Saturday

    def test_out_of_bounds_falls_back_to_weekday_rule(self):
        cal = XCalsCalendar("KR")
        far_future = date(2099, 6, 1)  # Monday, outside calendar bounds
        assert cal.is_trading_day(far_future)
        assert cal.open_time(far_future) == time(9, 0)
        assert cal.close_time(far_future) == time(15, 30)

    def test_unknown_market_raises(self):
        with pytest.raises(KeyError):
            get_calendar("JP")


class TestClockCalendarIntegration:
    def test_kr_holiday_blocks_session(self):
        clock = TradingSessionClock("KR")
        holiday_morning = datetime(2026, 2, 17, 10, 0, tzinfo=KST)  # 설날
        assert not clock.is_trading_day(holiday_morning)
        assert not clock.is_session_open(holiday_morning)
        assert not clock.is_before_open(holiday_morning)
        assert not clock.is_after_close(holiday_morning)

    def test_kr_year_end_closure_blocks_session(self):
        clock = TradingSessionClock("KR")
        year_end = datetime(2025, 12, 31, 10, 0, tzinfo=KST)
        assert not clock.is_session_open(year_end)

    def test_us_early_close_shifts_after_close(self):
        clock = TradingSessionClock("US")
        early_close_day_open = datetime(2025, 11, 28, 12, 59, tzinfo=NY)
        early_close_day_closed = datetime(2025, 11, 28, 13, 0, tzinfo=NY)
        assert clock.is_session_open(early_close_day_open)
        assert not clock.is_session_open(early_close_day_closed)
        assert clock.is_after_close(early_close_day_closed)

    def test_regular_day_unchanged(self):
        clock = TradingSessionClock("US")
        regular_afternoon = datetime(2025, 11, 26, 15, 59, tzinfo=NY)
        assert clock.is_session_open(regular_afternoon)
        assert clock.is_after_close(datetime(2025, 11, 26, 16, 0, tzinfo=NY))

    def test_explicit_weekday_calendar_keeps_legacy_behavior(self):
        clock = TradingSessionClock("KR", calendar=WeekdayCalendar("KR"))
        holiday_morning = datetime(2026, 1, 1, 10, 0, tzinfo=KST)
        assert clock.is_session_open(holiday_morning)
