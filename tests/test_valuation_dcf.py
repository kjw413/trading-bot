from __future__ import annotations

import pytest

from tradingbot.valuation.dcf import DcfInputs, dcf_value


def flat_inputs(**overrides) -> DcfInputs:
    """A flat, no-growth perpetuity: FCFF 100, WACC 10%, g_terminal 0.

    EV is exactly 100 / 0.10 = 1000 regardless of the explicit horizon, which
    gives us an arithmetic anchor independent of the implementation.
    """
    base = dict(
        fcff_0=100.0,
        growth=0.0,
        wacc=0.10,
        g_terminal=0.0,
        years=1,
        net_debt=200.0,
        minority_interest=0.0,
        non_operating_assets=50.0,
        diluted_shares=100.0,
        reinvestment_rate=0.0,
        roic=0.0,
    )
    base.update(overrides)
    return DcfInputs(**base)


class TestFlatPerpetuity:
    def test_enterprise_value_is_perpetuity(self):
        result = dcf_value(flat_inputs())
        assert result.enterprise_value == pytest.approx(1000.0)

    def test_enterprise_value_independent_of_horizon(self):
        one = dcf_value(flat_inputs(years=1)).enterprise_value
        ten = dcf_value(flat_inputs(years=10)).enterprise_value
        assert one == pytest.approx(ten) == pytest.approx(1000.0)

    def test_equity_bridge_and_per_share(self):
        # EquityValue = EV - net_debt - minority + non_operating = 1000-200-0+50 = 850
        result = dcf_value(flat_inputs())
        assert result.equity_value == pytest.approx(850.0)
        assert result.value_per_share == pytest.approx(8.5)

    def test_terminal_value_share_between_zero_and_one(self):
        result = dcf_value(flat_inputs(years=1))
        # Only year-1 FCFF is explicit; the rest is terminal -> PV(TV)/EV = 1/1.1.
        assert result.terminal_value_share == pytest.approx((1000.0 - 100.0 / 1.1) / 1000.0)


class TestGrowthMonotonicity:
    def test_higher_growth_raises_value(self):
        low = dcf_value(flat_inputs(growth=0.02, years=5)).value_per_share
        high = dcf_value(flat_inputs(growth=0.08, years=5)).value_per_share
        assert high > low


class TestTerminalGrowthClamp:
    def test_g_terminal_hard_clamped_to_cap(self):
        # g_terminal 0.10 with cap 0.03 must behave exactly like g_terminal 0.03.
        clamped = dcf_value(flat_inputs(g_terminal=0.10, g_terminal_cap=0.03)).enterprise_value
        explicit = dcf_value(flat_inputs(g_terminal=0.03, g_terminal_cap=0.03)).enterprise_value
        assert clamped == pytest.approx(explicit)

    def test_raising_cap_changes_value(self):
        low_cap = dcf_value(flat_inputs(g_terminal=0.10, g_terminal_cap=0.03)).enterprise_value
        high_cap = dcf_value(flat_inputs(g_terminal=0.10, g_terminal_cap=0.05)).enterprise_value
        assert high_cap > low_cap

    def test_wacc_not_above_terminal_growth_raises(self):
        with pytest.raises(ValueError):
            dcf_value(flat_inputs(wacc=0.03, g_terminal=0.03, g_terminal_cap=0.05))


class TestGuards:
    def test_non_positive_shares_raises(self):
        with pytest.raises(ValueError):
            dcf_value(flat_inputs(diluted_shares=0.0))

    def test_currency_mismatch_raises(self):
        with pytest.raises(ValueError):
            dcf_value(flat_inputs(currency="KRW"), price_currency="USD")

    def test_matching_currency_ok(self):
        result = dcf_value(flat_inputs(currency="USD"), price_currency="USD")
        assert result.value_per_share == pytest.approx(8.5)


class TestConsistencyCheck:
    def test_consistent_growth_flagged_within_tolerance(self):
        # g 0.05 ~= reinvestment 0.5 * roic 0.10 = 0.05
        result = dcf_value(flat_inputs(growth=0.05, years=5, reinvestment_rate=0.5, roic=0.10))
        assert result.consistency.within_tolerance is True
        assert result.consistency.expected_g == pytest.approx(0.05)

    def test_inconsistent_growth_flagged_out_of_tolerance(self):
        # g 0.05 but reinvestment 0.5 * roic 0.20 = 0.10 -> gap 0.05 > tol
        result = dcf_value(flat_inputs(growth=0.05, years=5, reinvestment_rate=0.5, roic=0.20))
        assert result.consistency.within_tolerance is False
