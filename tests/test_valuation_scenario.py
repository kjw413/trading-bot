from __future__ import annotations

import pytest

from tradingbot.valuation.dcf import DcfInputs, dcf_value
from tradingbot.valuation.scenario import ScenarioValues, scenario_values


def flat_inputs(**overrides) -> DcfInputs:
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


class TestScenarioValues:
    def test_monotonic_ok(self):
        sv = ScenarioValues(conservative=8.0, base=10.0, optimistic=13.0)
        assert (sv.conservative, sv.base, sv.optimistic) == (8.0, 10.0, 13.0)

    def test_equal_bounds_allowed(self):
        ScenarioValues(conservative=10.0, base=10.0, optimistic=10.0)

    def test_inverted_raises(self):
        with pytest.raises(ValueError):
            ScenarioValues(conservative=12.0, base=10.0, optimistic=13.0)


class TestScenarioValuesBuilder:
    def test_builds_three_tuple_from_dcf(self):
        conservative = flat_inputs(growth=0.02, years=5)
        base = flat_inputs(growth=0.05, years=5)
        optimistic = flat_inputs(growth=0.08, years=5)

        sv = scenario_values(conservative, base, optimistic)

        assert sv.conservative == pytest.approx(dcf_value(conservative).value_per_share)
        assert sv.base == pytest.approx(dcf_value(base).value_per_share)
        assert sv.optimistic == pytest.approx(dcf_value(optimistic).value_per_share)
        assert sv.conservative < sv.base < sv.optimistic

    def test_inconsistent_scenario_ordering_raises(self):
        # Optimistic inputs that produce a *lower* value than base must be rejected.
        conservative = flat_inputs(growth=0.02, years=5)
        base = flat_inputs(growth=0.05, years=5)
        bad_optimistic = flat_inputs(growth=0.01, years=5)
        with pytest.raises(ValueError):
            scenario_values(conservative, base, bad_optimistic)
