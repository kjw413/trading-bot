"""Pick the names a theme portfolio should hold.

Selection is deliberately dumb: the intelligence lives in the combined
factor score. NaN scores are unscoreable names, not zeros — they are
excluded rather than ranked last, and ties break by symbol so the same
inputs always produce the same portfolio.
"""

from __future__ import annotations

import pandas as pd


def select_top(scores: pd.Series, top_n: int) -> list[str]:
    """Symbols of the `top_n` highest scores, deterministic under ties."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    clean = scores.dropna()
    if clean.empty:
        return []
    frame = clean.rename("score").rename_axis("symbol").reset_index()
    frame = frame.sort_values(["score", "symbol"], ascending=[False, True])
    return frame["symbol"].head(top_n).tolist()
