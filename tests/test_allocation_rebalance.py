from __future__ import annotations

from datetime import date

import pytest

from tradingbot.allocation.rebalance import TradeIntent, is_rebalance_date, plan_rebalance
from tradingbot.engine.calendar import get_calendar


class TestPlanRebalance:
    def test_new_position_is_a_buy_by_weight(self):
        plan = plan_rebalance(targets={"AAA": 0.3}, current_weights={}, positions={})
        assert plan == [TradeIntent(symbol="AAA", side="BUY", qty=None, weight=pytest.approx(0.3))]

    def test_dropped_position_is_a_full_sell(self):
        plan = plan_rebalance(
            targets={}, current_weights={"AAA": 0.3}, positions={"AAA": 10}
        )
        assert plan == [TradeIntent(symbol="AAA", side="SELL", qty=10, weight=None)]

    def test_trimming_sells_a_proportional_quantity(self):
        plan = plan_rebalance(
            targets={"AAA": 0.1}, current_weights={"AAA": 0.3}, positions={"AAA": 30}
        )
        # Shed 2/3 of a 30-share position.
        assert plan == [TradeIntent(symbol="AAA", side="SELL", qty=20, weight=None)]

    def test_topping_up_buys_the_weight_difference(self):
        plan = plan_rebalance(
            targets={"AAA": 0.3}, current_weights={"AAA": 0.1}, positions={"AAA": 10}
        )
        assert plan == [TradeIntent(symbol="AAA", side="BUY", qty=None, weight=pytest.approx(0.2))]

    def test_sells_come_before_buys(self):
        plan = plan_rebalance(
            targets={"BBB": 0.3},
            current_weights={"AAA": 0.3},
            positions={"AAA": 10},
        )
        # Sells free the cash the buys need at the same next-open fill.
        assert [intent.side for intent in plan] == ["SELL", "BUY"]

    def test_within_band_changes_are_ignored(self):
        plan = plan_rebalance(
            targets={"AAA": 0.301}, current_weights={"AAA": 0.300}, positions={"AAA": 10},
            band=0.005,
        )
        assert plan == []

    def test_deterministic_symbol_order(self):
        plan = plan_rebalance(
            targets={"CCC": 0.2, "AAA": 0.2},
            current_weights={},
            positions={},
        )
        assert [intent.symbol for intent in plan] == ["AAA", "CCC"]

    def test_zero_quantity_sell_is_dropped(self):
        # A tiny trim of a tiny position can round to zero shares — emitting
        # a zero-share order would just be rejected downstream.
        plan = plan_rebalance(
            targets={"AAA": 0.29}, current_weights={"AAA": 0.30}, positions={"AAA": 1},
            band=0.001,
        )
        assert plan == []


class TestIsRebalanceDate:
    def test_monthly_true_on_last_trading_day_of_month(self):
        calendar = get_calendar("KR")
        # 2024-01-31 is a Wednesday and the last KR trading day of January.
        assert is_rebalance_date(date(2024, 1, 31), "monthly", calendar) is True

    def test_monthly_false_mid_month(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 15), "monthly", calendar) is False

    def test_monthly_true_at_year_end_boundary(self):
        calendar = get_calendar("KR")
        # KRX closes Dec 31; the last 2024 trading day is Dec 30 (Mon).
        assert is_rebalance_date(date(2024, 12, 30), "monthly", calendar) is True

    def test_weekly_true_on_friday(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 19), "weekly", calendar) is True

    def test_weekly_false_on_tuesday(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 16), "weekly", calendar) is False

    def test_daily_always_true(self):
        calendar = get_calendar("KR")
        assert is_rebalance_date(date(2024, 1, 16), "daily", calendar) is True

    def test_unknown_frequency_rejected(self):
        calendar = get_calendar("KR")
        with pytest.raises(ValueError, match="frequency"):
            is_rebalance_date(date(2024, 1, 16), "hourly", calendar)
