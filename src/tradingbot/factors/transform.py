"""Turn raw factor values into comparable, combinable scores.

Raw factors live on incompatible scales — a momentum return and an earnings
yield cannot be averaged directly. Standardizing each to a z-score after
clipping extremes makes them additive, and combining with renormalized
weights means a symbol missing one factor is scored on the ones it has
rather than being penalized for the gap.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_WINSOR_LIMIT = 0.02


def winsorize(values: pd.Series, limit: float = DEFAULT_WINSOR_LIMIT) -> pd.Series:
    """Clip the tails to their quantile boundary.

    A single mis-scaled value would otherwise set the whole cross-section's
    mean and standard deviation, flattening every real difference.
    """
    if not 0.0 <= limit < 0.5:
        raise ValueError("limit must be in [0, 0.5)")
    clean = values.dropna()
    if clean.empty or limit == 0.0:
        return values.copy()
    lower = clean.quantile(limit)
    upper = clean.quantile(1.0 - limit)
    return values.clip(lower=lower, upper=upper)


def zscore(values: pd.Series) -> pd.Series:
    """Center and scale to unit standard deviation.

    A constant cross-section scores all zeros rather than NaN: every name
    being equally attractive is information, not missing data.
    """
    result = values.copy()
    clean = values.dropna()
    if clean.empty:
        return result
    std = float(clean.std(ddof=0))
    if std == 0:
        result.loc[clean.index] = 0.0
        return result
    return (values - float(clean.mean())) / std


def standardize(values: pd.Series, *, limit: float = DEFAULT_WINSOR_LIMIT) -> pd.Series:
    """Winsorize then z-score — the standard pre-combination treatment."""
    return zscore(winsorize(values, limit))


def combine(
    scores: dict[str, pd.Series],
    weights: dict[str, float],
    *,
    min_factors: int = 1,
) -> pd.Series:
    """Weighted blend of standardized factor scores.

    Weights are renormalized per symbol over the factors that symbol actually
    has, so a missing factor neither counts as zero nor drops the symbol.
    Symbols scored by fewer than `min_factors` factors get NaN.
    """
    if not scores:
        return pd.Series(dtype=float)

    unknown = [name for name in weights if name not in scores]
    if unknown:
        available = ", ".join(sorted(scores))
        raise ValueError(f"weight given for unknown factor(s) {unknown}. Available: {available}")
    active = {name: float(weights.get(name, 0.0)) for name in scores}
    if sum(abs(w) for w in active.values()) == 0:
        raise ValueError("weights sum to zero")

    frame = pd.DataFrame(scores)
    weight_row = pd.Series(active)
    present = frame.notna()
    weighted_sum = (frame.fillna(0.0) * weight_row).sum(axis=1)
    weight_total = (present * weight_row).sum(axis=1)

    combined = weighted_sum / weight_total.replace(0.0, float("nan"))
    combined[present.sum(axis=1) < min_factors] = float("nan")
    return combined
