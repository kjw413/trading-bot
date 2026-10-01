from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from tradingbot.research.horizon import diagnose, newey_west_se_mean


def test_newey_west_lag_zero_matches_standard_error_identity():
    values = [1.0, 2.0, 4.0, 8.0]
    mean = sum(values) / len(values)
    expected = math.sqrt(
        sum((value - mean) ** 2 for value in values) / len(values) ** 2
    )

    assert newey_west_se_mean(values, lag=0) == expected


def test_positive_autocorrelation_increases_newey_west_standard_error():
    values = [1.0, 2.0, 3.0, 4.0]

    assert newey_west_se_mean(values, lag=1) > newey_west_se_mean(
        values, lag=0
    )


@pytest.mark.parametrize(
    ("values", "lag", "message"),
    [
        ([], 0, "at least one observation"),
        ([1.0, 2.0], 0.5, "must be an integer"),
        ([1.0, 2.0], -1, "must be non-negative"),
        ([1.0, 2.0], 2, "less than the number of observations"),
    ],
)
def test_newey_west_rejects_invalid_inputs(values, lag, message):
    with pytest.raises(ValueError, match=message):
        newey_west_se_mean(values, lag)


def test_diagnose_returns_hand_computable_statistics():
    result = diagnose([-1.0, 0.0, 1.0], [5, 5, 5], nw_lag=0)
    expected_se_ir = math.sqrt(2.0) / 3.0

    assert result.n_periods == 3
    assert result.mean_ic == 0.0
    assert result.sd_ic == 1.0
    assert result.ir == 0.0
    assert result.mean_sampling_variance == 0.25
    assert result.true_sd == pytest.approx(math.sqrt(0.75))
    assert result.ceiling == 0.0
    assert result.signal_variance_exhausted is False
    assert result.se_ir == pytest.approx(expected_se_ir)
    assert result.ci95 == pytest.approx(
        (-1.96 * expected_se_ir, 1.96 * expected_se_ir)
    )


def test_diagnose_drops_nan_ic_with_its_paired_cross_section_size():
    result = diagnose([-0.8, math.nan, 0.8], [3, 1, 5], nw_lag=0)

    assert result.n_periods == 2
    assert result.mean_sampling_variance == pytest.approx(0.375)
    assert result.true_sd == pytest.approx(math.sqrt(0.905))


def test_diagnose_flags_exhausted_signal_variance():
    result = diagnose([0.1, 0.2, 0.3], [2, 2, 2], nw_lag=0)

    assert result.true_sd == 0.0
    assert result.ceiling == math.inf
    assert result.signal_variance_exhausted is True


def test_diagnose_marks_constant_series_ratios_as_undefined():
    result = diagnose([0.2, 0.2, 0.2], [5, 5, 5], nw_lag=0)

    assert math.isnan(result.ir)
    assert math.isnan(result.se_ir)
    assert all(math.isnan(bound) for bound in result.ci95)


@pytest.mark.parametrize(
    ("ics", "sizes", "nw_lag", "message"),
    [
        ([0.1], [2, 3], 0, "must have the same length"),
        ([math.nan, 0.1], [2, 2], 0, "at least two non-NaN"),
        ([0.1, 0.2], [1, 2], 0, "must be greater than 1"),
        (
            [0.1, 0.2],
            [2, 2],
            2,
            "less than the number of observations",
        ),
    ],
)
def test_diagnose_rejects_invalid_inputs(ics, sizes, nw_lag, message):
    with pytest.raises(ValueError, match=message):
        diagnose(ics, sizes, nw_lag)


def test_horizon_diagnostics_is_frozen():
    result = diagnose([-1.0, 0.0, 1.0], [5, 5, 5], nw_lag=0)

    with pytest.raises(FrozenInstanceError):
        result.n_periods = 4
