from __future__ import annotations

import inspect

import pytest

from tradingbot.valuation.decision import (
    CompanyType,
    Signal,
    accumulate_gate,
    decide,
    ensure_supported,
    primary_model,
)
from tradingbot.valuation.scenario import ScenarioValues

# MaxBuy prices per scenario: conservative < base < optimistic.
MAX_BUY = ScenarioValues(conservative=8.0, base=10.0, optimistic=13.0)


class TestDecideZones:
    def test_below_conservative_accumulates(self):
        assert decide(7.0, MAX_BUY).signal is Signal.ACCUMULATE

    def test_at_conservative_boundary_accumulates(self):
        assert decide(8.0, MAX_BUY).signal is Signal.ACCUMULATE

    def test_between_conservative_and_base_is_partial(self):
        assert decide(9.0, MAX_BUY).signal is Signal.PARTIAL

    def test_at_base_boundary_is_partial(self):
        assert decide(10.0, MAX_BUY).signal is Signal.PARTIAL

    def test_between_base_and_optimistic_holds_or_trims(self):
        assert decide(11.0, MAX_BUY).signal is Signal.HOLD_OR_TRIM

    def test_at_optimistic_boundary_holds_or_trims(self):
        assert decide(13.0, MAX_BUY).signal is Signal.HOLD_OR_TRIM

    def test_above_optimistic_exits(self):
        assert decide(14.0, MAX_BUY).signal is Signal.EXIT

    def test_decision_carries_price_and_bounds(self):
        decision = decide(9.0, MAX_BUY)
        assert decision.current_price == 9.0
        assert decision.max_buy is MAX_BUY
        assert decision.reason


class TestNoCostBasisInput:
    def test_decide_takes_only_current_price_and_bounds(self):
        # Framework rule 2: cost basis / average price must never be an input.
        params = list(inspect.signature(decide).parameters)
        assert params == ["current_price", "max_buy"]
        for banned in ("cost", "avg", "average", "basis", "entry", "pnl", "return"):
            assert not any(banned in p.lower() for p in params)


class TestAccumulateGate:
    def test_all_three_true_passes(self):
        result = accumulate_gate(price_ok=True, thesis_intact=True, survival_ok=True)
        assert result.passed is True
        assert result.reasons == []

    def test_any_false_fails_with_reason(self):
        result = accumulate_gate(price_ok=True, thesis_intact=False, survival_ok=True)
        assert result.passed is False
        assert any("thesis" in reason for reason in result.reasons)

    def test_multiple_false_lists_all(self):
        result = accumulate_gate(price_ok=False, thesis_intact=False, survival_ok=False)
        assert result.passed is False
        assert len(result.reasons) == 3


class TestCompanyTypeRouting:
    def test_stable_routes_to_fcff_dcf(self):
        assert primary_model(CompanyType.STABLE) == "FCFF_DCF"

    def test_bank_routes_to_residual_income(self):
        assert primary_model(CompanyType.BANK) == "RIM"

    def test_ensure_supported_allows_dcf(self):
        ensure_supported(CompanyType.STABLE)  # no raise

    def test_ensure_supported_rejects_unimplemented_model(self):
        with pytest.raises(NotImplementedError):
            ensure_supported(CompanyType.BANK)
