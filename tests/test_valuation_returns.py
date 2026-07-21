from __future__ import annotations

import math

import pytest

from tradingbot.valuation.returns import RequiredReturn, irr, max_buy_price


class TestRequiredReturn:
    def test_rate_is_sum_of_components(self):
        r = RequiredReturn(risk_free=0.03, equity_risk_premium=0.05, firm_specific_premium=0.02)
        assert r.rate() == pytest.approx(0.10)

    def test_zero_premiums_allowed(self):
        assert RequiredReturn(0.03, 0.0, 0.0).rate() == pytest.approx(0.03)

    def test_negative_component_raises(self):
        with pytest.raises(ValueError):
            RequiredReturn(risk_free=-0.01, equity_risk_premium=0.05, firm_specific_premium=0.0)


class TestIrr:
    def test_no_dividends(self):
        # 100 -> 133.1 over 3 years is exactly 10% annualized.
        assert irr(100.0, 133.1, dividends=0.0, years=3) == pytest.approx(0.10, abs=1e-6)

    def test_with_dividends(self):
        # (110 + 11) / 100 over 1 year = 21%.
        assert irr(100.0, 110.0, dividends=11.0, years=1) == pytest.approx(0.21)

    def test_loss(self):
        assert irr(100.0, 90.0, dividends=0.0, years=1) == pytest.approx(-0.10)

    def test_non_positive_p0_raises(self):
        with pytest.raises(ValueError):
            irr(0.0, 100.0, dividends=0.0, years=1)

    def test_non_positive_years_raises(self):
        with pytest.raises(ValueError):
            irr(100.0, 110.0, dividends=0.0, years=0)


class TestMaxBuyPrice:
    def test_discounts_terminal_plus_dividends(self):
        # (133.1 + 0) / (1.10)^3 = 100.
        assert max_buy_price(133.1, dividends=0.0, r_required=0.10, years=3) == pytest.approx(100.0)

    def test_round_trip_with_irr(self):
        # Buying exactly at max_buy_price yields an IRR equal to r_required.
        p_t, dividends, r, years = 200.0, 15.0, 0.12, 4
        price = max_buy_price(p_t, dividends, r, years)
        assert irr(price, p_t, dividends, years) == pytest.approx(r, abs=1e-9)

    def test_higher_required_return_lowers_max_buy(self):
        low = max_buy_price(150.0, 0.0, 0.08, 3)
        high = max_buy_price(150.0, 0.0, 0.15, 3)
        assert high < low

    def test_non_positive_years_raises(self):
        with pytest.raises(ValueError):
            max_buy_price(150.0, 0.0, 0.10, 0)
