from __future__ import annotations

from dataclasses import replace

from tradingbot.valuation.dcf import DcfInputs, dcf_value


def implied_growth(
    current_price: float,
    base_inputs: DcfInputs,
    bracket: tuple[float, float] = (-0.5, 0.5),
    tol: float = 1e-6,
    max_iter: int = 200,
) -> float:
    """Growth rate the market price implies, holding all other inputs fixed.

    Reverse DCF (framework §6): instead of assuming growth and reading a value,
    solve dcf_value(growth).value_per_share - current_price = 0 for growth by
    bisection. Per-share value is monotonically increasing in growth, so a sign change
    across the bracket guarantees a unique root. A price unreachable within the
    bracket raises rather than returning a bound (confirmation-bias guard).
    """
    low, high = bracket
    if low >= high:
        raise ValueError("bracket must be (low, high) with low < high")

    def gap(growth: float) -> float:
        return dcf_value(replace(base_inputs, growth=growth)).value_per_share - current_price

    gap_low, gap_high = gap(low), gap(high)
    if gap_low == 0:
        return low
    if gap_high == 0:
        return high
    if (gap_low > 0) == (gap_high > 0):
        raise ValueError(
            f"current_price {current_price} is not reachable within growth bracket {bracket}"
        )

    for _ in range(max_iter):
        mid = (low + high) / 2.0
        gap_mid = gap(mid)
        if abs(gap_mid) < tol or (high - low) / 2.0 < tol:
            return mid
        if (gap_mid > 0) == (gap_low > 0):
            low, gap_low = mid, gap_mid
        else:
            high = mid
    return (low + high) / 2.0
