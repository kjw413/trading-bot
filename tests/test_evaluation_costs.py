from __future__ import annotations

import pytest

from tradingbot.research.evaluation import scale_costs

CONFIG = {
    "fees": {
        "KR": {"commission_rate": 0.00015, "sell_tax_rate": 0.0015},
        "US": {
            "commission_rate": 0.0,
            "sec_fee_rate": 0.0000278,
            "finra_taf_per_share": 0.000166,
            "finra_taf_cap": 8.30,
        },
    },
    "execution": {"slippage_bps": 5},
    "backtest": {"initial_cash_kr": 10000000},
}


class TestScaleCosts:
    def test_doubles_the_markets_fee_rates(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["fees"]["KR"]["commission_rate"] == pytest.approx(0.0003)
        assert scaled["fees"]["KR"]["sell_tax_rate"] == pytest.approx(0.003)

    def test_doubles_slippage(self):
        assert scale_costs(CONFIG, "KR", 2.0)["execution"]["slippage_bps"] == pytest.approx(10)

    def test_doubles_the_taf_cap_too(self):
        # Scaling the per-share rate but not its cap would let the cap absorb
        # the increase and neuter the whole sensitivity test.
        scaled = scale_costs(CONFIG, "US", 2.0)
        assert scaled["fees"]["US"]["finra_taf_per_share"] == pytest.approx(0.000332)
        assert scaled["fees"]["US"]["finra_taf_cap"] == pytest.approx(16.60)

    def test_leaves_other_markets_alone(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["fees"]["US"]["sec_fee_rate"] == pytest.approx(0.0000278)

    def test_leaves_non_cost_settings_alone(self):
        scaled = scale_costs(CONFIG, "KR", 2.0)
        assert scaled["backtest"]["initial_cash_kr"] == 10000000

    def test_does_not_mutate_the_original(self):
        scale_costs(CONFIG, "KR", 2.0)
        assert CONFIG["fees"]["KR"]["commission_rate"] == pytest.approx(0.00015)
        assert CONFIG["execution"]["slippage_bps"] == 5

    def test_multiplier_of_one_is_a_faithful_copy(self):
        scaled = scale_costs(CONFIG, "KR", 1.0)
        assert scaled["fees"]["KR"] == CONFIG["fees"]["KR"]
        assert scaled is not CONFIG

    def test_market_is_case_insensitive(self):
        assert scale_costs(CONFIG, "kr", 2.0)["fees"]["KR"]["commission_rate"] == pytest.approx(0.0003)

    def test_missing_market_section_is_not_an_error(self):
        # A config without fees for this market still gets slippage scaled.
        bare = {"execution": {"slippage_bps": 5}}
        assert scale_costs(bare, "KR", 2.0)["execution"]["slippage_bps"] == pytest.approx(10)

    def test_negative_multiplier_rejected(self):
        with pytest.raises(ValueError):
            scale_costs(CONFIG, "KR", -1.0)
