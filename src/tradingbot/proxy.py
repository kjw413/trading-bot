"""Measured proxy qualification and leveraged-product cost disclosure.

All price histories are injected by the caller.  This module performs no cache or
network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite, sqrt

import numpy as np
import pandas as pd


PROXY_WINDOW_DAYS = 252
VOLATILITY_WINDOW_DAYS = 60
TRADING_DAYS_PER_YEAR = 252
BETA_TOLERANCE = 0.10
MIN_R_SQUARED = 0.95


class ProxyStatus(str, Enum):
    QUALIFIED = "qualified"
    DISCRETIONARY_HOLDING = "discretionary_holding"


class CostState(Enum):
    """A non-numeric cost state that cannot be treated as zero."""

    UNKNOWN = "unknown"

    def __bool__(self) -> bool:
        raise TypeError(
            "Unknown cost has no truth value; handle CostState.UNKNOWN explicitly"
        )


AnnualCost = float | CostState
DateLike = str | date | datetime | pd.Timestamp


@dataclass(frozen=True)
class ProxyMeasurement:
    traded_symbol: str
    proxy_symbol: str
    leverage: float
    beta: float
    r_squared: float
    observations: int
    qualifies: bool

    @property
    def status(self) -> ProxyStatus:
        if self.qualifies:
            return ProxyStatus.QUALIFIED
        return ProxyStatus.DISCRETIONARY_HOLDING

    @property
    def report(self) -> str:
        measured = (
            f"beta={self.beta:.4f}; R²={self.r_squared:.4f}; "
            f"n={self.observations}"
        )
        pair = f"{self.traded_symbol}/{self.proxy_symbol}"
        if self.qualifies:
            return f"{pair}: qualifies ({measured})."
        return (
            f"{pair}: does not qualify ({measured}); no validated signal for this "
            "holding — discretionary holding."
        )


@dataclass(frozen=True)
class LeverageDisclosure:
    leverage: float
    annualized_sigma: float
    variance_drag: float
    annual_carry: AnnualCost
    expected_drag: AnnualCost
    overlap_fraction: float


def _finite_number(value: float, *, name: str) -> float:
    measured = float(value)
    if not isfinite(measured):
        raise ValueError(f"{name} must be finite")
    return measured


def _clean_prices(prices: pd.Series, *, name: str) -> pd.Series:
    if not isinstance(prices, pd.Series):
        raise TypeError(f"{name} must be a pandas Series")
    if prices.index.has_duplicates:
        raise ValueError(f"{name} index must not contain duplicates")
    try:
        numeric = pd.to_numeric(prices, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric prices") from exc
    numeric = numeric.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    if numeric.empty:
        raise ValueError(f"{name} has no finite prices")
    if bool((numeric <= 0.0).any()):
        raise ValueError(f"{name} must contain only positive prices")
    return numeric


def measure_proxy_pair(
    *,
    traded_symbol: str,
    proxy_symbol: str,
    traded_prices: pd.Series,
    proxy_prices: pd.Series,
    leverage: float,
    window: int = PROXY_WINDOW_DAYS,
) -> ProxyMeasurement:
    """Regress traded returns on proxy returns over the trailing window.

    The tested relationship is ``traded = alpha + beta * proxy``.  A leveraged
    pair therefore has an expected beta of ``leverage``.
    """
    if window < 2:
        raise ValueError("window must contain at least two returns")
    multiple = _finite_number(leverage, name="leverage")
    if multiple == 0.0:
        raise ValueError("leverage must not be zero")

    traded = _clean_prices(traded_prices, name="traded_prices")
    proxy = _clean_prices(proxy_prices, name="proxy_prices")
    aligned = pd.concat(
        (traded.rename("traded"), proxy.rename("proxy")),
        axis=1,
        join="inner",
    ).dropna()
    returns = (
        aligned.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .tail(window)
    )
    if len(returns) < window:
        raise ValueError(
            f"proxy measurement needs {window} aligned returns; got {len(returns)}"
        )

    x = returns["proxy"].to_numpy(dtype=float)
    y = returns["traded"].to_numpy(dtype=float)
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    proxy_sum_squares = float(np.dot(x_centered, x_centered))
    traded_sum_squares = float(np.dot(y_centered, y_centered))
    if proxy_sum_squares <= 0.0:
        raise ValueError("proxy returns have zero variance")
    if traded_sum_squares <= 0.0:
        raise ValueError("traded returns have zero variance")

    beta = float(np.dot(x_centered, y_centered) / proxy_sum_squares)
    intercept = float(y.mean() - beta * x.mean())
    residuals = y - (intercept + beta * x)
    residual_sum_squares = float(np.dot(residuals, residuals))
    r_squared = 1.0 - residual_sum_squares / traded_sum_squares
    r_squared = min(1.0, max(0.0, r_squared))

    beta_bounds = sorted(
        (multiple * (1.0 - BETA_TOLERANCE), multiple * (1.0 + BETA_TOLERANCE))
    )
    qualifies = (
        beta_bounds[0] <= beta <= beta_bounds[1]
        and r_squared > MIN_R_SQUARED
    )
    return ProxyMeasurement(
        traded_symbol=traded_symbol.strip().upper(),
        proxy_symbol=proxy_symbol.strip().upper(),
        leverage=multiple,
        beta=beta,
        r_squared=r_squared,
        observations=len(returns),
        qualifies=qualifies,
    )


def annualized_volatility(
    traded_prices: pd.Series,
    *,
    window: int = VOLATILITY_WINDOW_DAYS,
) -> float:
    """Population volatility of trailing daily returns, annualized by 252."""
    if window < 1:
        raise ValueError("window must be positive")
    prices = _clean_prices(traded_prices, name="traded_prices")
    returns = (
        prices.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .tail(window)
    )
    if len(returns) < window:
        raise ValueError(
            f"volatility measurement needs {window} returns; got {len(returns)}"
        )
    return float(returns.std(ddof=0) * sqrt(TRADING_DAYS_PER_YEAR))


def variance_drag(*, leverage: float, annualized_sigma: float) -> float:
    """Return ``(L² - L) * sigma² / 2`` in annual rate units."""
    multiple = _finite_number(leverage, name="leverage")
    sigma = _finite_number(annualized_sigma, name="annualized_sigma")
    if sigma < 0.0:
        raise ValueError("annualized_sigma must not be negative")
    return (multiple**2 - multiple) * sigma**2 / 2.0


def annual_carry(
    *,
    leverage: float,
    expense_ratio: float | None,
    financing_rate: float | None,
) -> AnnualCost:
    """Return expense ratio plus ``(L - 1) * financing_rate``, or UNKNOWN."""
    multiple = _finite_number(leverage, name="leverage")
    if expense_ratio is None or financing_rate is None:
        return CostState.UNKNOWN
    expense = _finite_number(expense_ratio, name="expense_ratio")
    financing = _finite_number(financing_rate, name="financing_rate")
    if expense < 0.0 or financing < 0.0:
        raise ValueError("expense_ratio and financing_rate must not be negative")
    return expense + (multiple - 1.0) * financing


def total_expected_drag(
    *, variance_component: float, carry: AnnualCost
) -> AnnualCost:
    """Add carry only when it is known; UNKNOWN propagates without arithmetic."""
    variance = _finite_number(variance_component, name="variance_component")
    if carry is CostState.UNKNOWN:
        return CostState.UNKNOWN
    return variance + _finite_number(carry, name="carry")


def _normalized_date(value: DateLike, *, name: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid date") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{name} must be a valid date")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def overlap_fraction(
    traded_prices: pd.Series,
    *,
    validation_start: DateLike,
    validation_end: DateLike,
) -> float:
    """Fraction of the inclusive validation date range covered by own history."""
    prices = _clean_prices(traded_prices, name="traded_prices")
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise TypeError("traded_prices must have a DatetimeIndex")
    history_index = prices.index
    if history_index.tz is not None:
        history_index = history_index.tz_localize(None)
    history_start = history_index.min().normalize()
    history_end = history_index.max().normalize()
    start = _normalized_date(validation_start, name="validation_start")
    end = _normalized_date(validation_end, name="validation_end")
    if end < start:
        raise ValueError("validation_end must not precede validation_start")

    overlap_start = max(start, history_start)
    overlap_end = min(end, history_end)
    if overlap_end < overlap_start:
        return 0.0
    validation_days = (end - start).days + 1
    overlap_days = (overlap_end - overlap_start).days + 1
    return overlap_days / validation_days


def leverage_disclosure(
    *,
    traded_prices: pd.Series,
    leverage: float,
    expense_ratio: float | None,
    financing_rate: float | None,
    validation_start: DateLike,
    validation_end: DateLike,
    volatility_window: int = VOLATILITY_WINDOW_DAYS,
) -> LeverageDisclosure:
    """Compute the mandatory disclosure from caller-supplied price history."""
    multiple = _finite_number(leverage, name="leverage")
    sigma = annualized_volatility(traded_prices, window=volatility_window)
    variance_component = variance_drag(
        leverage=multiple, annualized_sigma=sigma
    )
    carry = annual_carry(
        leverage=multiple,
        expense_ratio=expense_ratio,
        financing_rate=financing_rate,
    )
    return LeverageDisclosure(
        leverage=multiple,
        annualized_sigma=sigma,
        variance_drag=variance_component,
        annual_carry=carry,
        expected_drag=total_expected_drag(
            variance_component=variance_component, carry=carry
        ),
        overlap_fraction=overlap_fraction(
            traded_prices,
            validation_start=validation_start,
            validation_end=validation_end,
        ),
    )
