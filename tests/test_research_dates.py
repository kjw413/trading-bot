from __future__ import annotations

from datetime import date

from tradingbot.research.dates import month_end_trading_days


def test_month_end_trading_days_us_q1_2020():
    days = month_end_trading_days("US", date(2020, 1, 1), date(2020, 3, 31))
    assert days == [date(2020, 1, 31), date(2020, 2, 28), date(2020, 3, 31)]


def test_range_end_mid_month_uses_last_available_day():
    days = month_end_trading_days("US", date(2020, 1, 1), date(2020, 2, 14))
    assert days == [date(2020, 1, 31), date(2020, 2, 14)]


def test_empty_range():
    assert month_end_trading_days("US", date(2020, 3, 31), date(2020, 1, 1)) == []
