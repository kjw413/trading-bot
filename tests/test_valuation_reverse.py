from __future__ import annotations

import pytest

from tradingbot.valuation.dcf import DcfInputs, dcf_value
from tradingbot.valuation.reverse import implied_growth


def flat_inputs(**overrides) -> DcfInputs:
    base = dict(
        fcff_0=100.0,
        growth=0.0,
        wacc=0.10,
        g_terminal=0.0,
        years=5,
        net_debt=200.0,
        minority_interest=0.0,
        non_operating_assets=50.0,
        diluted_shares=100.0,
        reinvestment_rate=0.0,
        roic=0.0,
    )
    base.update(overrides)
    return DcfInputs(**base)


class TestImpliedGrowth:
    def test_recovers_known_growth(self):
        true_g = 0.05
        price = dcf_value(flat_inputs(growth=true_g)).value_per_share
        # Pass inputs with a different growth; the search must override it.
        recovered = implied_growth(price, flat_inputs(growth=0.0))
        assert recovered == pytest.approx(true_g, abs=1e-4)

    def test_recovers_negative_growth(self):
        true_g = -0.03
        price = dcf_value(flat_inputs(growth=true_g)).value_per_share
        recovered = implied_growth(price, flat_inputs(growth=0.10))
        assert recovered == pytest.approx(true_g, abs=1e-4)

    def test_higher_price_implies_higher_growth(self):
        low_price = dcf_value(flat_inputs(growth=0.02)).value_per_share
        high_price = dcf_value(flat_inputs(growth=0.08)).value_per_share
        assert implied_growth(high_price, flat_inputs()) > implied_growth(low_price, flat_inputs())

    def test_price_outside_bracket_raises(self):
        # An absurdly high price cannot be reached within the default bracket.
        with pytest.raises(ValueError):
            implied_growth(1e9, flat_inputs())
