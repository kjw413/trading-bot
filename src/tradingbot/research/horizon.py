"""Pure statistical diagnostics for IC series at overlapping horizons."""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class HorizonDiagnostics:
    n_periods: int
    mean_ic: float
    sd_ic: float
    ir: float
    mean_sampling_variance: float
    true_sd: float
    ceiling: float
    signal_variance_exhausted: bool
    se_ir: float
    ci95: tuple[float, float]


def newey_west_se_mean(values: Sequence[float], lag: int) -> float:
    """Return the Newey-West standard error of the sample mean."""
    observations = tuple(float(value) for value in values)
    n = len(observations)
    if n == 0:
        raise ValueError("values must contain at least one observation")

    try:
        lag = operator.index(lag)
    except TypeError as exc:
        raise ValueError("lag must be an integer") from exc
    if lag < 0:
        raise ValueError("lag must be non-negative")
    if lag >= n:
        raise ValueError("lag must be less than the number of observations")

    mean = math.fsum(observations) / n
    centered = tuple(value - mean for value in observations)
    sum_squares = math.fsum(value * value for value in centered)
    if lag == 0:
        return math.sqrt(sum_squares / n**2)

    long_run_variance = sum_squares / n
    for j in range(1, lag + 1):
        autocovariance = math.fsum(
            centered[index] * centered[index - j]
            for index in range(j, n)
        ) / n
        bartlett_weight = 1.0 - j / (lag + 1)
        long_run_variance += 2.0 * bartlett_weight * autocovariance

    return math.sqrt(long_run_variance / n)


def diagnose(
    ics: Sequence[float],
    cross_section_sizes: Sequence[int],
    nw_lag: int,
) -> HorizonDiagnostics:
    """Summarize IC strength, sampling noise, and overlap-aware precision.

    NaN ICs and their paired cross-section sizes are dropped. A constant IC
    series has undefined IR and confidence bounds rather than zero values.
    """
    if len(ics) != len(cross_section_sizes):
        raise ValueError("ics and cross_section_sizes must have the same length")

    clean_pairs = tuple(
        (float(ic), size)
        for ic, size in zip(ics, cross_section_sizes, strict=True)
        if not math.isnan(float(ic))
    )
    n_periods = len(clean_pairs)
    if n_periods < 2:
        raise ValueError("at least two non-NaN IC observations are required")
    if any(size <= 1 for _, size in clean_pairs):
        raise ValueError("cross_section_sizes must be greater than 1")

    clean_ics = tuple(ic for ic, _ in clean_pairs)
    mean_ic = math.fsum(clean_ics) / n_periods
    if all(ic == clean_ics[0] for ic in clean_ics[1:]):
        observed_variance = 0.0
    else:
        squared_deviations = math.fsum(
            (ic - mean_ic) ** 2 for ic in clean_ics
        )
        observed_variance = squared_deviations / (n_periods - 1)
    sd_ic = math.sqrt(observed_variance)
    ir = mean_ic / sd_ic if sd_ic > 0.0 else float("nan")

    mean_sampling_variance = math.fsum(
        1.0 / (size - 1) for _, size in clean_pairs
    ) / n_periods
    true_variance = observed_variance - mean_sampling_variance
    signal_variance_exhausted = true_variance <= 0.0
    if signal_variance_exhausted:
        true_sd = 0.0
        ceiling = math.inf
    else:
        true_sd = math.sqrt(true_variance)
        ceiling = mean_ic / true_sd

    se_mean = newey_west_se_mean(clean_ics, nw_lag)
    if sd_ic > 0.0:
        se_ir = se_mean / sd_ic
        ci95 = (ir - 1.96 * se_ir, ir + 1.96 * se_ir)
    else:
        se_ir = float("nan")
        ci95 = (float("nan"), float("nan"))

    return HorizonDiagnostics(
        n_periods=n_periods,
        mean_ic=mean_ic,
        sd_ic=sd_ic,
        ir=ir,
        mean_sampling_variance=mean_sampling_variance,
        true_sd=true_sd,
        ceiling=ceiling,
        signal_variance_exhausted=signal_variance_exhausted,
        se_ir=se_ir,
        ci95=ci95,
    )
