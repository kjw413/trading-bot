from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide
from tradingbot.report.metrics import annual_turnover


def make_result(fills: list[Fill], equities: list[tuple[str, float]]) -> BacktestResult:
    curve = pd.DataFrame(
        {
            "date": [pd.Timestamp(day) for day, _ in equities],
            "equity": [value for _, value in equities],
        }
    )
    return BacktestResult(
        initial_cash=equities[0][1] if equities else 0.0,
        final_equity=equities[-1][1] if equities else 0.0,
        equity_curve=curve,
        fills=fills,
        rejected_orders=[],
        expired_orders=[],
    )


def buy(qty: int, price: float, day: str = "2024-01-02") -> Fill:
    return Fill(
        order_id="x",
        symbol="AAA",
        side=OrderSide.BUY,
        qty=qty,
        price=price,
        fee=0.0,
        dt=date.fromisoformat(day),
    )


def sell(qty: int, price: float, day: str = "2024-01-02") -> Fill:
    return Fill(
        order_id="x",
        symbol="AAA",
        side=OrderSide.SELL,
        qty=qty,
        price=price,
        fee=0.0,
        dt=date.fromisoformat(day),
    )


# One full year of flat equity at 1000.
YEAR = [("2024-01-01", 1000.0), ("2024-12-31", 1000.0)]


class TestAnnualTurnover:
    def test_buying_the_whole_book_once_a_year_is_one(self):
        result = make_result([buy(10, 100.0)], YEAR)
        assert annual_turnover(result) == pytest.approx(1.0, rel=0.01)

    def test_buying_twice_the_book_is_two(self):
        result = make_result([buy(10, 100.0), buy(10, 100.0)], YEAR)
        assert annual_turnover(result) == pytest.approx(2.0, rel=0.01)

    def test_sells_do_not_count_one_way_turnover(self):
        # One-way turnover counts purchases only; counting both sides would
        # double every round trip.
        with_sells = make_result([buy(10, 100.0), sell(10, 100.0)], YEAR)
        assert annual_turnover(with_sells) == pytest.approx(1.0, rel=0.01)

    def test_annualized_over_a_half_year(self):
        half = [("2024-01-01", 1000.0), ("2024-07-01", 1000.0)]
        result = make_result([buy(10, 100.0)], half)
        # Same trading in half the time is twice the annual rate.
        assert annual_turnover(result) == pytest.approx(2.0, rel=0.02)

    def test_no_trades_is_zero(self):
        assert annual_turnover(make_result([], YEAR)) == pytest.approx(0.0)

    def test_uses_average_equity_not_final(self):
        # Equity doubles mid-run; the denominator must be the average (1500),
        # not the final value (2000), or turnover is understated.
        curve = [("2024-01-01", 1000.0), ("2024-12-31", 2000.0)]
        result = make_result([buy(15, 100.0)], curve)
        assert annual_turnover(result) == pytest.approx(1.0, rel=0.01)

    def test_empty_curve_is_nan_not_zero(self):
        # No equity curve means unmeasurable, not "no trading".
        result = make_result([buy(10, 100.0)], [])
        assert math.isnan(annual_turnover(result))

    def test_zero_average_equity_is_nan_not_infinite(self):
        result = make_result([buy(10, 100.0)], [("2024-01-01", 0.0), ("2024-12-31", 0.0)])
        assert math.isnan(annual_turnover(result))

    def test_single_point_curve_is_nan(self):
        # A zero-length period cannot be annualized.
        result = make_result([buy(10, 100.0)], [("2024-01-01", 1000.0)])
        assert math.isnan(annual_turnover(result))
