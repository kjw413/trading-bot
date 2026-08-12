"""Reduce exposure to names that are about to report.

One rule: a name whose next expected announcement falls inside the window is
scaled down, and the freed weight becomes cash. It is never handed to the
other names — redistributing would relocate the exposure this overlay exists
to remove, the same reason `apply_constraints` sends capped excess to cash.

This only ever reduces, so it cannot break a concentration cap or a cash
buffer that already held. That is what makes it safe to apply after
`apply_constraints` rather than before.
"""

from __future__ import annotations


def reduce_for_events(
    weights: dict[str, float],
    *,
    days_to_event: dict[str, int | None],
    window_days: int,
    scale: float,
) -> dict[str, float]:
    """Scale down weights for names reporting within `window_days`.

    A symbol whose schedule is unknown (None, or absent from the mapping) is
    left alone. Unknown is not the same as "no event coming", and guessing
    would trade on nothing.

    A negative `window_days` disables the overlay entirely, matching how
    `abs_momentum_ma_days = 0` disables its filter.
    """
    if not 0.0 <= scale <= 1.0:
        raise ValueError("scale must be in [0, 1]")
    if window_days < 0 or not weights:
        return dict(weights)

    adjusted = {}
    for symbol, weight in weights.items():
        days = days_to_event.get(symbol)
        if days is not None and 0 <= days <= window_days:
            adjusted[symbol] = weight * scale
        else:
            adjusted[symbol] = weight
    return adjusted
