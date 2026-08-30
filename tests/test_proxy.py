from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.proxy import (
    CostState,
    ProxyStatus,
    leverage_disclosure,
    measure_proxy_pair,
    overlap_fraction,
    variance_drag,
)


def prices_from_returns(returns: np.ndarray, *, start: str = "2025-01-01") -> pd.Series:
    values = np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + returns)))
    index = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=index)


def proxy_returns() -> np.ndarray:
    observations = np.arange(252, dtype=float)
    return 0.01 * np.sin(2.0 * np.pi * observations / 21.0)


def test_a_synthetic_proxy_pair_qualifies():
    proxy = proxy_returns()
    measurement = measure_proxy_pair(
        traded_symbol="SOXL",
        proxy_symbol="SOXX",
        traded_prices=prices_from_returns(3.0 * proxy),
        proxy_prices=prices_from_returns(proxy),
        leverage=3.0,
    )

    assert measurement.beta == pytest.approx(3.0)
    assert measurement.r_squared == pytest.approx(1.0)
    assert measurement.status is ProxyStatus.QUALIFIED
    assert measurement.qualifies


def test_a_pair_failing_beta_alone_does_not_qualify():
    proxy = proxy_returns()
    measurement = measure_proxy_pair(
        traded_symbol="SOXL",
        proxy_symbol="SOXX",
        traded_prices=prices_from_returns(2.6 * proxy),
        proxy_prices=prices_from_returns(proxy),
        leverage=3.0,
    )

    assert measurement.beta == pytest.approx(2.6)
    assert measurement.r_squared == pytest.approx(1.0)
    assert measurement.status is ProxyStatus.DISCRETIONARY_HOLDING


def test_a_pair_with_the_right_beta_but_low_r_squared_does_not_qualify():
    proxy = proxy_returns()
    observations = np.arange(252, dtype=float)
    unrelated = 0.08 * np.cos(2.0 * np.pi * observations / 21.0)
    measurement = measure_proxy_pair(
        traded_symbol="SPCX",
        proxy_symbol="SPY",
        traded_prices=prices_from_returns(3.0 * proxy + unrelated),
        proxy_prices=prices_from_returns(proxy),
        leverage=3.0,
    )

    assert measurement.beta == pytest.approx(3.0)
    assert measurement.r_squared < 0.95
    assert measurement.status is ProxyStatus.DISCRETIONARY_HOLDING


def test_a_failed_pair_reports_both_beta_and_r_squared():
    proxy = proxy_returns()
    observations = np.arange(252, dtype=float)
    unrelated = 0.08 * np.cos(2.0 * np.pi * observations / 21.0)
    measurement = measure_proxy_pair(
        traded_symbol="SPCX",
        proxy_symbol="SPY",
        traded_prices=prices_from_returns(3.0 * proxy + unrelated),
        proxy_prices=prices_from_returns(proxy),
        leverage=3.0,
    )

    assert f"beta={measurement.beta:.4f}" in measurement.report
    assert f"R²={measurement.r_squared:.4f}" in measurement.report


def test_no_qualifying_proxy_reads_as_a_discretionary_holding():
    proxy = proxy_returns()
    measurement = measure_proxy_pair(
        traded_symbol="SOXL",
        proxy_symbol="SOXX",
        traded_prices=prices_from_returns(2.6 * proxy),
        proxy_prices=prices_from_returns(proxy),
        leverage=3.0,
    )

    assert not measurement.qualifies
    assert "no validated signal for this holding" in measurement.report
    assert "discretionary holding" in measurement.report


def test_unknown_carry_never_becomes_zero_or_a_partial_total():
    prices = prices_from_returns(np.tile(np.array([0.01, -0.01]), 30))
    disclosure = leverage_disclosure(
        traded_prices=prices,
        leverage=3.0,
        expense_ratio=None,
        financing_rate=None,
        validation_start=prices.index[0],
        validation_end=prices.index[-1],
    )

    assert disclosure.annual_carry is CostState.UNKNOWN
    assert disclosure.expected_drag is CostState.UNKNOWN
    with pytest.raises(TypeError, match="Unknown cost has no truth value"):
        _ = disclosure.annual_carry or 0.0
    with pytest.raises(TypeError):
        _ = disclosure.variance_drag + disclosure.annual_carry


def test_variance_drag_uses_the_stated_formula_for_a_known_sigma():
    assert variance_drag(leverage=3.0, annualized_sigma=0.20) == pytest.approx(
        (3.0**2 - 3.0) * 0.20**2 / 2.0
    )

    prices = prices_from_returns(np.tile(np.array([0.01, -0.01]), 30))
    disclosure = leverage_disclosure(
        traded_prices=prices,
        leverage=3.0,
        expense_ratio=0.01,
        financing_rate=0.05,
        validation_start=prices.index[0],
        validation_end=prices.index[-1],
    )
    expected_sigma = 0.01 * np.sqrt(252.0)
    assert disclosure.annualized_sigma == pytest.approx(expected_sigma)
    assert disclosure.variance_drag == pytest.approx(
        (3.0**2 - 3.0) * expected_sigma**2 / 2.0
    )


def test_overlap_fraction_compares_own_history_with_the_validation_window():
    prices = pd.Series(
        np.arange(100.0, 105.0),
        index=pd.date_range("2026-01-06", "2026-01-10", freq="D"),
    )

    measured = overlap_fraction(
        prices,
        validation_start="2026-01-01",
        validation_end="2026-01-10",
    )

    assert measured == pytest.approx(0.5)
